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

if TYPE_CHECKING:  # pragma: no cover
    import fitz


_ANNOTATION_SUBJECT_PREFIX = "cm:"
_TOTAL_SCORE_SUBJECT = "og:total-score"
_ANNOTATION_HASH_KEY = b"opengrader-pdf-comment-v1"

_SUMMARY_SUBJECT = "og:feedback-summary"
_SUMMARY_HASH_KEY = b"opengrader-pdf-summary-v1"

# Bump when summary layout changes so unchanged feedback still rerenders cached PDFs.
_SUMMARY_LAYOUT_VERSION = "1"

_SUMMARY_PAGE_WIDTH = 612.0
_SUMMARY_PAGE_HEIGHT = 792.0
_SUMMARY_MARGIN = 54.0

_REGULAR = False
_BOLD = True


def sync_total_score_annotation(
    *,
    source_pdf_path: Path,
    annotated_pdf_path: Path,
    total_points: float,
    possible_points: float,
) -> bool:
    import fitz

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

        if len(document) > 0:
            page = document[0]
            rect = fitz.Rect(page.rect.width - 160, 20, page.rect.width - 10, 50)
            annot = page.add_freetext_annot(
                rect,
                score_text,
                fontsize=14,
                text_color=(0.8, 0, 0),
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
    import fitz

    if not source_pdf_path.exists():
        raise FileNotFoundError(f"source PDF not found: {source_pdf_path}")
    if not source_segments:
        raise ValueError(
            "Question source metadata missing; regenerate submission files to enable PDF annotations."
        )

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
    import fitz

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
    title: str
    student_label: str
    total_awarded: float
    total_possible: float
    questions: tuple[FeedbackQuestionSummary, ...] = field(default_factory=tuple)
    curved_total: float | None = None


def _summary_digest(summary: FeedbackSummary) -> str:
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
    """Pass force after incremental annotation writes so the full save compacts prior revisions.
    Returns whether the PDF was written.
    """
    import fitz

    if not source_pdf_path.exists():
        raise FileNotFoundError(f"source PDF not found: {source_pdf_path}")

    annotated_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if not annotated_pdf_path.exists():
        shutil.copy2(source_pdf_path, annotated_pdf_path)

    digest = _summary_digest(summary)

    # Page changes require a full save, which also compacts incremental annotation writes.
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
    import fitz

    page = document.new_page(
        -1, width=_SUMMARY_PAGE_WIDTH, height=_SUMMARY_PAGE_HEIGHT
    )
    annot = page.add_text_annot(
        fitz.Point(4, 4), "Opengrader grade summary", icon="Note"
    )
    annot.set_info(subject=_SUMMARY_SUBJECT, content=digest)
    hidden_flag = getattr(fitz, "PDF_ANNOT_IS_HIDDEN", 2)
    annot.set_flags(hidden_flag)
    annot.update()
    return page


def _wrap_text(
    drawable: DrawableText, fontsize: float, max_width: float
) -> list[DrawableText]:
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


_SummaryLine = tuple[DrawableText, float, tuple[float, float, float]]


def _question_block_lines(
    question: FeedbackQuestionSummary, fonts: TextFonts, max_width: float
) -> list[_SummaryLine]:
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
    fontfile = fonts.fontfile_for(drawable.fontname)
    if fontfile is None:
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
