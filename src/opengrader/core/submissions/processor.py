from __future__ import annotations

import json
import os
import re
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from difflib import SequenceMatcher
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal, Mapping

import numpy as np
from PIL import Image

from opengrader.core.source_location import QuestionSourceSegment
from opengrader.core.roster import load_roster_students
from opengrader.core.pdf_utils import (
    POINTS_SPEC,
    QUESTION_PREFIX_RE,
    SCAN_QUESTION_LINE_RE,
    SCAN_QUESTION_MAX_X_NORM,
    calculate_page_offsets,
    crop_norm,
    render_page_to_image,
    safe_image_to_data,
    norm_bbox,
)
from .student_file import (
    IdentityCandidate,
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    render_student_submission_markdown,
)
# The structural shape this module consumes lives in its own module because it
# has more than one producer: the text-layer reader below, and the hand-drawn
# region maps in `core/region_map.py`. See `template.py` for why that split is
# load-bearing rather than cosmetic.
from .template import AssignmentTemplate, FieldLocation
from .extra_pages import read_extra_pages_for
from .page_context import infer_extra_pages
from .transcription import (
    SOURCE_MATH_MODEL,
    SOURCE_TESSERACT,
    SOURCE_TEXT_LAYER,
    SOURCE_VISION_MODEL,
    should_escalate,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from opengrader.core.bubble_sheet import BubbleSheetLayout
    # process_submission_pdf is imported locally at its one call site (in
    # _build_document_from_ocr_result) instead — opengrader.core.ocr.extractor
    # imports cv2 and fitz at module scope (~230ms combined cold), and this
    # whole module is loaded eagerly by core/submissions/__init__.py, so an
    # eager import here would tax the TUI's startup path for a name only the
    # OCR extraction strategy calls.
    from opengrader.core.ocr.extractor import process_submission_pdf
    from opengrader.core.ocr import OcrConfig
from opengrader.core.ocr.math_ocr import (
    DEFAULT_MATH_BACKEND,
    MathTranscriber,
    resolve_math_transcriber,
)
from opengrader.core.ocr.ollama_vlm import (
    OllamaVisionConfig,
    # ensure_model_available, # This is not used in the processor, but in the CLI
    extract_field_text,
    extract_question_prompt_and_answer,
)


# ==============================================================================
# PROGRESS REPORTING
# ==============================================================================


@dataclass
class ExtractionProgress:
    """UI-agnostic progress reporter for the extraction pipeline.

    The per-PDF loop lives in the caller (the CLI or the setup-screen worker),
    so aggregate totals/ETA are the caller's job. Each build call reports how
    many responses it is about to transcribe for its PDF (``file_start``) and
    then ticks once per completed response (``response``). Callbacks are
    optional; a bare ``ExtractionProgress()`` is a no-op, so core code can call
    the methods unconditionally.
    """

    on_file_start: Callable[[str, int], None] | None = None
    on_response: Callable[[str, str], None] | None = None

    def file_start(self, pdf_name: str, response_count: int) -> None:
        if self.on_file_start is not None:
            self.on_file_start(pdf_name, response_count)

    def response(self, pdf_name: str, question_id: str) -> None:
        if self.on_response is not None:
            self.on_response(pdf_name, question_id)


# ==============================================================================
# CONFIGURATION & REGEX PATTERNS
# ==============================================================================

# Patterns applied to fitz-extracted text from a PDF text layer (blank assignment
# or text-layer student PDF).
_QUESTION_INLINE_RE = re.compile(
    r"(\d+)\.\s*" + POINTS_SPEC + r"\s*(.+?)(?=\n\s*\d+\.\s*\(|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_ID_VALUE_RE = re.compile(r"(?:Student\s*)?ID\s*:?\s*(\w+)", re.IGNORECASE)
_NAME_VALUE_RE = re.compile(r"Name\s*:?\s*([^\n]+)", re.IGNORECASE)
_NAME_LABEL_RE = re.compile(r"^\s*Name\b", re.IGNORECASE)
_ID_LABEL_RE = re.compile(r"^\s*(?:Student\s*)?ID\b", re.IGNORECASE)
_TOTAL_POINTS_LINE_RE = re.compile(r"(?i)\btotal\b.*\bpoints?\b")
_FORM_LABEL_RE = re.compile(
    r"^\s*(name|student\s*id|id|date|section|class|course|signature|score|page)\b",
    re.IGNORECASE,
)

# Patterns applied to pytesseract OCR output from a rendered scan page —
# used purely for *alignment* (locating question prefixes and the Name/ID
# labels), not for value extraction.
_SCAN_NAME_WORD_RE = re.compile(r"^name[:.,]?$", re.IGNORECASE)
_SCAN_ID_WORD_RE = re.compile(r"^id[:.,]?$", re.IGNORECASE)
_SCAN_STUDENT_WORD_RE = re.compile(r"^student[:.,]?$", re.IGNORECASE)
_SCAN_PUNCT_WORD_RE = re.compile(r"^[:.,;]+$")

# Field-bbox geometry constants (PDF points, page-size relative).
_FIELD_BOTTOM_PAD_POINTS = 10.0  # ~3.5mm below the label baseline


# ==============================================================================
# DATA STRUCTURES
# ==============================================================================

@dataclass(frozen=True)
class _TextLine:
    page: int
    page_w: float
    page_h: float
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


@dataclass(frozen=True)
class _ScanLabelBox:
    page: int
    x0: float  # pixel coords in the rendered scan
    y0: float
    x1: float
    y1: float
    page_w: int
    page_h: int


@dataclass(frozen=True)
class _ScanLayout:
    question_positions: dict[int, tuple[int, float]]  # qnum -> (page, y_top_norm)
    total_points_positions: dict[int, float]  # page -> y_top_norm
    name_label: _ScanLabelBox | None
    id_label: _ScanLabelBox | None
    # Per-page list of every OCR'd line as (y_top, x0, x1) in pixel coords,
    # sorted by y_top. Used to clip a label's field bbox at the nearest line
    # below it that actually overlaps the field's x range.
    line_boxes_by_page: dict[int, list[tuple[float, float, float]]]


@dataclass(frozen=True)
class _RosterEntry:
    student_id: str
    name: str


# ==============================================================================
# UTILITY AND PROCESSING FUNCTIONS
# ==============================================================================

def _load_roster(path: Path) -> list[_RosterEntry]:
    """Load a roster from a tab-separated file.

    Delegates parsing to the shared ``core.roster`` loader so the extractor and
    the needs-review engine never drift on what a roster file looks like.
    """
    return [
        _RosterEntry(student_id=student.student_id, name=student.name)
        for student in load_roster_students(path)
    ]


def _match_roster(student_id: str, name: str, roster: list[_RosterEntry]) -> tuple[_RosterEntry, float] | None:
    """Fuzzy match a student against the roster using ID and name."""
    best_entry: _RosterEntry | None = None
    best_score = 0.0

    for entry in roster:
        id_score = SequenceMatcher(None, student_id, entry.student_id).ratio()
        name_score = SequenceMatcher(None, name.lower(), entry.name.lower()).ratio()
        score = (id_score * 0.7) + (name_score * 0.3)
        if score > best_score:
            best_score = score
            best_entry = entry

    return (best_entry, best_score) if best_entry else None


# A student id is "usable" only if the extractor actually read digits; a blank
# or too-short value means we should fall back to matching on the name alone.
def _has_usable_id(student_id: str) -> bool:
    return len(re.sub(r"\D", "", student_id or "")) >= 4


# Resolutions to try when reading a sheet's QR, cheapest first. 150 DPI decodes
# a short payload fine and is what this always used, but a *scrambled* sheet's
# code also carries a form key, and the denser symbol that produces does not
# reliably survive 150 DPI — one sheet in five failed to decode in testing while
# reading perfectly at 200. Escalating only on failure keeps the common case as
# cheap as it was, and mirrors the cheapest-first cascade in `reorder.py`.
_QR_READ_DPIS = (150, 300)


def _read_qr_code(pdf_path: Path):  # noqa: ANN202 - OpengraderCode | None
    """Decode the Opengrader QR on a scan's first page, or ``None``.

    Best-effort: any failure, or a sheet with no Opengrader QR, yields ``None``
    so extraction falls back to the OCR identity paths.
    """
    from opengrader.core.ocr.qr_reader import read_code_from_image

    # Import lazily: this whole module is extraction-time only, and fitz
    # (PyMuPDF, ~110ms cold) has no reason to load just because the TUI
    # imports opengrader.core.submissions on startup.
    import fitz

    try:
        doc = fitz.open(str(pdf_path))
        try:
            if len(doc) == 0:
                return None
            for dpi in _QR_READ_DPIS:
                image, _, _ = render_page_to_image(doc, 0, dpi=dpi)
                code = read_code_from_image(image)
                if code is not None:
                    return code
        finally:
            doc.close()
    except Exception:
        return None
    return None


def _read_qr_student_id(pdf_path: Path) -> str | None:
    """Read a personalized-template student id from the scan's first page QR."""
    code = _read_qr_code(pdf_path)
    if code is not None and code.student_id:
        return code.student_id
    return None


def _read_qr_metadata(pdf_path: Path) -> tuple[str | None, str | None, str | None]:
    """Read ``(student_id, version, form_key)`` off the sheet's first-page QR.

    The bubble-sheet path has no printed title or version text to OCR — the
    sheet is deliberately almost all circles — so the QR is where the
    assignment version comes from, and on a scrambled batch it is also the only
    machine-readable record of which question order this sheet was printed in.
    Best-effort like its sibling: a QR-less sheet just yields all ``None``.
    """
    code = _read_qr_code(pdf_path)
    if code is None:
        return None, None, None
    return (code.student_id or None), (code.version or None), (code.form or None)


def _layout_for_form(
    layout: "BubbleSheetLayout", form_key: str | None
) -> "BubbleSheetLayout":
    """Rebuild the layout in the question order *this* sheet was printed in.

    A scrambled batch prints every sheet differently, so the shared layout the
    caller supplied describes none of them. The permutation is re-derived from
    the sheet's own form key rather than looked up in a sidecar map — the sheet
    carries the means to read itself, so nothing can be lost or fall out of
    sync with a re-generated PDF.

    Companion questions and answer-key order ride along unchanged: scrambling
    changes only where a row is printed, never what the submission ends up
    saying.
    """
    if not form_key:
        return layout

    from opengrader.core.bubble_sheet import (
        BubbleQuestion,
        build_bubble_sheet_layout,
        shuffle_questions,
    )

    questions = [
        BubbleQuestion(
            question_id=row.question_id,
            choice_labels=row.choice_labels(),
            points=row.points,
        )
        for row in layout.rows
    ]
    return build_bubble_sheet_layout(
        shuffle_questions(questions, form_key),
        config=layout.config,
        assignment_title=layout.assignment_title,
        companions=layout.companions,
        question_order=layout.question_order,
    )


def _build_document_from_bubble_sheet(
    *,
    pdf_path: Path,
    submission_dir: Path,
    layout: "BubbleSheetLayout",
    progress_callback: Callable[[str], None],
    qr_student_id: str | None,
    shuffled_batch: bool = False,
    form_key_override: str | None = None,
) -> StudentSubmissionDocument:
    """OMR extraction path: read a generated answer sheet's filled bubbles.

    A thin adapter over `submissions/bubble_extraction.py`, which does the
    work; this exists so the mode sits beside the other three at the one place
    that chooses between them. Warnings (an unregistered page) are surfaced
    through the progress callback rather than raised: the questions on a page
    that could not be read come back blank and unverified, which puts them in
    the needs-review queue instead of discarding the rest of the submission.
    """
    from opengrader.core.submissions.bubble_extraction import build_bubble_sheet_document

    _qr_id, qr_version, form_key = _read_qr_metadata(pdf_path)
    if form_key_override:
        # A human read the form code off the paper because the QR would not
        # scan. Their reading wins: it is the only source left, and refusing it
        # would leave the sheet ungradable.
        form_key = form_key_override
    if form_key:
        progress_callback(f"Scrambled sheet: form {form_key}")
    elif shuffled_batch:
        # The caller said these sheets are scrambled, and this one will not say
        # which form it is. Reading it against the unscrambled layout would map
        # every answer to the wrong question and report a confident, completely
        # wrong set of grades — the one failure this whole design exists to
        # prevent. Refuse the sheet instead: the questions come back unread and
        # land in the needs-review queue, where a human can read the printed
        # form key off the paper.
        progress_callback(
            f"Warning: {pdf_path.name} is from a scrambled batch but its form "
            "key could not be read, so its question order is unknown. Left "
            "unread — check the scan, or read the printed 'Form' code off the "
            "sheet and re-extract it on its own with --bubble-form."
        )
        return build_bubble_sheet_document(
            pdf_path,
            submission_dir,
            layout,
            assignment_title=layout.assignment_title,
            assignment_version=qr_version,
            student_id=qr_student_id or "",
            read_marks=False,
            progress_callback=progress_callback,
        ).document

    sheet_layout = _layout_for_form(layout, form_key)
    result = build_bubble_sheet_document(
        pdf_path,
        submission_dir,
        sheet_layout,
        assignment_title=layout.assignment_title,
        assignment_version=qr_version,
        student_id=qr_student_id or "",
        progress_callback=progress_callback,
    )
    for warning in result.warnings:
        progress_callback(f"Warning: {warning}")
    if result.ambiguous_count:
        progress_callback(
            f"{result.ambiguous_count} question(s) had more than one bubble filled "
            "and were left blank for review."
        )
    return result.document


@dataclass(frozen=True)
class RosterMatch:
    """Outcome of resolving one submission's identity against the roster.

    ``status`` is one of:
      * ``"matched"`` — ``entry`` is the confident single match.
      * ``"ambiguous"`` — the name matched several roster people (duplicate
        names); ``candidates`` holds them and ``entry`` is ``None`` so the
        caller never guesses.
      * ``"unmatched"`` — no confident match; keep whatever was extracted.

    ``score`` is the 0-1 similarity that produced a ``"matched"`` result. It is
    returned rather than discarded because it is the *only* measured statement
    of how sure this identity is: once a roster match lands, the stored name and
    id are the roster's own, so an OCR confidence in the handwriting no longer
    describes the value on disk. A match that barely cleared the threshold is
    exactly the one a human should look at.
    """

    status: Literal["matched", "ambiguous", "unmatched"]
    entry: _RosterEntry | None = None
    candidates: tuple[_RosterEntry, ...] = ()
    score: float | None = None


def resolve_student_identity(
    student_id: str,
    name: str,
    roster: list[_RosterEntry],
    *,
    combined_threshold: float = 0.6,
    name_threshold: float = 0.85,
) -> RosterMatch:
    """Resolve a student's roster identity, preferring the id when present.

    When the extractor read a usable id we keep the existing combined fuzzy
    match. When only a name is available (the common OCR case where the id box
    is blank or unreadable) we match on the name alone and, crucially, refuse to
    pick when several roster people share that name — that ambiguity is returned
    for the caller to resolve rather than silently mis-assigning a grade.
    """
    student_id = (student_id or "").strip()
    name = (name or "").strip()

    if _has_usable_id(student_id):
        combined = _match_roster(student_id, name, roster)
        if combined is not None and combined[1] >= combined_threshold:
            return RosterMatch(
                status="matched", entry=combined[0], score=combined[1]
            )

    if name:
        scored = sorted(
            (
                (
                    entry,
                    SequenceMatcher(None, name.lower(), entry.name.lower()).ratio(),
                )
                for entry in roster
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )
        # De-dupe by id so a roster that lists the same person twice is not
        # mistaken for a genuine duplicate-name collision. The best score per id
        # is kept alongside, since that is what a matched result reports as its
        # confidence.
        distinct: dict[str, _RosterEntry] = {}
        best_by_id: dict[str, float] = {}
        for entry, score in scored:
            if score < name_threshold:
                continue
            distinct.setdefault(entry.student_id, entry)
            best_by_id.setdefault(entry.student_id, score)
        if len(distinct) == 1:
            entry = next(iter(distinct.values()))
            return RosterMatch(
                status="matched", entry=entry, score=best_by_id[entry.student_id]
            )
        if len(distinct) > 1:
            return RosterMatch(
                status="ambiguous", candidates=tuple(distinct.values())
            )

    return RosterMatch(status="unmatched")


# Given the ambiguous submission and the roster people who share its name,
# return the chosen entry (or None to leave it unresolved). Injected by callers
# — the extractor CLI uses it to prompt interactively — so core stays pure and
# non-interactive by default.
AmbiguityResolver = Callable[
    [StudentSubmissionDocument, "tuple[_RosterEntry, ...]"],
    "_RosterEntry | None",
]


def _apply_roster_identity(
    document: StudentSubmissionDocument,
    roster: list[_RosterEntry] | None,
    progress_callback: Callable[[str], None],
    resolve_ambiguous: "AmbiguityResolver | None" = None,
    qr_student_id: str | None = None,
) -> StudentSubmissionDocument:
    """Fill a submission's student id/name from the roster where confident.

    When ``qr_student_id`` is present (read from the sheet's QR code) it is
    **authoritative**: the id is written directly, and — if a roster is supplied
    — the matching roster entry supplies the canonical name. This bypasses fuzzy
    name matching and duplicate-name ambiguity entirely, which is the whole point
    of the personalized-QR workflow. An id printed on the sheet but absent from
    the roster is still trusted (the roster may be stale).

    Otherwise: unique matches are written in. Duplicate-name collisions are
    offered to ``resolve_ambiguous`` (if provided) so an interactive caller can
    pick the right student; otherwise they are reported and left blank so the
    missing id surfaces for manual assignment (e.g. in the TUI needs-review
    queue) instead of being guessed.

    **Identity confidence.** Whatever this writes, it also records how sure that
    value is (``name_confidence``/``id_confidence``, 0-100), because once an
    identity has been *resolved* the OCR confidence in the handwriting no longer
    describes what is stored. A QR id is exact (the symbol is error-corrected,
    so it either decodes or it doesn't) and a roster name pulled alongside it is
    the roster's own, so both are 100. A fuzzy roster match records the match's
    own similarity, which is the one number that says a 62% match and a 98% one
    are not the same claim.
    """
    if qr_student_id:
        entry = None
        for candidate in roster or []:
            if candidate.student_id == qr_student_id:
                entry = candidate
                break
        if entry is not None:
            progress_callback(f"  QR: identified student id {qr_student_id}.")
            return replace(
                document,
                student_name=entry.name,
                student_id=entry.student_id,
                name_confidence=100.0,
                id_confidence=100.0,
            )
        progress_callback(
            f"  QR: student id {qr_student_id} (not on roster; trusting the code)."
        )
        # Only the id is verified here — the name is still whatever was read off
        # the sheet, so its confidence is left exactly as the reader set it.
        return replace(document, student_id=qr_student_id, id_confidence=100.0)

    if not roster:
        return document

    resolution = resolve_student_identity(
        document.student_id, document.student_name, roster
    )
    if resolution.status == "matched" and resolution.entry is not None:
        entry = resolution.entry
        if document.student_id != entry.student_id:
            progress_callback(
                f"  Roster: matched '{document.student_name or entry.name}' "
                f"-> id {entry.student_id}."
            )
        match_confidence = (
            round(resolution.score * 100, 1) if resolution.score is not None else None
        )
        return replace(
            document,
            student_name=entry.name,
            student_id=entry.student_id,
            name_confidence=match_confidence,
            id_confidence=match_confidence,
        )
    if resolution.status == "ambiguous":
        if resolve_ambiguous is not None:
            chosen = resolve_ambiguous(document, resolution.candidates)
            if chosen is not None:
                progress_callback(
                    f"  Roster: assigned id {chosen.student_id} for "
                    f"'{document.student_name}'."
                )
                # A human picked from the exact candidate set, which is the
                # strongest resolution there is — stronger than any similarity
                # score, since the tie is precisely what a score cannot settle.
                return replace(
                    document,
                    student_name=chosen.name,
                    student_id=chosen.student_id,
                    name_confidence=100.0,
                    id_confidence=100.0,
                )
        ids = ", ".join(entry.student_id for entry in resolution.candidates)
        progress_callback(
            f"  Roster: '{document.student_name}' matches multiple students "
            f"({ids}); left blank for manual assignment."
        )
        # Record the tied roster people so the review queue can offer them as a
        # pick list — the human resolves from the exact candidate set the
        # matcher considered rather than re-typing an id.
        return replace(
            document,
            id_candidates=tuple(
                IdentityCandidate(student_id=entry.student_id, name=entry.name)
                for entry in resolution.candidates
            ),
        )
    return document


# Section stamped on a mixed-stack submission whose (resolved) student id was in
# no supplied roster — surfaces in the review queue as a routing miss instead of
# being silently dropped.
UNASSIGNED_SECTION = "unassigned"


def _apply_section(
    document: StudentSubmissionDocument,
    default_section: str | None,
    section_by_id: Mapping[str, str] | None,
    progress_callback: Callable[[str], None],
) -> StudentSubmissionDocument:
    """Stamp the class section on a submission, if section routing is in effect.

    Two mutually exclusive modes, mirroring the extractor's two workflows:

    * ``default_section`` (one-stack-per-section): the whole run is known to be
      one section, so every submission is tagged with it — even one whose id
      couldn't be read, since the physical stack settles the section.
    * ``section_by_id`` (one mixed stack, routed by roster membership): the
      section is looked up by the id `_apply_roster_identity` just resolved; an
      id in no roster is tagged ``UNASSIGNED_SECTION`` and reported.

    A no-op (returns the document unchanged) when neither is supplied.
    """
    if default_section is not None:
        return replace(document, section=default_section)
    if section_by_id is not None:
        student_id = (document.student_id or "").strip()
        section = section_by_id.get(student_id) if student_id else None
        if section is None:
            progress_callback(
                f"  Section: '{document.student_name or student_id or 'unknown'}' "
                f"not found in any roster; tagged '{UNASSIGNED_SECTION}'."
            )
            return replace(document, section=UNASSIGNED_SECTION)
        return replace(document, section=section)
    return document


def _pdf_page_count(pdf_path: Path) -> int:
    """Page count of a PDF, or 0 when it can't be opened.

    Only used to decide which pages a crop never covered, so an unreadable file
    means "don't guess" rather than an error — extraction has already produced
    the grades-bearing output by the time this runs.
    """
    import fitz  # see _read_qr_code for why this is deferred

    try:
        with fitz.open(str(pdf_path)) as doc:
            return len(doc)
    except Exception:
        return 0


def _apply_extra_pages(
    document: StudentSubmissionDocument,
    pdf_path: Path,
    extra_pages: tuple[int, ...] | None,
) -> StudentSubmissionDocument:
    """Record the source pages that no question crop covers.

    Three sources, most authoritative first:

    1. an explicit ``extra_pages`` from the caller;
    2. the splitter's sidecar for this PDF (``--extra-work-pages``), where an
       empty list is a real answer ("checked, none") and is honoured as such;
    3. otherwise, inference from page coverage — any page the extractor cut no
       crop from. This is what makes a "continued on back" page reachable on
       exams that were never split with ``--extra-work-pages``.

    A submission with nothing extra is left untouched, so its markdown keeps the
    exact shape it had before this feature existed.
    """
    if extra_pages is None:
        extra_pages = read_extra_pages_for(pdf_path)
    if extra_pages is None:
        segments = tuple(
            segment
            for question in document.questions
            for segment in question.source_segments
        )
        extra_pages = infer_extra_pages(_pdf_page_count(pdf_path), segments)
    if not extra_pages:
        return document
    return replace(document, extra_pages=tuple(sorted({int(p) for p in extra_pages})))


def _finalize_submission_document(
    document: StudentSubmissionDocument,
    pdf_path: Path,
    markdown_path: Path,
    extra_pages: tuple[int, ...] | None,
) -> tuple[StudentSubmissionDocument, Path]:
    """Stamp extra work pages onto a built document and (re)write its markdown.

    The single tail every extraction path funnels through, so the three build
    strategies can't drift on this. Rendering is round-trip safe, so rewriting a
    file a path already wrote produces the same bytes plus the new frontmatter
    key (and nothing at all when there are no extra pages).
    """
    document = _apply_extra_pages(document, pdf_path, extra_pages)
    markdown_path.write_text(render_student_submission_markdown(document))
    return document, markdown_path


def _question_number(question: StudentSubmissionQuestion) -> int | None:
    match = re.fullmatch(r"q(\d+)", question.question_id)
    return int(match.group(1)) if match is not None else None


def _render_scan_all_pages(
    scan_pdf: Path,
    *,
    dpi: int = 300,
) -> dict[int, Image.Image]:
    """Render every page of a scan PDF as PIL images, keyed by 1-indexed page."""
    import fitz  # see _read_qr_code for why this is deferred

    rendered: dict[int, Image.Image] = {}
    doc = fitz.open(str(scan_pdf))
    try:
        for page_index in range(len(doc)):
            image, _, _ = render_page_to_image(doc, page_index, dpi=dpi)
            rendered[page_index + 1] = image
    finally:
        doc.close()
    return rendered


def _first_whitespace_row(
    image: Image.Image,
    *,
    x0: int,
    x1: int,
    y_start: int,
    y_max: int,
    threshold: int = 235,
    min_gap_height: int = 5,
) -> int | None:
    """Return the y of the top of the first horizontal whitespace band.

    Scans rows ``y_start..y_max`` within the column range ``[x0, x1]`` for a
    run of ``min_gap_height`` consecutive rows whose minimum pixel value is
    above ``threshold`` (i.e., pure whitespace). Returns the y of the first
    row in that run, or ``None`` if no such band is found.
    """
    if x1 <= x0 or y_max <= y_start:
        return None
    arr = np.asarray(image.convert("L"))
    height, width = arr.shape
    x0_c = max(0, min(width, x0))
    x1_c = max(x0_c, min(width, x1))
    y_start_c = max(0, min(height, y_start))
    y_max_c = max(y_start_c, min(height, y_max))
    if x1_c <= x0_c or y_max_c <= y_start_c:
        return None

    band = arr[y_start_c:y_max_c, x0_c:x1_c]
    if band.size == 0:
        return None
    is_white = band.min(axis=1) > threshold

    gap_count = 0
    gap_start = -1
    for offset, white in enumerate(is_white):
        if white:
            if gap_count == 0:
                gap_start = y_start_c + offset
            gap_count += 1
            if gap_count >= min_gap_height:
                return gap_start
        else:
            gap_count = 0
            gap_start = -1
    return None


def _label_box_from_words(words: list[dict]) -> tuple[float, float, float, float]:
    x0 = min(word["left"] for word in words)
    y0 = min(word["top"] for word in words)
    x1 = max(word["left"] + word["width"] for word in words)
    y1 = max(word["top"] + word["height"] for word in words)
    return x0, y0, x1, y1


def _label_box_starting_at(
    line_words: list[dict],
    start_index: int,
    page: int,
    page_w: int,
    page_h: int,
) -> _ScanLabelBox:
    """Build a _ScanLabelBox from a run of consecutive label-like tokens."""
    label_words = [line_words[start_index]]
    end_limit = min(len(line_words), start_index + 4)
    for offset in range(start_index + 1, end_limit):
        text = line_words[offset]["text"]
        if (
            _SCAN_PUNCT_WORD_RE.match(text)
            or _SCAN_NAME_WORD_RE.match(text)
            or _SCAN_ID_WORD_RE.match(text)
            or _SCAN_STUDENT_WORD_RE.match(text)
        ):
            label_words.append(line_words[offset])
        else:
            break
    x0, y0, x1, y1 = _label_box_from_words(label_words)
    return _ScanLabelBox(
        page=page, x0=x0, y0=y0, x1=x1, y1=y1, page_w=page_w, page_h=page_h
    )


def _detect_scan_layout(
    rendered_pages: dict[int, Image.Image],
    max_page: int | None = None,
) -> _ScanLayout:
    """Find question prefixes, totals, and Name/ID label boxes on the scan.

    Each page is OCR'd with pytesseract; words are grouped by their reported
    line index. Lines whose first token is a question prefix like
    ``1. (50 points)`` are recorded (left 30% only, to avoid mid-line false
    positives). Lines whose first token matches ``Name`` / ``ID`` /
    ``Student ID`` contribute label bboxes used to crop the field area on
    the actual scan. This OCR pass is *alignment-only* — its job is to
    locate things on the page, not to read their handwritten contents.
    """
    question_positions: dict[int, tuple[int, float]] = {}
    total_points_positions: dict[int, float] = {}
    name_label: _ScanLabelBox | None = None
    id_label: _ScanLabelBox | None = None
    line_boxes_by_page: dict[int, list[tuple[float, float, float]]] = {}

    for page_number, image in sorted(rendered_pages.items()):
        if max_page is not None and page_number > max_page:
            continue
        data = safe_image_to_data(image, config="--psm 6")
        if not data["text"]:
            continue

        lines: dict[tuple[int, int, int], list[dict]] = {}
        word_count = len(data.get("text", []))
        for i in range(word_count):
            text = str(data["text"][i]).strip()
            if not text:
                continue
            key = (
                int(data["block_num"][i]),
                int(data["par_num"][i]),
                int(data["line_num"][i]),
            )
            lines.setdefault(key, []).append(
                {
                    "text": text,
                    "left": int(data["left"][i]),
                    "top": int(data["top"][i]),
                    "width": int(data["width"][i]),
                    "height": int(data["height"][i]),
                }
            )

        width, height = image.size
        per_page_line_boxes: list[tuple[float, float, float]] = []
        for line_words in lines.values():
            line_words.sort(key=lambda word: word["left"])
            line_text = " ".join(word["text"] for word in line_words)
            line_top = min(word["top"] for word in line_words)
            line_left = min(word["left"] for word in line_words)
            line_right = max(word["left"] + word["width"] for word in line_words)
            per_page_line_boxes.append(
                (float(line_top), float(line_left), float(line_right))
            )

            question_match = SCAN_QUESTION_LINE_RE.match(line_text)
            if (
                question_match is not None
                and (line_left / width) < SCAN_QUESTION_MAX_X_NORM
            ):
                qnum = int(question_match.group(1))
                if qnum not in question_positions:
                    question_positions[qnum] = (page_number, line_top / height)

            if (
                _TOTAL_POINTS_LINE_RE.search(line_text)
                and page_number not in total_points_positions
            ):
                total_points_positions[page_number] = line_top / height

            for index, word in enumerate(line_words):
                text = word["text"]
                if name_label is None and _SCAN_NAME_WORD_RE.match(text):
                    name_label = _label_box_starting_at(
                        line_words, index, page_number, width, height
                    )
                elif id_label is None and (
                    _SCAN_ID_WORD_RE.match(text)
                    or _SCAN_STUDENT_WORD_RE.match(text)
                ):
                    id_label = _label_box_starting_at(
                        line_words, index, page_number, width, height
                    )
                if name_label is not None and id_label is not None:
                    break

        line_boxes_by_page[page_number] = sorted(
            per_page_line_boxes, key=lambda entry: entry[0]
        )

    return _ScanLayout(
        question_positions=question_positions,
        total_points_positions=total_points_positions,
        name_label=name_label,
        id_label=id_label,
        line_boxes_by_page=line_boxes_by_page,
    )


def _field_location_from_scan_label(
    label: _ScanLabelBox,
    *,
    right_boundary: _ScanLabelBox | None,
    line_boxes: list[tuple[float, float, float]],
    page_image: Image.Image | None = None,
    tight_top: bool = False,
) -> FieldLocation:
    """Convert a scan-detected label box into a normalized FieldLocation.

    The field starts just after the label horizontally and extends to
    either another label on the same horizontal band (the typical
    "Name: ...  ID: ..." layout) or near the right edge of the page.
    Vertically it spans the top of the page down to a few millimeters
    below the label, then is clipped at the first vertical whitespace gap
    below the label (so the crop stops where the label-line content
    actually ends) and as a secondary safety net just above the next OCR'd
    line below.

    With ``tight_top=True`` the crop's top is pulled down to a small
    ascender pad above the label line instead of starting at the top of
    the page — suited to fields like student-ID where the field column
    accumulates stray marks above (e.g., margin doodles) that mislead OCR.
    """
    line_height = max(1.0, label.y1 - label.y0)
    vertical_pad = max(8.0, 0.3 * line_height)

    x0_px = label.x1 + 5.0
    if (
        right_boundary is not None
        and right_boundary.page == label.page
        and right_boundary.x0 > label.x1
        and not (
            right_boundary.y0 > label.y1 + 0.5 * line_height
            or right_boundary.y1 < label.y0 - 0.5 * line_height
        )
    ):
        x1_px = right_boundary.x0 - 5.0
    else:
        x1_px = label.page_w * 0.97

    y0_px = max(0.0, label.y0 - vertical_pad) if tight_top else 0.0
    bottom_pad_px = _FIELD_BOTTOM_PAD_POINTS * (label.page_h / 792.0)
    y1_pad = label.y1 + bottom_pad_px

    next_line_top: float | None = None
    for top, line_x0, line_x1 in line_boxes:
        if abs(top - label.y0) < 5.0:
            continue
        if top <= label.y0:
            continue
        if line_x1 <= x0_px or line_x0 >= x1_px:
            continue
        next_line_top = top
        break
    y1_px = min(y1_pad, next_line_top - 2.0) if next_line_top is not None else y1_pad

    if page_image is not None:
        whitespace_y = _first_whitespace_row(
            page_image,
            x0=int(x0_px),
            x1=int(x1_px),
            y_start=int(label.y0 + 1),
            y_max=int(y1_px + line_height),
            min_gap_height=max(3, int(0.25 * line_height)),
        )
        if whitespace_y is not None and whitespace_y > label.y0:
            y1_px = min(y1_px, float(whitespace_y))

    # Always include enough room below the label baseline to capture
    # handwriting descenders (g, p, q, y, j). Clamped to the page bottom.
    y1_px = min(float(label.page_h), max(y1_px, label.y1 + vertical_pad))

    return FieldLocation(
        page=label.page,
        bbox=(
            round(x0_px / label.page_w, 4),
            round(y0_px / label.page_h, 4),
            round(max(0.0, (x1_px - x0_px) / label.page_w), 4),
            round(max(0.0, (y1_px - y0_px) / label.page_h), 4),
        ),
    )


def _response_bbox_from_positions(
    qnum: int,
    positions: dict[int, tuple[int, float]],
    total_points_positions: dict[int, float],
) -> tuple[int, tuple[float, float, float, float]] | None:
    """Compute a response bbox using a combined per-question position map.

    The next question's top — used as this question's bbox bottom — is found
    in the supplied positions dict, which can hold scan-detected positions
    for the questions whose prefixes were OCR'd on the scan and blank-assignment
    positions (already shifted by the page's scan offset) for the rest.
    """
    position = positions.get(qnum)
    if position is None:
        return None

    page, y_top = position
    next_y_same_page: float | None = None
    for other_qnum, (other_page, other_y) in positions.items():
        if other_qnum <= qnum or other_page != page:
            continue
        if other_y <= y_top:
            continue
        if next_y_same_page is None or other_y < next_y_same_page:
            next_y_same_page = other_y

    candidates = [1.0 - 0.025]
    if next_y_same_page is not None:
        candidates.append(max(y_top + 0.005, next_y_same_page - 0.005))
    total_y = total_points_positions.get(page)
    if total_y is not None and total_y > y_top:
        candidates.append(max(y_top + 0.005, total_y - 0.005))
    y_bottom = max(y_top + 0.005, min(candidates))

    x0 = 0.08
    x1 = 0.92
    return page, (
        round(x0, 4),
        round(y_top, 4),
        round(x1 - x0, 4),
        round(max(0.005, y_bottom - y_top), 4),
    )


def _read_pdf_lines(pdf_path: Path) -> list[_TextLine]:
    """Return every non-empty text line in the PDF with its page bbox."""
    import fitz  # see _read_qr_code for why this is deferred

    doc = fitz.open(str(pdf_path))
    try:
        lines: list[_TextLine] = []
        for page_index, page in enumerate(doc, start=1):
            pw, ph = page.rect.width, page.rect.height
            page_dict = page.get_text("dict")
            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:  # text blocks only
                    continue
                for line in block.get("lines", []):
                    text = "".join(span.get("text", "") for span in line.get("spans", []))
                    if not text.strip():
                        continue
                    x0, y0, x1, y1 = line["bbox"]
                    lines.append(
                        _TextLine(
                            page=page_index,
                            page_w=pw,
                            page_h=ph,
                            x0=x0,
                            y0=y0,
                            x1=x1,
                            y1=y1,
                            text=text,
                        )
                    )
        return lines
    finally:
        doc.close()


def _ensure_text_layer(lines: list[_TextLine], pdf_path: Path) -> None:
    if not any(line.text.strip() for line in lines):
        raise ValueError(
            f"no text layer found in PDF: {pdf_path}. "
            "Pass --blank-template PATH.pdf to apply a blank assignment template instead, "
            "or run an OCR step first."
        )


def _norm_bbox(
    line: _TextLine,
    *,
    x0: float | None = None,
    y0: float | None = None,
    x1: float | None = None,
    y1: float | None = None,
) -> tuple[float, float, float, float]:
    """Normalize PDF-space coordinates to fractions of the line's page size."""
    pw, ph = max(line.page_w, 1.0), max(line.page_h, 1.0)
    nx0 = (x0 if x0 is not None else line.x0) / pw
    ny0 = (y0 if y0 is not None else line.y0) / ph
    nx1 = (x1 if x1 is not None else line.x1) / pw
    ny1 = (y1 if y1 is not None else line.y1) / ph
    return (
        round(nx0, 4),
        round(ny0, 4),
        round(max(0.0, nx1 - nx0), 4),
        round(max(0.0, ny1 - ny0), 4),
    )


# Markers a born-digital submission commonly uses to introduce the typed
# answer ("Answer:", "Ans.", "Response -", "Solution:"), anchored to a line
# start so a mention inside the prompt doesn't trip it.
_ANSWER_MARKER_RE = re.compile(
    r"(?im)^\s*(?:answer|ans|response|solution)\b\s*[:.\-]?\s*"
)


def _split_prompt_and_response(block: str) -> tuple[str, str]:
    """Split a question text block into (prompt, typed response).

    Born-digital submissions (e.g. a PDF exported from Word) carry the
    student's typed answer in the text layer right after each question. We
    separate it from the prompt on a best-effort, conservative basis:

    1. an explicit answer marker line ("Answer:", "Response -", …), else
    2. the first blank line (prompt paragraph vs. the typed answer below it).

    When neither is present we keep the whole block as the prompt and leave the
    response empty rather than guess — an empty response surfaces in the
    needs-review queue, which is safer than polluting the prompt with the
    answer (or vice versa).
    """
    marker = _ANSWER_MARKER_RE.search(block)
    if marker is not None:
        return block[: marker.start()], block[marker.end() :]
    paragraphs = re.split(r"\n\s*\n", block, maxsplit=1)
    if len(paragraphs) == 2:
        return paragraphs[0], paragraphs[1]
    return block, ""


def _extract_questions_from_text(lines: list[_TextLine]) -> list[StudentSubmissionQuestion]:
    """Extract prompts, typed responses, points, and bboxes from PDF text lines."""
    all_text = "\n".join(l.text for l in lines)
    questions: list[StudentSubmissionQuestion] = []

    for match in _QUESTION_INLINE_RE.finditer(all_text):
        qnum_str, pts_str, qtext = match.groups()
        qnum = int(qnum_str)

        prompt_text, response_text = _split_prompt_and_response(qtext)

        # Find the physical line matching the start of this question to get bbox
        start_line = next((l for l in lines if re.search(rf"^{qnum_str}\s*\.", l.text.strip())), lines[0])
        bbox = _norm_bbox(start_line)

        cleaned_response = re.sub(r"\s+", " ", response_text).strip()
        questions.append(
            StudentSubmissionQuestion(
                question_id=f"q{qnum}",
                prompt=re.sub(r"\s+", " ", prompt_text).strip(),
                possible_points=float(pts_str),
                response_image_path=None,
                response_text=cleaned_response,
                source_segments=(QuestionSourceSegment(page_number=start_line.page, bbox_norm=bbox),),
                # Read verbatim from the PDF text layer — exact, so full confidence.
                response_confidence=100.0 if cleaned_response else None,
                response_source=SOURCE_TEXT_LAYER if cleaned_response else None,
            )
        )
    return questions


def _ocr_image(image_path: Path, *, psm: int = 6) -> str:
    """Perform Tesseract OCR on an image path."""
    # Import lazily: pytesseract (~110ms cold) is extraction-time only, like
    # fitz above (see _read_qr_code) — this whole module is the extraction
    # pipeline, never touched by the TUI's parse/render/grade path.
    import pytesseract

    try:
        with Image.open(image_path) as img:
            return pytesseract.image_to_string(img, config=f"--psm {psm}").strip()
    except Exception:
        return ""


def _ocr_image_with_confidence(
    image_path: Path, *, psm: int = 6
) -> tuple[str, float | None]:
    """OCR an image, returning the text and a mean word confidence (0-100).

    Aggregates Tesseract's per-word confidence into one number for the crop —
    the signal behind the grade screen's confidence chip and the low-confidence
    review flag. Words Tesseract reports with no confidence (``conf == -1``) or
    empty text are ignored; when nothing readable remains the confidence is
    ``None`` (an empty response, handled elsewhere), never a misleading zero.
    """
    import pytesseract  # see _ocr_image for why this is deferred

    try:
        with Image.open(image_path) as img:
            data = pytesseract.image_to_data(
                img, config=f"--psm {psm}", output_type=pytesseract.Output.DICT
            )
    except Exception:
        return "", None

    words: list[str] = []
    confidences: list[float] = []
    for text, conf in zip(data.get("text", []), data.get("conf", [])):
        token = str(text).strip()
        if not token:
            continue
        try:
            conf_value = float(conf)
        except (TypeError, ValueError):
            continue
        words.append(token)
        if conf_value >= 0:
            confidences.append(conf_value)

    if not words:
        return "", None
    mean_confidence = sum(confidences) / len(confidences) if confidences else None
    return " ".join(words), mean_confidence


def _ocr_field_with_confidence(
    image_path: Path, *, digits: bool
) -> tuple[str, float | None]:
    """Read a name/id field crop, returning the text and its OCR confidence.

    The field counterpart of :func:`_ocr_image_with_confidence`, and the source
    of the ``student_name_confidence``/``student_id_confidence`` frontmatter.
    The id variant keeps the existing multi-PSM digit sweep (a handwritten id is
    read far better with a digit whitelist) and measures the pass that produced
    the text, so the recorded number describes the reading that was actually
    kept rather than a second, differently-configured one.

    ``None`` confidence means *unmeasured*, never *bad* — the same convention
    the per-response confidence uses for a VLM transcription.
    """
    if not digits:
        return _ocr_image_with_confidence(image_path, psm=7)

    digit_only = "tessedit_char_whitelist=0123456789"
    best_text = ""
    best_confidence: float | None = None
    for config_str in (
        f"--psm 11 -c {digit_only}",
        f"--psm 7 -c {digit_only}",
        f"--psm 6 -c {digit_only}",
    ):
        text, confidence = _ocr_data_with_confidence(image_path, config_str)
        digits_only = re.sub(r"\D", "", text)
        # Longest run of digits wins, matching `_ocr_image_digits`'s existing
        # preference — a partial read of an id is worse than a fuller one.
        if len(digits_only) > len(best_text):
            best_text = digits_only
            best_confidence = confidence
    return best_text, best_confidence


def _ocr_data_with_confidence(
    image_path: Path, config: str
) -> tuple[str, float | None]:
    """Run one Tesseract pass, returning its text and mean word confidence."""
    import pytesseract  # see _ocr_image for why this is deferred

    try:
        with Image.open(image_path) as img:
            data = pytesseract.image_to_data(
                img, config=config, output_type=pytesseract.Output.DICT
            )
    except Exception:
        return "", None

    words: list[str] = []
    confidences: list[float] = []
    for text, conf in zip(data.get("text", []), data.get("conf", [])):
        token = str(text).strip()
        if not token:
            continue
        try:
            conf_value = float(conf)
        except (TypeError, ValueError):
            continue
        words.append(token)
        if conf_value >= 0:
            confidences.append(conf_value)
    if not words:
        return "", None
    return (
        " ".join(words),
        sum(confidences) / len(confidences) if confidences else None,
    )


def _ocr_image_digits(image_path: Path) -> str:
    """Perform Tesseract OCR on an image path, optimized for digits."""
    import pytesseract  # see _ocr_image for why this is deferred

    digit_only = "tessedit_char_whitelist=0123456789"
    best = ""
    # Try different PSM modes for robustness
    for config_str in (
        f"--psm 11 -c {digit_only}",
        f"--psm 7 -c {digit_only}",
        f"--psm 6 -c {digit_only}",
    ):
        try:
            with Image.open(image_path) as img:
                text = pytesseract.image_to_string(img, config=config_str)
        except Exception:
            continue
        digits = re.sub(r"\D", "", text)
        if len(digits) > len(best):
            best = digits
    return best
# ==============================================================================
# STRATEGY HELPERS
# ==============================================================================

def _load_blank_assignment_template(pdf_path: Path) -> AssignmentTemplate:
    """Extract the structural template from a blank assignment PDF."""
    lines = _read_pdf_lines(pdf_path)
    _ensure_text_layer(lines, pdf_path)

    import fitz  # see _read_qr_code for why this is deferred

    with fitz.open(str(pdf_path)) as doc:
        total_pages = len(doc)

    name_field = None
    id_field = None
    for line in lines:
        if _NAME_LABEL_RE.match(line.text):
            name_field = FieldLocation(line.page, _norm_bbox(line))
        elif _ID_LABEL_RE.match(line.text):
            id_field = FieldLocation(line.page, _norm_bbox(line))

    return AssignmentTemplate(
        assignment_title=_assignment_title_from_lines(lines),
        name_field=name_field,
        student_id_field=id_field,
        questions=tuple(_extract_questions_from_text(lines)),
        total_pages=total_pages,
    )

def _select_field(
    label: _ScanLabelBox | None,
    right_boundary: _ScanLabelBox | None,
    template_field: FieldLocation | None,
    line_boxes_by_page: dict[int, list[tuple[float, float, float]]],
    rendered_pages: dict[int, Image.Image],
    tight_top: bool = False,
) -> FieldLocation | None:
    if label is not None:
        return _field_location_from_scan_label(
            label,
            right_boundary=right_boundary,
            line_boxes=line_boxes_by_page.get(label.page, []),
            page_image=rendered_pages.get(label.page),
            tight_top=tight_top,
        )
    return template_field

def _save_field_crop(
    image: Image.Image,
    field_loc: FieldLocation,
    output_dir: Path,
    prefix: str,
) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    crop = crop_norm(image, field_loc.bbox)
    filename = f"{prefix}.png"
    crop.save(output_dir / filename)
    return str(Path(output_dir.name) / filename)

def _scan_offset_by_page(
    template: AssignmentTemplate,
    scan_layout: _ScanLayout,
) -> dict[int, float]:
    """Calculate median vertical shift per page between template and scan."""
    deltas: dict[int, list[float]] = {}
    for q in template.questions:
        qnum = _question_number(q)
        scan_pos = scan_layout.question_positions.get(qnum)
        if scan_pos and q.source_segments:
            page, scan_y = scan_pos
            template_y = q.source_segments[0].bbox_norm[1]
            if page == q.source_segments[0].page_number:
                deltas.setdefault(page, []).append(scan_y - template_y)
    return calculate_page_offsets(deltas)

def _combined_question_positions(
    template: AssignmentTemplate,
    scan_layout: _ScanLayout,
    page_offsets: dict[int, float],
) -> dict[int, tuple[int, float]]:
    """Merge scan-detected positions with offset template positions."""
    merged = scan_layout.question_positions.copy()
    for q in template.questions:
        qnum = _question_number(q)
        if qnum in merged or not q.source_segments:
            continue
        
        t_seg = q.source_segments[0]
        offset = page_offsets.get(t_seg.page_number, 0.0)
        merged[qnum] = (t_seg.page_number, t_seg.bbox_norm[1] + offset)
    return merged

def _question_with_scan_crop(
    question: StudentSubmissionQuestion,
    positions: dict[int, tuple[int, float]],
    total_points_positions: dict[int, float],
    rendered_pages: dict[int, Image.Image],
    responses_dir: Path,
) -> StudentSubmissionQuestion:
    qnum = _question_number(question)
    if qnum is None:
        return question

    res = _response_bbox_from_positions(qnum, positions, total_points_positions)
    if res is None:
        return question

    page_num, bbox = res
    image = rendered_pages.get(page_num)
    if image is None:
        return question

    responses_dir.mkdir(parents=True, exist_ok=True)
    crop = crop_norm(image, bbox)
    filename = f"{question.question_id}.png"
    crop.save(responses_dir / filename)

    return replace(
        question,
        response_image_path=str(Path(responses_dir.name) / filename),
        source_segments=(QuestionSourceSegment(page_number=page_num, bbox_norm=bbox),),
    )


def _question_with_mapped_crop(
    question: StudentSubmissionQuestion,
    rendered_pages: dict[int, Image.Image],
    responses_dir: Path,
) -> StudentSubmissionQuestion:
    """Crop a question whose region an instructor drew (``exact_regions``).

    The counterpart of :func:`_question_with_scan_crop`, and the whole reason
    ``AssignmentTemplate.exact_regions`` exists. Nothing is inferred here: the
    recorded rectangles *are* the answer regions, so they are cut out verbatim.
    No expansion, no whitespace trimming, no next-question bottom edge — the box
    the instructor drew is what they will see in the grading TUI, which is the
    only way "draw it once and check the preview" can be a trustworthy promise.

    A question mapped across a page break carries continuation segments; those
    are cropped separately and stacked vertically into the single response image
    the rest of the pipeline expects, exactly as the OCR path does for a
    detected continuation. ``source_segments`` keeps every segment, so the grade
    screen's whole-page view still highlights both halves on their own pages.

    A segment whose page is missing from the scan (a short scan, or a map for a
    longer exam) is skipped rather than fatal — one lost answer belongs in the
    review queue, not in a crash that loses the other 39.
    """
    segments = [
        segment
        for segment in question.source_segments
        if segment.page_number in rendered_pages
    ]
    crops: list[Image.Image] = []
    kept: list[QuestionSourceSegment] = []
    for segment in segments:
        crop = crop_norm(rendered_pages[segment.page_number], segment.bbox_norm)
        if crop is None or crop.width <= 0 or crop.height <= 0:
            continue
        crops.append(crop.convert("RGB"))
        kept.append(segment)

    if not crops:
        return question

    if len(crops) == 1:
        stitched = crops[0]
    else:
        width = max(crop.width for crop in crops)
        height = sum(crop.height for crop in crops)
        # White, not black: the page under these crops is paper, and a black
        # gutter beside a narrower continuation strip reads as ink to both
        # Tesseract and the vision model.
        stitched = Image.new("RGB", (width, height), color=(255, 255, 255))
        y = 0
        for crop in crops:
            stitched.paste(crop, (0, y))
            y += crop.height

    responses_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{question.question_id}.png"
    stitched.save(responses_dir / filename)
    return replace(
        question,
        response_image_path=str(Path(responses_dir.name) / filename),
        source_segments=tuple(kept),
    )


def _source_pdf_relpath(pdf_path: Path, submission_dir: Path) -> str:
    try:
        return os.path.relpath(pdf_path, submission_dir)
    except ValueError:
        return str(pdf_path)

def _assignment_title_from_lines(lines: list[_TextLine]) -> str:
    return lines[0].text.strip() if lines else "Unknown Assignment"

def _render_submission_markdown(
    document: StudentSubmissionDocument,
) -> str:
    return render_student_submission_markdown(document)

# ==============================================================================
# STRATEGY: EXTRACT FROM RAW TEMPLATE OR TEXT LAYERS
# ==============================================================================


def _fill_responses_with_vlm(
    questions: list[StudentSubmissionQuestion],
    submission_dir: Path,
    ollama_config: OllamaVisionConfig,
    *,
    pdf_name: str,
    progress: ExtractionProgress,
    progress_callback: Callable[[str], None],
    math_latex: bool = False,
    math_transcriber: "MathTranscriber | None" = None,
    escalate_floor: float | None = None,
) -> list[StudentSubmissionQuestion]:
    """Transcribe each question's response crop into ``response_text``.

    Two orders, one function, because the *reading* rules (what counts as a
    result, what each reader's confidence means, what happens when one returns
    nothing) are identical and only the sequence differs:

    * **model-first** (the default, ``escalate_floor=None``): the maths backend
      if one was chosen, then the Ollama vision model, then Tesseract as the
      fallback. Every crop costs a model call.
    * **escalated** (``escalate_floor`` set, ``--llm-escalate``): Tesseract
      reads every crop first — fast, local, and *measured* — and a model is
      asked only about the answers it read badly (see
      :func:`~core.submissions.transcription.should_escalate`). On a typical
      exam most answers are short and legible, so this is the difference
      between one model call per answer and one per *problem* answer.

    The escalated order is safe for exactly one reason, and it is the same rule
    the maths backend already follows: **a reader that returns nothing never
    costs an answer.** An escalation whose model reads nothing keeps the OCR
    text and the OCR confidence, so the worst case of escalating is the time it
    took. The best case is a handwritten answer Tesseract mangled being
    replaced by a model that can read it — which is exactly the population the
    review queue was going to hand to a human.

    Questions that already carry response text, or that have no crop image, are
    left untouched. Ticks the progress reporter once per transcribed question.
    Every reading records **which reader produced it** (`response_source`), so
    the choice made here is auditable on disk and measurable afterwards by
    `transcribe-eval`.
    """
    filled: list[StudentSubmissionQuestion] = []
    for question in questions:
        response_text = question.response_text
        response_confidence = question.response_confidence
        response_source = question.response_source
        if not response_text and question.response_image_path:
            crop_path = submission_dir / question.response_image_path
            reading = _transcribe_crop(
                crop_path,
                question_id=question.question_id,
                ollama_config=ollama_config,
                progress_callback=progress_callback,
                math_latex=math_latex,
                math_transcriber=math_transcriber,
                escalate_floor=escalate_floor,
            )
            response_text, response_confidence, response_source = reading
            progress.response(pdf_name, question.question_id)
        filled.append(
            replace(
                question,
                response_text=response_text,
                response_confidence=response_confidence,
                response_source=response_source,
            )
        )
    return filled


def _transcribe_crop(
    crop_path: Path,
    *,
    question_id: str,
    ollama_config: OllamaVisionConfig | None,
    progress_callback: Callable[[str], None],
    math_latex: bool = False,
    math_transcriber: "MathTranscriber | None" = None,
    escalate_floor: float | None = None,
) -> tuple[str, float | None, str | None]:
    """Read one answer crop, returning ``(text, confidence, reader)``.

    The single place the pipeline decides *how* an answer gets read, shared by
    the template path and the dark-scan path so the two can never disagree
    about what a reading is worth. Returns the reader that actually produced
    the text, which is what the ``Read by:`` line records.
    """

    def _read_with_ocr() -> tuple[str, float | None, str | None]:
        text, confidence = _ocr_image_with_confidence(crop_path, psm=6)
        # No text means nothing was read, not "Tesseract read an empty answer":
        # attributing a blank to a reader would make the run's tally claim
        # coverage it does not have.
        return (text, confidence, SOURCE_TESSERACT if text.strip() else None)

    def _read_with_math() -> tuple[str, float | None, str | None] | None:
        if math_transcriber is None:
            return None
        progress_callback(f"Transcribing {question_id} as LaTeX...")
        text = math_transcriber(crop_path) or ""
        if not text:
            return None
        # A model transcription, like the VLM's: no calibrated confidence, so
        # it is recorded as unverified rather than given a number nobody
        # measured.
        return (text, None, SOURCE_MATH_MODEL)

    def _read_with_vlm() -> tuple[str, float | None, str | None] | None:
        if ollama_config is None:
            return None
        progress_callback(f"Transcribing {question_id} with Ollama VLM...")
        try:
            _prompt, answer = extract_question_prompt_and_answer(
                image_path=crop_path,
                config=ollama_config,
                math_latex=math_latex,
            )
        except Exception as exc:
            progress_callback(f"  Warning: Ollama VLM failed for {question_id}: {exc}")
            return None
        text = answer or ""
        return (text, None, SOURCE_VISION_MODEL) if text else None

    if escalate_floor is not None:
        ocr_reading = _read_with_ocr()
        text, confidence, _source = ocr_reading
        if not should_escalate(text, confidence, floor=escalate_floor):
            return ocr_reading
        progress_callback(
            f"  {question_id}: {_escalation_reason(text, confidence)}, "
            "re-reading with a model..."
        )
        # The maths backend first for the same reason as below — it is the
        # specialist — then the vision model. A model that reads nothing leaves
        # the OCR reading standing rather than blanking the answer.
        return _read_with_math() or _read_with_vlm() or ocr_reading

    # Model-first: the specialist maths backend, the vision model, then OCR as
    # the reader that always answers.
    return _read_with_math() or _read_with_vlm() or _read_with_ocr()


def _escalation_reason(text: str, confidence: float | None) -> str:
    """Why this reading is being escalated, for the run's progress output."""
    if not text.strip():
        return "OCR read nothing"
    return f"OCR confidence {confidence:.0f}%" if confidence is not None else "OCR unsure"


def _build_document_from_assignment_template(
    pdf_path: Path,
    submission_dir: Path,
    template: AssignmentTemplate,
    *,
    ollama_config: OllamaVisionConfig | None = None,
    llm_responses: bool = False,
    llm_math: bool = False,
    # Resolved once by the caller and passed down (it cannot cross a process
    # boundary as a callable, so `build_student_submission_document_unified`
    # builds it inside the worker). Absent from this signature until now, which
    # made every call from the unified entry point — i.e. every real use of
    # --blank-template — die with a TypeError before a single page was read.
    math_transcriber: "MathTranscriber | None" = None,
    escalate_floor: float | None = None,
    dpi: int = 300,
    progress: ExtractionProgress | None = None,
    progress_callback: Callable[[str], None] = lambda _message: None,
) -> tuple[
    StudentSubmissionDocument,
    FieldLocation | None,
    FieldLocation | None,
    str | None,
    str | None,
]:
    """Build a student .submission.md using the blank-assignment layout.

    Renders every page of the scan, detects on-paper question and Name/ID
    label positions via pytesseract (alignment only — no value-extraction
    OCR), saves Name/ID field crops to ``field_crops/`` and per-question
    response crops to ``responses/``, and assembles a submission document.

    When ``llm_responses`` is set and an ``ollama_config`` is supplied, each
    question's response crop is additionally transcribed into ``response_text``
    via the vision model (with a Tesseract fallback). Otherwise per-question
    response text is left empty for a downstream OCR pass to populate.

    A template with ``exact_regions`` set (one built from a hand-drawn region
    map) takes a shorter route: its rectangles are cropped verbatim and the
    alignment OCR is skipped entirely. Everything after the crops — the vision
    model, the roster match, the markdown — is identical either way.
    """
    progress = progress or ExtractionProgress()
    # The caller's render DPI reaches this path too. It used to be fixed at the
    # helper's own 300 default — identical to the CLI's, so nothing changes for
    # a default run — which meant `--dpi 450` silently did nothing here and, more
    # to the point, `submission-extractor --refine --refine-dpi` could not buy a
    # sharper crop for a template-aligned scan, which is the commonest kind.
    rendered_pages = _render_scan_all_pages(pdf_path, dpi=dpi)

    field_crops_dir = submission_dir / "identifiers"
    responses_dir = submission_dir / "responses"

    # A hand-drawn template needs none of the alignment machinery below, and
    # running it anyway would be both wasteful and wrong. Wasteful because
    # `_detect_scan_layout` OCRs every page of every scan and nothing here would
    # read the result. Wrong because that OCR looks for Opengrader's own
    # `N. (M points)` prefixes and `Name:` / `ID:` labels — an exam written in
    # Word may print none of them, and a stray match would shift every crop by a
    # meaningless amount. See `AssignmentTemplate.exact_regions`.
    if template.exact_regions:
        scan_layout = None
        name_field = template.name_field
        id_field = template.student_id_field
    else:
        scan_layout = _detect_scan_layout(
            rendered_pages, max_page=template.total_pages
        )
        name_field = _select_field(
            label=scan_layout.name_label,
            right_boundary=scan_layout.id_label,
            template_field=template.name_field,
            line_boxes_by_page=scan_layout.line_boxes_by_page,
            rendered_pages=rendered_pages,
        )
        id_field = _select_field(
            label=scan_layout.id_label,
            right_boundary=None,
            template_field=template.student_id_field,
            line_boxes_by_page=scan_layout.line_boxes_by_page,
            rendered_pages=rendered_pages,
            tight_top=True,
        )

    name_image: str | None = None
    if name_field is not None:
        page_image = rendered_pages.get(name_field.page)
        if page_image is not None:
            name_image = _save_field_crop(page_image, name_field, field_crops_dir, "name")

    id_image: str | None = None
    if id_field is not None:
        page_image = rendered_pages.get(id_field.page)
        if page_image is not None:
            id_image = _save_field_crop(page_image, id_field, field_crops_dir, "id")

    if scan_layout is None:
        questions = [
            _question_with_mapped_crop(question, rendered_pages, responses_dir)
            for question in template.questions
        ]
    else:
        page_offsets = _scan_offset_by_page(template, scan_layout)
        positions = _combined_question_positions(template, scan_layout, page_offsets)
        questions = [
            _question_with_scan_crop(
                question,
                positions,
                scan_layout.total_points_positions,
                rendered_pages,
                responses_dir,
            )
            for question in template.questions
        ]

    if llm_responses and ollama_config is not None:
        crop_count = sum(1 for q in questions if q.response_image_path)
        progress.file_start(pdf_path.name, crop_count)
        questions = _fill_responses_with_vlm(
            questions,
            submission_dir,
            ollama_config,
            pdf_name=pdf_path.name,
            progress=progress,
            progress_callback=progress_callback,
            math_latex=llm_math,
            math_transcriber=math_transcriber,
            escalate_floor=escalate_floor,
        )

    document = StudentSubmissionDocument(
        student_name="",
        student_id="",
        course_code=None,
        assignment_title=template.assignment_title,
        assignment_version=None,
        source_pdf=_source_pdf_relpath(pdf_path, submission_dir),
        student_score="",
        name_image_path=name_image,
        id_image_path=id_image,
        questions=questions,
    )
    return document, name_field, id_field, name_image, id_image


def _build_document_from_text_layer_only(
    pdf_path: Path,
    submission_dir: Path,
    progress_callback: Callable[[str], None],
    roster: list[_RosterEntry] | None = None,
    resolve_ambiguous: "AmbiguityResolver | None" = None,
    default_section: str | None = None,
    section_by_id: Mapping[str, str] | None = None,
    qr_student_id: str | None = None,
) -> tuple[StudentSubmissionDocument, Path]:
    """Build a document directly from a text-layer student PDF (no scan crops)."""
    progress_callback("Extracting from text layer...")

    lines = _read_pdf_lines(pdf_path)
    _ensure_text_layer(lines, pdf_path)

    all_text = "\n".join(line.text for line in lines)

    raw_id = id_match.group(1) if (id_match := _ID_VALUE_RE.search(all_text)) else ""
    raw_name = name_match.group(1).strip() if (name_match := _NAME_VALUE_RE.search(all_text)) else ""

    document = StudentSubmissionDocument(
        student_name=raw_name,
        student_id=raw_id,
        course_code=None,
        assignment_title=_assignment_title_from_lines(lines),
        assignment_version=None,
        source_pdf=_source_pdf_relpath(pdf_path, submission_dir),
        student_score="",
        # A born-digital PDF's text layer is the exact characters the file
        # carries, not a reading of them — the same 100 the per-response
        # confidence records for a text-layer answer. Only claimed for a field
        # that actually matched; a blank one is unmeasured, not perfect.
        name_confidence=100.0 if raw_name else None,
        id_confidence=100.0 if raw_id else None,
        questions=_extract_questions_from_text(lines),
    )
    document = _apply_roster_identity(
        document, roster, progress_callback, resolve_ambiguous, qr_student_id
    )
    document = _apply_section(
        document, default_section, section_by_id, progress_callback
    )
    markdown_path = submission_dir / f"{pdf_path.stem}.submission.md"
    markdown_path.write_text(render_student_submission_markdown(document))
    return document, markdown_path


def _build_document_from_ocr_result(
    pdf_path: Path,
    submission_dir: Path,
    ocr_config: OcrConfig,
    dpi: int,
    split_subquestions: bool,
    ollama_config: OllamaVisionConfig | None,
    skip_ollama_qa: bool,
    save_layout_debug: bool,
    progress_callback: Callable[[str], None],
    roster: list[_RosterEntry] | None = None,
    resolve_ambiguous: "AmbiguityResolver | None" = None,
    progress: ExtractionProgress | None = None,
    default_section: str | None = None,
    section_by_id: Mapping[str, str] | None = None,
    llm_math: bool = False,
    math_transcriber: "MathTranscriber | None" = None,
    escalate_floor: float | None = None,
    qr_student_id: str | None = None,
) -> tuple[StudentSubmissionDocument, Path]:
    """Processes a single student PDF using OCR and VLM to extract submission data."""
    # The local import the `if TYPE_CHECKING` block at the top of this module
    # promises. It has to be *here* rather than up there: a name imported under
    # TYPE_CHECKING exists only for the type checker, so without this line the
    # call below raises `NameError: name 'process_submission_pdf' is not
    # defined` at run time — invisible to the type checker and to any test that
    # stubs the extractor, and fatal to every OCR extraction. Keeping it inside
    # the function is deliberate and is what the note upstairs explains:
    # `ocr/extractor` pulls in cv2 and fitz (~230 ms cold) and this module is
    # loaded eagerly on the TUI's startup path.
    from opengrader.core.ocr.extractor import process_submission_pdf

    progress = progress or ExtractionProgress()
    progress_callback("Scanning layout...")

    submission_result = process_submission_pdf(
        pdf_path=pdf_path,
        config=ocr_config,
        output_dir=submission_dir,
        dpi=dpi,
        split_subquestions=split_subquestions,
        save_layout_debug=save_layout_debug,
    )

    student_name = ""
    student_id = ""
    name_image_path = None
    id_image_path = None
    # How sure we are of each field, on the same 0-100 scale as a response's
    # transcription confidence. ``None`` = unmeasured (a VLM read gives no
    # calibrated score), never "bad" — see `_ocr_field_with_confidence`.
    name_confidence: float | None = None
    id_confidence: float | None = None

    # Extract fields (name, ID)
    for field_res in submission_result.fields:
        if not field_res.crop_path:
            continue
        field_crop = Path(field_res.crop_path)

        if field_res.field_id == "student_name":
            name_image_path = field_crop.relative_to(submission_dir).as_posix()
            if ollama_config:
                progress_callback("Extracting name with Ollama VLM...")
                try:
                    student_name = extract_field_text(image_path=field_crop, field_kind="name", config=ollama_config)
                    # Left unverified on purpose: the model returns no score, so
                    # inventing one here would be worse than saying nothing.
                except Exception as e:
                    progress_callback(f"  Warning: Ollama VLM failed for name: {e}")
            if not student_name:
                student_name, name_confidence = _ocr_field_with_confidence(
                    field_crop, digits=False
                )  # Fallback
        elif field_res.field_id == "student_id":
            id_image_path = field_crop.relative_to(submission_dir).as_posix()
            if ollama_config:
                progress_callback("Extracting student ID with Ollama VLM...")
                try:
                    student_id = extract_field_text(image_path=field_crop, field_kind="sid", config=ollama_config)
                except Exception as e:
                    progress_callback(f"  Warning: Ollama VLM failed for student ID: {e}")
            if not student_id:
                student_id, id_confidence = _ocr_field_with_confidence(
                    field_crop, digits=True
                )  # Fallback

    # Extract question responses
    vlm_active = bool(not skip_ollama_qa and ollama_config)
    if vlm_active:
        vlm_count = sum(1 for q_res in submission_result.questions if q_res.output_path)
        progress.file_start(pdf_path.name, vlm_count)
    questions: list[StudentSubmissionQuestion] = []
    for q_res in submission_result.questions:
        response_text = ""
        response_confidence: float | None = None
        response_source: str | None = None
        if q_res.output_path:
            crop_path = Path(q_res.output_path)
            response_text, response_confidence, response_source = _transcribe_crop(
                crop_path,
                question_id=q_res.question_id,
                # `--no-ollama-qa` is expressed here rather than inside the
                # reader: with no vision model available the cascade is simply
                # the maths backend (if any) and then Tesseract.
                ollama_config=None if skip_ollama_qa else ollama_config,
                progress_callback=progress_callback,
                math_latex=llm_math,
                math_transcriber=math_transcriber,
                escalate_floor=escalate_floor,
            )
            if vlm_active:
                progress.response(pdf_path.name, q_res.question_id)

        questions.append(
            StudentSubmissionQuestion(
                question_id=q_res.question_id,
                prompt="",
                possible_points=None,
                response_image_path=Path(q_res.output_path).relative_to(submission_dir).as_posix() if q_res.output_path else None,
                response_text=response_text,
                source_segments=q_res.source_segments,
                response_confidence=response_confidence,
                response_source=response_source,
            )
        )

    doc = StudentSubmissionDocument(
        student_name=student_name,
        student_id=student_id,
        course_code=None,
        assignment_title="",
        assignment_version=submission_result.version_id,
        source_pdf=_source_pdf_relpath(pdf_path, submission_dir),
        student_score="",
        name_image_path=name_image_path,
        id_image_path=id_image_path,
        name_confidence=name_confidence,
        id_confidence=id_confidence,
        questions=questions,
    )
    doc = _apply_roster_identity(
        doc, roster, progress_callback, resolve_ambiguous, qr_student_id
    )
    doc = _apply_section(doc, default_section, section_by_id, progress_callback)

    markdown_content = render_student_submission_markdown(doc)
    markdown_path = submission_dir / f"{pdf_path.stem}.submission.md"
    markdown_path.write_text(markdown_content)

    return doc, markdown_path


# ==============================================================================
# MAIN CORE ENTRANCE ROUTINE
# ==============================================================================

def build_student_submission_document_unified(
    pdf_path: Path,
    output_dir: Path,
    ocr_config: OcrConfig | None,
    dpi: int,
    split_subquestions: bool,
    ollama_config: OllamaVisionConfig | None,
    skip_ollama_qa: bool,
    save_layout_debug: bool,
    blank_assignment_template: AssignmentTemplate | None,
    progress_callback: Callable[[str], None],
    roster: list[_RosterEntry] | None = None,
    update: bool = False,
    refresh_layout: bool = False,
    resolve_ambiguous: "AmbiguityResolver | None" = None,
    llm_responses: bool = False,
    llm_math: bool = False,
    math_backend: str = DEFAULT_MATH_BACKEND,
    escalate_floor: float | None = None,
    progress: ExtractionProgress | None = None,
    default_section: str | None = None,
    section_by_id: Mapping[str, str] | None = None,
    extra_pages: tuple[int, ...] | None = None,
    bubble_sheet_layout: "BubbleSheetLayout | None" = None,
    bubble_shuffled: bool = False,
    bubble_form_key: str | None = None,
) -> tuple[StudentSubmissionDocument, Path]:
    """Unified function to build a student submission document from a PDF.

    Chooses between bubble-sheet (OMR), blank-assignment-template extraction,
    text-layer only, or OCR-layout extraction.

    ``bubble_sheet_layout`` selects the OMR path and wins over every other
    mode: it is an explicit statement from the caller that these scans *are*
    generated answer sheets, and guessing otherwise (a bubble sheet has a text
    layer when born-digital, and plenty of rectangles for layout detection to
    find) would quietly produce a garbage extraction instead of an error.

    ``llm_responses`` requests vision-model transcription of each response into
    ``response_text`` for the blank-template path (the OCR path already does
    this unless ``skip_ollama_qa``); the text-layer path reads the typed answer
    straight from the text layer and needs no model. ``progress`` receives the
    per-response ticks the caller aggregates into a bar/ETA.

    ``llm_math`` asks the vision model to transcribe handwritten mathematics as
    LaTeX (``$...$``) rather than flattened plain text; it applies only to the
    vision-model paths (template + OCR), never the plain text layer.

    ``escalate_floor`` flips the transcription order from *model-first* to
    *escalate-on-doubt*: Tesseract reads every crop and a model is asked only
    about the readings below that confidence (or empty). It costs a fraction of
    the model calls on an exam whose answers are mostly short and legible, and
    it cannot lose an answer — see :func:`_transcribe_crop`. Applies to the
    template and OCR paths; the text layer is exact and never escalates.

    ``extra_pages`` overrides which source pages are recorded as extra student
    work; leave it ``None`` to take the splitter's sidecar or, failing that,
    infer them from page coverage (see ``_apply_extra_pages``).
    """
    progress = progress or ExtractionProgress()
    submission_dir = output_dir / pdf_path.stem
    submission_dir.mkdir(parents=True, exist_ok=True)

    # Resolved here rather than passed in, because extraction runs in a process
    # pool and a job's fields must stay picklable (`submissions/batch.py`) — a
    # backend crosses that boundary as a name and becomes a callable on the far
    # side, which also gives each worker its own cached model. Non-strict: a
    # missing backend degrades to the built-in path instead of killing a batch
    # halfway through, since the CLI already refused it at startup.
    math_transcriber = (
        resolve_math_transcriber(math_backend, strict=False) if llm_math else None
    )

    # Personalized-QR fast path: if the sheet carries an Opengrader QR id, it is
    # authoritative and skips all name-based roster matching. Read once here and
    # thread into every build path's identity resolution.
    qr_student_id = _read_qr_student_id(pdf_path)

    if bubble_sheet_layout is not None:
        document = _build_document_from_bubble_sheet(
            pdf_path=pdf_path,
            submission_dir=submission_dir,
            layout=bubble_sheet_layout,
            progress_callback=progress_callback,
            qr_student_id=qr_student_id,
            shuffled_batch=bubble_shuffled,
            form_key_override=bubble_form_key,
        )
        document = _apply_roster_identity(
            document, roster, progress_callback, resolve_ambiguous, qr_student_id
        )
        document = _apply_section(
            document, default_section, section_by_id, progress_callback
        )
        markdown_path = submission_dir / f"{pdf_path.stem}.submission.md"
        return _finalize_submission_document(
            document, pdf_path, markdown_path, extra_pages
        )

    if blank_assignment_template:
        progress_callback("Using blank assignment template for extraction...")
        document, name_field, id_field, name_image, id_image = (
            _build_document_from_assignment_template(
                pdf_path,
                submission_dir,
                blank_assignment_template,
                ollama_config=ollama_config,
                llm_responses=llm_responses,
                llm_math=llm_math,
                math_transcriber=math_transcriber,
                escalate_floor=escalate_floor,
                dpi=dpi,
                progress=progress,
                progress_callback=progress_callback,
            )
        )
        document = _apply_roster_identity(
            document, roster, progress_callback, resolve_ambiguous, qr_student_id
        )
        document = _apply_section(
            document, default_section, section_by_id, progress_callback
        )
        markdown_path = submission_dir / f"{pdf_path.stem}.submission.md"
        return _finalize_submission_document(
            document, pdf_path, markdown_path, extra_pages
        )

    # Check if PDF has a text layer for the text-layer-only strategy
    import fitz  # see _read_qr_code for why this is deferred

    doc = fitz.open(str(pdf_path))
    has_text_layer = False
    try:
        for page_index in range(len(doc)):
            if doc[page_index].get_text("text").strip():
                has_text_layer = True
                break
    finally:
        doc.close()

    if has_text_layer:
        document, markdown_path = _build_document_from_text_layer_only(
            pdf_path=pdf_path,
            submission_dir=submission_dir,
            progress_callback=progress_callback,
            roster=roster,
            resolve_ambiguous=resolve_ambiguous,
            default_section=default_section,
            section_by_id=section_by_id,
            qr_student_id=qr_student_id,
        )
    else:
        if ocr_config is None:
            raise ValueError("OCR configuration is required for scanned PDFs without a text layer.")
        progress_callback("Using OCR layout detection for extraction (no text layer detected)...")
        document, markdown_path = _build_document_from_ocr_result(
            pdf_path=pdf_path,
            submission_dir=submission_dir,
            ocr_config=ocr_config,
            dpi=dpi,
            split_subquestions=split_subquestions,
            ollama_config=ollama_config,
            skip_ollama_qa=skip_ollama_qa,
            save_layout_debug=save_layout_debug,
            progress_callback=progress_callback,
            roster=roster,
            resolve_ambiguous=resolve_ambiguous,
            progress=progress,
            default_section=default_section,
            section_by_id=section_by_id,
            llm_math=llm_math,
            math_transcriber=math_transcriber,
            escalate_floor=escalate_floor,
            qr_student_id=qr_student_id,
        )
    return _finalize_submission_document(
        document, pdf_path, markdown_path, extra_pages
    )