from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from opengrader.core.pdf.text_fonts import DrawableText, TextFonts, load_text_fonts
from opengrader.core.source_location import QuestionSourceSegment

if TYPE_CHECKING:  # pragma: no cover - typing only
    # fitz (PyMuPDF, ~110ms cold) is imported lazily at each call site below
    # instead — this module is pulled in just to *define* the summary/comment
    # sync functions (e.g. by the grade-screen data provider at startup), and
    # none of them run until a grader actually saves a comment or score.
    import fitz


_ANNOTATION_SUBJECT_PREFIX = "cm:"
_TOTAL_SCORE_SUBJECT = "og:total-score"
_ANNOTATION_HASH_KEY = b"opengrader-pdf-comment-v1"

# Marker carried by every generated "Grade Summary" page so a resync can find
# and delete the previous ones before appending fresh pages (idempotent, like
# the tagged comment/total annotations above). The marker annotation is hidden,
# so it never renders — it only identifies the page.
#
# Its *content* is a digest of the `FeedbackSummary` the page was drawn from, so
# a resync can tell "this page is already exactly what I would draw" from "this
# page is stale" without rendering anything. That is what keeps the whole
# summary rewrite — a full document save, the most expensive thing on the grade
# path — off a save that changed nothing the page shows.
_SUMMARY_SUBJECT = "og:feedback-summary"
_SUMMARY_HASH_KEY = b"opengrader-pdf-summary-v1"

# Bumped whenever `_draw_summary_pages`/`_question_block_lines` change what they
# draw from the same `FeedbackSummary`. Without it, a layout fix would leave
# every existing annotated PDF matching its stored digest and silently never
# re-rendering — the one way a content hash can serve a stale page.
_SUMMARY_LAYOUT_VERSION = "1"

# US-Letter, matching the template generator's page metrics.
_SUMMARY_PAGE_WIDTH = 612.0
_SUMMARY_PAGE_HEIGHT = 792.0
_SUMMARY_MARGIN = 54.0

# The summary page prints the student's real name, so it cannot use the built-in
# base-14 fonts: those encode text as WinAnsi and silently replace every
# character outside that 256-character set with a dot — mangling exactly the
# names (Cyrillic, Han, Vietnamese tone marks) on the one page the student is
# handed back. `core/pdf/text_fonts.py` picks a font that can draw the text.
# Bold is requested as a flag rather than a font name because the font is only
# known once the text is in hand.
_REGULAR = False
_BOLD = True


def sync_total_score_annotation(
    *,
    source_pdf_path: Path,
    annotated_pdf_path: Path,
    total_points: float,
    possible_points: float,
) -> bool:
    """Synchronize the total score summary onto the first page of the PDF.

    Returns whether the file was written. ``saveIncr`` *appends a revision*
    rather than rewriting, so an unchanged total that still saved would grow the
    returned scan for nothing — and, worse, grow it invisibly to
    :func:`sync_feedback_summary_page`, which is the only compaction point.
    """
    import fitz  # see the TYPE_CHECKING import above for why this is deferred

    if not source_pdf_path.exists():
        raise FileNotFoundError(f"source PDF not found: {source_pdf_path}")

    score_text = f"Total Score: {total_points:g} / {possible_points:g}"

    annotated_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if not annotated_pdf_path.exists():
        shutil.copy2(source_pdf_path, annotated_pdf_path)

    with fitz.open(annotated_pdf_path) as document:
        if _annotation_placements(document, subject=_TOTAL_SCORE_SUBJECT) == [
            (0, score_text)
        ]:
            return False
        _remove_existing_question_annotations(document, subject=_TOTAL_SCORE_SUBJECT)

        # Always add the total score to the first page (index 0)
        if len(document) > 0:
            page = document[0]
            # Position the annotation at the top right of the page
            # page.rect.width - 150 provides a consistent right-aligned feel.
            rect = fitz.Rect(page.rect.width - 160, 20, page.rect.width - 10, 50)
            annot = page.add_freetext_annot(
                rect,
                score_text,
                fontsize=14,
                text_color=(0.8, 0, 0),  # Dark Red
            )
            annot.set_info(subject=_TOTAL_SCORE_SUBJECT, content=score_text)
            annot.update()

        document.saveIncr()
    return True


def sync_question_comment_annotation(
    *,
    source_pdf_path: Path,
    annotated_pdf_path: Path,
    question_id: str,
    comment: str,
    points_awarded: float | None = None,
    possible_points: float | None = None,
    source_segments: tuple[QuestionSourceSegment, ...],
) -> bool:
    """Synchronize one question comment into an annotated PDF copy.

    Returns whether the file was written — see
    :func:`sync_total_score_annotation` for why an unchanged note must not save.
    """
    import fitz  # see sync_total_score_annotation for why this is deferred

    if not source_pdf_path.exists():
        raise FileNotFoundError(f"source PDF not found: {source_pdf_path}")
    if not source_segments:
        raise ValueError(
            "Question source metadata missing; regenerate submission files to enable PDF annotations."
        )

    # Build the annotation text: Score followed by comment if present
    text_parts = []
    if points_awarded is not None:
        score_str = f"Score: {points_awarded:g}"
        if possible_points is not None:
            score_str += f"/{possible_points:g}"
        text_parts.append(score_str)

    if comment.strip():
        text_parts.append(comment.strip())

    full_text = "\n\n".join(text_parts)

    if not full_text and not annotated_pdf_path.exists():
        return False

    annotated_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if not annotated_pdf_path.exists():
        shutil.copy2(source_pdf_path, annotated_pdf_path)

    subject = _comment_subject(question_id)
    # What this note would be after the write: one annotation on the answer's
    # page carrying this text, or none at all when the grader cleared it. The
    # page is compared as well as the text because a re-extraction can move an
    # answer to a different page without changing a word of the feedback.
    expected = [(source_segments[0].page_number - 1, full_text)] if full_text else []
    with fitz.open(annotated_pdf_path) as document:
        if _annotation_placements(document, subject=subject) == expected:
            return False
        _remove_existing_question_annotations(document, subject=subject)
        if full_text:
            _add_comment_annotation(
                document,
                segment=source_segments[0],
                subject=subject,
                comment=full_text,
            )
        document.saveIncr()
    return True


def _comment_subject(question_id: str) -> str:
    """Return the stable opaque PDF subject used to identify one question note.

    The subject must be deterministic so repeated comment saves replace the
    prior annotation instead of adding duplicates. It must also avoid readable
    identifiers because students may inspect annotation metadata in the PDF.
    """
    digest = hashlib.blake2s(
        question_id.encode("utf-8"),
        digest_size=16,
        key=_ANNOTATION_HASH_KEY,
        person=b"pdfcmt1",
    ).hexdigest()
    return f"{_ANNOTATION_SUBJECT_PREFIX}{digest}"


def _annotation_placements(
    document: fitz.Document,
    *,
    subject: str,
) -> list[tuple[int, str]]:
    """Return ``(page index, content)`` for every annotation tagged ``subject``.

    The comparable identity of a tagged annotation: which page it sits on and
    what it says. Everything else about it (the icon, the exact point, the
    object number) is either fixed by this module or regenerated on write, so
    two syncs producing the same list would produce the same document.
    """
    placements: list[tuple[int, str]] = []
    for index in range(len(document)):
        page = document[index]
        annot = page.first_annot
        while annot is not None:
            info = annot.info or {}
            if info.get("subject") == subject:
                placements.append((index, info.get("content") or ""))
            annot = annot.next
    return placements


def _remove_existing_question_annotations(
    document: fitz.Document,
    *,
    subject: str,
) -> None:
    """Delete the current opaque annotation for a question before rewriting it."""
    for page in document:
        annot = page.first_annot
        while annot is not None:
            next_annot = annot.next
            info = annot.info or {}
            if info.get("subject") == subject:
                page.delete_annot(annot)
            annot = next_annot


def _add_comment_annotation(
    document: fitz.Document,
    *,
    segment: QuestionSourceSegment,
    subject: str,
    comment: str,
) -> None:
    import fitz  # see sync_total_score_annotation for why this is deferred

    page_index = segment.page_number - 1
    if page_index < 0 or page_index >= len(document):
        raise ValueError(f"source segment page out of range: {segment.page_number}")

    page = document[page_index]
    page_rect = page.rect
    x_norm, y_norm, _w_norm, _h_norm = segment.bbox_norm
    x0 = x_norm * page_rect.width
    y0 = y_norm * page_rect.height
    x = max(12.0, x0 - 28.0)
    y = min(max(12.0, y0 + 4.0), page_rect.height - 24.0)

    annot = page.add_text_annot(fitz.Point(x, y), comment, icon="Comment")
    annot.set_info(
        subject=subject,
        content=comment,
    )
    annot.update()


@dataclass(frozen=True)
class FeedbackQuestionSummary:
    """One question's line on the returnable Grade Summary page."""

    label: str
    possible_points: float | None
    points_awarded: float | None
    rubric_labels: tuple[str, ...] = ()
    comment: str = ""

    @property
    def is_graded(self) -> bool:
        return self.points_awarded is not None


@dataclass(frozen=True)
class FeedbackSummary:
    """Everything the Grade Summary page renders for one student."""

    title: str
    student_label: str
    total_awarded: float
    total_possible: float
    questions: tuple[FeedbackQuestionSummary, ...] = field(default_factory=tuple)
    # The curved total when an assignment curve is active (`core/curve.py`);
    # None means no curve. Shown as the headline with the raw total beneath, so
    # the curve stays transparent.
    curved_total: float | None = None


def _summary_digest(summary: FeedbackSummary) -> str:
    """Fingerprint exactly what the summary pages would draw.

    Numbers go in through the same ``%g``/:func:`_number` formatting the page
    itself uses, so the digest tracks the *rendered* text rather than the float
    behind it — ``8`` and ``8.0`` draw the same page and must not force a
    rewrite. Keyed like :func:`_comment_subject`, and for the same reason: it
    lands in annotation metadata a student can read out of the returned PDF, so
    it must reveal nothing about the grades it summarizes.
    """
    payload = json.dumps(
        {
            "layout": _SUMMARY_LAYOUT_VERSION,
            "title": summary.title,
            "student": summary.student_label,
            "awarded": f"{summary.total_awarded:g}",
            "possible": f"{summary.total_possible:g}",
            "curved": (
                None
                if summary.curved_total is None
                else f"{summary.curved_total:g}"
            ),
            "questions": [
                {
                    "label": question.label,
                    "possible": _number(question.possible_points),
                    "awarded": (
                        None
                        if question.points_awarded is None
                        else _number(question.points_awarded)
                    ),
                    "rubric": list(question.rubric_labels),
                    "comment": question.comment.strip(),
                }
                for question in summary.questions
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.blake2s(
        payload.encode("utf-8"),
        digest_size=16,
        key=_SUMMARY_HASH_KEY,
        person=b"pdfsum1",
    ).hexdigest()


def _current_summary_digest(document: fitz.Document) -> str | None:
    """Return the digest the document's summary pages were drawn from.

    ``None`` when there is no summary yet, when a page carries no digest (a PDF
    written before this marker gained one — it re-renders once and is then
    up to date), or when the marked pages disagree, which can only mean a
    half-written document that is safest to redraw.
    """
    digests = {content for (_index, content) in _annotation_placements(
        document, subject=_SUMMARY_SUBJECT
    )}
    if len(digests) != 1:
        return None
    digest = digests.pop()
    return digest or None


def sync_feedback_summary_page(
    *,
    source_pdf_path: Path,
    annotated_pdf_path: Path,
    summary: FeedbackSummary,
    force: bool = False,
) -> bool:
    """Append (or refresh) a Grade Summary page at the end of the annotated PDF.

    The summary is a legible, always-visible breakdown — per-question points,
    rubric selections, and comments, plus the total — so the annotated scan can
    be handed back as-is instead of relying on the collapsed sticky-note icons.
    Ungraded questions are listed explicitly, so the page stays coherent while a
    student is only partially graded.

    Idempotent: any previously generated summary pages (identified by a hidden
    marker annotation) are removed first, so calling this on every save keeps
    exactly one up-to-date summary at the back of the document.

    **Nothing is written when the page is already current.** The marker carries a
    digest of the `FeedbackSummary` it was drawn from; when that matches what
    this call would draw, the whole rewrite is skipped and the function returns
    ``False``. This is the hot path: grading walks question by question and every
    save re-synced the PDF, so the common case was rendering a page identical to
    the one already there and rewriting the entire document to store it.

    ``force`` runs the rewrite even on a matching digest, and callers must pass
    it when they have just written an annotation. That is not politeness — it is
    what keeps the compaction invariant true. The two annotation writers use
    ``saveIncr``, which appends a revision, and the full save below is the only
    thing in the codebase that discards those revisions. If a caller appends a
    revision and this function then skips, the file grows with nothing to
    reclaim it. Returning "did I write?" from those two functions and passing it
    here makes that structural rather than something the caller has to reason
    about. Returns whether the file was written.
    """
    import fitz  # see sync_total_score_annotation for why this is deferred

    if not source_pdf_path.exists():
        raise FileNotFoundError(f"source PDF not found: {source_pdf_path}")

    annotated_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if not annotated_pdf_path.exists():
        shutil.copy2(source_pdf_path, annotated_pdf_path)

    digest = _summary_digest(summary)

    # A page insert/delete is a structural change, so it can't ride the
    # incremental save the comment/total annotations use — write a full copy to
    # a sibling temp file and atomically swap it in.
    #
    # **`garbage`/`deflate` are load-bearing, not tuning.** The two annotation
    # writes above use `saveIncr`, which *appends a revision* rather than
    # rewriting — cheap and correct in isolation, but the old revision stays in
    # the file. This full save is the only point that can discard them, and
    # without `garbage` it faithfully copied every one forward: measured, the
    # annotated PDF grew ~1.25 MB on **every** re-sync, unbounded, even when
    # nothing had changed. Grading one student's 11 questions once each, typing
    # nothing, produced a 14 MB scan that should be 1.1 MB — on the artifact
    # handed back to the student and packed into `return-bundle`, so a
    # 200-student class shipped gigabytes of dead revisions. With compaction the
    # file stays flat at 1.1 MB across any number of syncs, and each save gets
    # *faster* because it is no longer rewriting the accumulated bulk.
    #
    # It is safe on the two things this file's idempotency depends on: garbage
    # collection only drops *unreferenced* objects, so the hidden
    # `og:feedback-summary` marker and the `cm:`/`og:total-score` annotations —
    # all live objects on live pages — survive, and `_remove_summary_pages`
    # still finds them on the next pass.
    tmp_path = annotated_pdf_path.with_name(annotated_pdf_path.name + ".summary.tmp")
    with fitz.open(annotated_pdf_path) as document:
        if not force and _current_summary_digest(document) == digest:
            return False
        _remove_summary_pages(document)
        _draw_summary_pages(document, summary, digest)
        document.save(tmp_path, garbage=4, deflate=True)
    os.replace(tmp_path, annotated_pdf_path)
    return True


def _remove_summary_pages(document: fitz.Document) -> None:
    """Delete every previously generated summary page (by its hidden marker)."""
    marked: list[int] = []
    for index in range(len(document)):
        page = document[index]
        annot = page.first_annot
        while annot is not None:
            if (annot.info or {}).get("subject") == _SUMMARY_SUBJECT:
                marked.append(index)
                break
            annot = annot.next
    for index in reversed(marked):
        document.delete_page(index)


def _append_marked_summary_page(document: fitz.Document, digest: str) -> fitz.Page:
    import fitz  # see sync_total_score_annotation for why this is deferred

    page = document.new_page(
        -1, width=_SUMMARY_PAGE_WIDTH, height=_SUMMARY_PAGE_HEIGHT
    )
    annot = page.add_text_annot(
        fitz.Point(4, 4), "Opengrader grade summary", icon="Note"
    )
    # Every page of a paginated summary carries the same digest: the fingerprint
    # is of the whole `FeedbackSummary`, so the pages are current together or
    # stale together.
    annot.set_info(subject=_SUMMARY_SUBJECT, content=digest)
    hidden_flag = getattr(fitz, "PDF_ANNOT_IS_HIDDEN", 2)
    annot.set_flags(hidden_flag)
    annot.update()
    return page


def _wrap_text(
    drawable: DrawableText, fontsize: float, max_width: float
) -> list[DrawableText]:
    """Break one already-font-resolved run of text into lines that fit the page.

    The font is chosen for the whole run *before* wrapping, so every line comes
    back carrying the same font — which is what keeps the width measurement used
    here and the drawing done later in agreement.
    """
    words = drawable.text.split()
    if not words:
        return [replace(drawable, text="")]

    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if replace(drawable, text=trial).width(fontsize) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return [replace(drawable, text=line) for line in lines]


def _number(value: float | None) -> str:
    return f"{value:g}" if value is not None else "?"


# One laid-out line of the summary: the text (with its resolved font), the size
# it is set at, and its colour.
_SummaryLine = tuple[DrawableText, float, tuple[float, float, float]]


def _question_block_lines(
    question: FeedbackQuestionSummary, fonts: TextFonts, max_width: float
) -> list[_SummaryLine]:
    """Render one question to wrapped, font-resolved lines ready for layout."""
    lines: list[_SummaryLine] = []
    if question.is_graded:
        head = (
            f"{question.label}: {_number(question.points_awarded)}"
            f" / {_number(question.possible_points)}"
        )
    else:
        head = f"{question.label}: not yet graded (out of {_number(question.possible_points)})"
    for line in _wrap_text(fonts.choose(head, bold=_BOLD), 11, max_width):
        lines.append((line, 11, (0.0, 0.0, 0.0)))

    if question.rubric_labels:
        rubric = "Rubric: " + ", ".join(question.rubric_labels)
        for line in _wrap_text(fonts.choose(rubric, bold=_REGULAR), 10, max_width):
            lines.append((line, 10, (0.25, 0.25, 0.25)))

    comment = question.comment.strip()
    if comment:
        wrapped = _wrap_text(
            fonts.choose("Comment: " + comment, bold=_REGULAR), 10, max_width
        )
        for line in wrapped:
            lines.append((line, 10, (0.15, 0.15, 0.15)))
    return lines


def _register_font(page: fitz.Page, drawable: DrawableText, fonts: TextFonts) -> None:
    """Make one font usable on this page before drawing with it.

    An embedded TTF has to be declared on every page that uses it. Only the fonts
    actually chosen get registered, so a page of plain ASCII never carries the
    CJK font it did not need.
    """
    fontfile = fonts.fontfile_for(drawable.fontname)
    if fontfile is None:
        # A PyMuPDF built-in (the CJK fonts, and the base-14 fallbacks used when
        # the bundled TTFs are missing): named, not embedded from a file.
        page.insert_font(fontname=drawable.fontname)
    else:
        page.insert_font(fontname=drawable.fontname, fontfile=fontfile)


def _draw_summary_pages(
    document: fitz.Document, summary: FeedbackSummary, digest: str
) -> None:
    max_width = _SUMMARY_PAGE_WIDTH - 2 * _SUMMARY_MARGIN
    bottom = _SUMMARY_PAGE_HEIGHT - _SUMMARY_MARGIN
    fonts = load_text_fonts()

    page = _append_marked_summary_page(document, digest)
    y = _SUMMARY_MARGIN

    def write(
        drawable: DrawableText, fontsize: float, color: tuple[float, float, float]
    ) -> None:
        nonlocal y
        _register_font(page, drawable, fonts)
        page.insert_text(
            (_SUMMARY_MARGIN, y + fontsize),
            drawable.text,
            fontname=drawable.fontname,
            fontsize=fontsize,
            color=color,
        )
        y += fontsize * 1.4

    write(fonts.choose("Grade Summary", bold=_BOLD), 18, (0.0, 0.0, 0.0))
    y += 4
    # The title and the student label are the two strings that can contain
    # anything — an assignment name typed by the instructor and a name off the
    # roster — so both go through the font chooser like every other run.
    write(fonts.choose(summary.title), 12, (0.3, 0.3, 0.3))
    write(fonts.choose(summary.student_label), 11, (0.0, 0.0, 0.0))
    if summary.curved_total is not None:
        write(
            fonts.choose(
                f"Total (curved): {summary.curved_total:g} / {summary.total_possible:g}",
                bold=_BOLD,
            ),
            13,
            (0.8, 0.0, 0.0),
        )
        write(
            fonts.choose(
                f"Raw total: {summary.total_awarded:g} / {summary.total_possible:g}"
            ),
            10,
            (0.35, 0.35, 0.35),
        )
    else:
        write(
            fonts.choose(
                f"Total: {summary.total_awarded:g} / {summary.total_possible:g}",
                bold=_BOLD,
            ),
            13,
            (0.8, 0.0, 0.0),
        )
    y += 6
    page.draw_line(
        (_SUMMARY_MARGIN, y),
        (_SUMMARY_PAGE_WIDTH - _SUMMARY_MARGIN, y),
        color=(0.7, 0.7, 0.7),
        width=0.8,
    )
    y += 12

    for question in summary.questions:
        block = _question_block_lines(question, fonts, max_width)
        needed = sum(size * 1.4 for (_drawable, size, _color) in block) + 8
        if y + needed > bottom:
            page = _append_marked_summary_page(document, digest)
            y = _SUMMARY_MARGIN
        for drawable, fontsize, color in block:
            write(drawable, fontsize, color)
        y += 8
