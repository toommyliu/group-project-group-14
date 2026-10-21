from __future__ import annotations

import os
import re
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
)
from .student_file import (
    AssignmentTitleMetadata,
    IdentityCandidate,
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    parse_assignment_metadata_from_title,
    render_student_submission_markdown,
)
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

if TYPE_CHECKING:  # pragma: no cover
    from opengrader.core.bubble_sheet import BubbleSheetLayout
    from opengrader.core.pages.catalog import PageCatalog
    from opengrader.core.pages.extract import CatalogCrops
    from opengrader.core.ocr import OcrConfig
    from opengrader.core.ocr.vision_reader import VisionReader
from opengrader.core.ocr.math_ocr import (
    DEFAULT_MATH_BACKEND,
    MathTranscriber,
    resolve_math_transcriber,
)


@dataclass
class ExtractionProgress:
    on_file_start: Callable[[str, int], None] | None = None
    on_response: Callable[[str, str], None] | None = None

    def file_start(self, pdf_name: str, response_count: int) -> None:
        if self.on_file_start is not None:
            self.on_file_start(pdf_name, response_count)

    def response(self, pdf_name: str, question_id: str) -> None:
        if self.on_response is not None:
            self.on_response(pdf_name, question_id)


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

_SCAN_NAME_WORD_RE = re.compile(r"^name[:.,]?$", re.IGNORECASE)
_SCAN_ID_WORD_RE = re.compile(r"^id[:.,]?$", re.IGNORECASE)
_SCAN_STUDENT_WORD_RE = re.compile(r"^student[:.,]?$", re.IGNORECASE)
_SCAN_PUNCT_WORD_RE = re.compile(r"^[:.,;]+$")

_FIELD_BOTTOM_PAD_POINTS = 10.0  # About 3.5 mm below the label baseline.


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
    x0: float  # Pixel coordinates in the rendered scan.
    y0: float
    x1: float
    y1: float
    page_w: int
    page_h: int


@dataclass(frozen=True)
class _ScanLayout:
    question_positions: dict[int, tuple[int, float]]  # qnum -> (page, y_top_norm).
    total_points_positions: dict[int, float]  # page -> y_top_norm.
    name_label: _ScanLabelBox | None
    id_label: _ScanLabelBox | None
    # Per-page OCR lines (y_top, x0, x1), sorted by y_top.
    line_boxes_by_page: dict[int, list[tuple[float, float, float]]]


@dataclass(frozen=True)
class _RosterEntry:
    student_id: str
    name: str


def _load_roster(path: Path) -> list[_RosterEntry]:
    return [
        _RosterEntry(student_id=student.student_id, name=student.name)
        for student in load_roster_students(path)
    ]


def _match_roster(student_id: str, name: str, roster: list[_RosterEntry]) -> tuple[_RosterEntry, float] | None:
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


def _has_usable_id(student_id: str) -> bool:
    return len(re.sub(r"\D", "", student_id or "")) >= 4


# Form keys make QR symbols denser and harder to read at low DPI.
_QR_READ_DPIS = (150, 300)


def _read_qr_code(pdf_path: Path):  # noqa: ANN202
    from opengrader.core.ocr.qr_reader import read_code_from_image

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
    code = _read_qr_code(pdf_path)
    if code is not None and code.student_id:
        return code.student_id
    return None


def _read_qr_metadata(pdf_path: Path) -> tuple[str | None, str | None, str | None]:
    code = _read_qr_code(pdf_path)
    if code is None:
        return None, None, None
    return (code.student_id or None), (code.version or None), (code.form or None)


def _layout_for_form(
    layout: "BubbleSheetLayout", form_key: str | None
) -> "BubbleSheetLayout":
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
    from opengrader.core.submissions.bubble_extraction import build_bubble_sheet_document

    _qr_id, qr_version, form_key = _read_qr_metadata(pdf_path)
    if form_key_override:
        form_key = form_key_override
    if form_key:
        progress_callback(f"Scrambled sheet: form {form_key}")
    elif shuffled_batch:
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
        return replace(
            document,
            id_candidates=tuple(
                IdentityCandidate(student_id=entry.student_id, name=entry.name)
                for entry in resolution.candidates
            ),
        )
    return document


UNASSIGNED_SECTION = "unassigned"


def _apply_section(
    document: StudentSubmissionDocument,
    default_section: str | None,
    section_by_id: Mapping[str, str] | None,
    progress_callback: Callable[[str], None],
) -> StudentSubmissionDocument:
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
    import fitz

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
    """Explicit pages take precedence over the sidecar, then coverage inference. An empty
    explicit or sidecar list means checked with no extra pages.
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
    from .splitter import read_scan_warnings

    warnings = (*read_scan_warnings(pdf_path), *document.scan_warnings)
    document = replace(document, scan_warnings=tuple(dict.fromkeys(warnings)))
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
    import fitz

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
    import fitz

    doc = fitz.open(str(pdf_path))
    try:
        lines: list[_TextLine] = []
        for page_index, page in enumerate(doc, start=1):
            pw, ph = page.rect.width, page.rect.height
            page_dict = page.get_text("dict")
            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:
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


_ANSWER_MARKER_RE = re.compile(
    r"(?im)^\s*(?:answer|ans|response|solution)\b\s*[:.\-]?\s*"
)


def _split_prompt_and_response(block: str) -> tuple[str, str]:
    marker = _ANSWER_MARKER_RE.search(block)
    if marker is not None:
        return block[: marker.start()], block[marker.end() :]
    paragraphs = re.split(r"\n\s*\n", block, maxsplit=1)
    if len(paragraphs) == 2:
        return paragraphs[0], paragraphs[1]
    return block, ""


def _extract_questions_from_text(lines: list[_TextLine]) -> list[StudentSubmissionQuestion]:
    all_text = "\n".join(l.text for l in lines)
    questions: list[StudentSubmissionQuestion] = []

    for match in _QUESTION_INLINE_RE.finditer(all_text):
        qnum_str, pts_str, qtext = match.groups()
        qnum = int(qnum_str)

        prompt_text, response_text = _split_prompt_and_response(qtext)

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
                response_confidence=100.0 if cleaned_response else None,
                response_source=SOURCE_TEXT_LAYER if cleaned_response else None,
            )
        )
    return questions


def _ocr_image_with_confidence(
    image_path: Path, *, psm: int = 6
) -> tuple[str, float | None]:
    """Confidence is the mean word confidence on a 0-100 scale."""
    import pytesseract

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
        if len(digits_only) > len(best_text):
            best_text = digits_only
            best_confidence = confidence
    return best_text, best_confidence


def _ocr_data_with_confidence(
    image_path: Path, config: str
) -> tuple[str, float | None]:
    import pytesseract

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


def _template_response_field(
    label: _TextLine,
    *,
    next_top: float | None,
) -> FieldLocation:
    line_height = max(1.0, label.y1 - label.y0)
    x0 = min(label.page_w * 0.92, label.x1 + label.page_w * 0.01)
    y0 = max(0.0, label.y0 - line_height * 1.8)
    fallback_bottom = label.y1 + line_height * 3.0
    y1 = (
        min(fallback_bottom, next_top - line_height * 1.5)
        if next_top
        else fallback_bottom
    )
    y1 = max(y1, label.y1 + line_height)
    return FieldLocation(
        page=label.page,
        bbox=(
            round(x0 / label.page_w, 4),
            round(y0 / label.page_h, 4),
            round(max(0.0, label.page_w * 0.92 - x0) / label.page_w, 4),
            round(max(0.0, y1 - y0) / label.page_h, 4),
        ),
    )


_TEMPLATE_SUBQUESTION_RE = re.compile(
    r"^\s*([a-z])\s*[\).]\s*" + POINTS_SPEC + r"\s*(.*)$",
    re.IGNORECASE,
)


def _extract_blank_template_questions(
    lines: list[_TextLine],
) -> tuple[StudentSubmissionQuestion, ...]:
    candidates: list[tuple[int, str, _TextLine, float, str]] = []
    current_parent: int | None = None
    for line in lines:
        top_level = QUESTION_PREFIX_RE.match(line.text)
        if top_level is not None:
            number_text, points_text, prompt = top_level.groups()
            current_parent = int(number_text)
            candidates.append(
                (current_parent, "", line, float(points_text), prompt.strip())
            )
            continue
        subquestion = _TEMPLATE_SUBQUESTION_RE.match(line.text)
        if subquestion is None or current_parent is None:
            continue
        suffix, points_text, prompt = subquestion.groups()
        candidates.append(
            (
                current_parent,
                suffix.lower(),
                line,
                float(points_text),
                prompt.strip(),
            )
        )

    parents_with_parts = {
        number for number, suffix, _line, _points, _prompt in candidates if suffix
    }
    questions: list[StudentSubmissionQuestion] = []
    for index, (number, suffix, line, points, prompt) in enumerate(candidates):
        if not suffix and number in parents_with_parts:
            continue
        next_line = next(
            (
                candidate[2]
                for candidate in candidates[index + 1 :]
                if candidate[2].page == line.page
                and candidate[2].y0 > line.y0
            ),
            None,
        )
        y_top = max(0.0, line.y0 / line.page_h - 0.003)
        y_bottom = (
            min(0.975, next_line.y0 / line.page_h - 0.005)
            if next_line is not None
            else 0.975
        )
        questions.append(
            StudentSubmissionQuestion(
                question_id=f"q{number}{suffix}",
                prompt=prompt,
                possible_points=points,
                response_image_path=None,
                response_text="",
                source_segments=(
                    QuestionSourceSegment(
                        page_number=line.page,
                        bbox_norm=(
                            0.08,
                            round(y_top, 4),
                            0.84,
                            round(max(0.005, y_bottom - y_top), 4),
                        ),
                    ),
                ),
            )
        )
    return tuple(questions)


def _load_blank_assignment_template(pdf_path: Path) -> AssignmentTemplate:
    lines = _read_pdf_lines(pdf_path)
    _ensure_text_layer(lines, pdf_path)

    import fitz

    with fitz.open(str(pdf_path)) as doc:
        total_pages = len(doc)

    name_label: _TextLine | None = None
    id_label: _TextLine | None = None
    for line in lines:
        if _NAME_LABEL_RE.match(line.text):
            name_label = line
        elif _ID_LABEL_RE.match(line.text):
            id_label = line

    name_next_top = (
        id_label.y0
        if name_label is not None
        and id_label is not None
        and id_label.page == name_label.page
        and id_label.y0 > name_label.y0
        else None
    )
    id_next_top = None
    if id_label is not None:
        id_next_top = next(
            (
                line.y0
                for line in sorted(lines, key=lambda candidate: candidate.y0)
                if line.page == id_label.page
                and line.y0 > id_label.y0
                and re.match(r"^\s*\d+\s*\.", line.text) is not None
            ),
            None,
        )
    name_field = (
        _template_response_field(name_label, next_top=name_next_top)
        if name_label is not None
        else None
    )
    id_field = (
        _template_response_field(id_label, next_top=id_next_top)
        if id_label is not None
        else None
    )

    blank_questions = _extract_blank_template_questions(lines)

    return AssignmentTemplate(
        assignment_title=_assignment_title_from_lines(lines),
        name_field=name_field,
        student_id_field=id_field,
        questions=blank_questions,
        total_pages=total_pages,
        exact_regions=True,
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
        # Black padding would look like ink to OCR and vision models.
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


def source_pdf_relpath(pdf_path: Path, submission_dir: Path) -> str:
    try:
        return os.path.relpath(pdf_path, submission_dir)
    except ValueError:
        return str(pdf_path)

def _assignment_title_from_lines(lines: list[_TextLine]) -> str:
    return lines[0].text.strip() if lines else "Unknown Assignment"

def _fill_responses_with_vlm(
    questions: list[StudentSubmissionQuestion],
    submission_dir: Path,
    vision_reader: "VisionReader",
    *,
    pdf_name: str,
    progress: ExtractionProgress,
    progress_callback: Callable[[str], None],
    math_latex: bool = False,
    math_transcriber: "MathTranscriber | None" = None,
    escalate_floor: float | None = None,
) -> list[StudentSubmissionQuestion]:
    """Default reader order is math model, vision model, then OCR; escalate_floor runs OCR
    first. A successful blank model reading remains blank.
    """
    filled: list[StudentSubmissionQuestion] = []
    for question in questions:
        response_text = question.response_text
        response_confidence = question.response_confidence
        response_source = question.response_source
        response_provider = question.response_provider
        if not response_text and question.response_image_path:
            crop_path = submission_dir / question.response_image_path
            reading = _transcribe_crop(
                crop_path,
                question_id=question.question_id,
                vision_reader=vision_reader,
                progress_callback=progress_callback,
                math_latex=math_latex,
                math_transcriber=math_transcriber,
                escalate_floor=escalate_floor,
            )
            response_text, response_confidence, response_source, response_provider = reading
            progress.response(pdf_name, question.question_id)
        filled.append(
            replace(
                question,
                response_text=response_text,
                response_confidence=response_confidence,
                response_source=response_source,
                response_provider=response_provider,
            )
        )
    return filled


def _transcribe_crop(
    crop_path: Path,
    *,
    question_id: str,
    vision_reader: "VisionReader | None",
    progress_callback: Callable[[str], None],
    math_latex: bool = False,
    math_transcriber: "MathTranscriber | None" = None,
    escalate_floor: float | None = None,
) -> tuple[str, float | None, str | None, str | None]:
    def _read_with_ocr() -> tuple[str, float | None, str | None]:
        text, confidence = _ocr_image_with_confidence(crop_path, psm=6)
        return (text, confidence, SOURCE_TESSERACT if text.strip() else None)

    def _read_with_math() -> tuple[str, float | None, str | None] | None:
        if math_transcriber is None:
            return None
        progress_callback(f"Transcribing {question_id} as LaTeX...")
        text = math_transcriber(crop_path) or ""
        if not text:
            return None
        return (text, None, SOURCE_MATH_MODEL)

    def _read_with_vlm() -> tuple[str, float | None, str | None] | None:
        if vision_reader is None:
            return None
        progress_callback(f"Transcribing {question_id} with the vision model...")
        try:
            answer = vision_reader.extract_handwriting_only(
                image_path=crop_path, math_latex=math_latex,
            )
        except Exception as exc:
            # Any failure from the vision call (timeout, auth error, a blocked
            # cloud call, ...) is caught here so a network hiccup on one crop
            # never aborts the rest of the cohort — the caller falls back to
            # OCR below and forces its confidence down so this answer still
            # lands in the review queue.
            progress_callback(f"  Warning: Vision model failed for {question_id}: {exc}")
            return None
        text = answer or ""
        confidence = (
            0.0
            if "[unclear]" in text.casefold()
            else None
        )
        return (text, confidence, SOURCE_VISION_MODEL)

    ocr_reading = None
    if escalate_floor is not None:
        ocr_reading = _read_with_ocr()
        text, confidence, _source = ocr_reading
        if not should_escalate(text, confidence, floor=escalate_floor):
            return (*ocr_reading, None)
        progress_callback(
            f"  {question_id}: {_escalation_reason(text, confidence)}, "
            "re-reading with a model..."
        )
    reading = _read_with_math() or _read_with_vlm()
    if reading is not None:
        text, confidence, source = reading
        provider = (
            getattr(vision_reader, "provider", None)
            if source == SOURCE_VISION_MODEL
            else None
        )
        return text, confidence, source, provider
    text, confidence, source = ocr_reading or _read_with_ocr()
    # Printed text can score highly in Tesseract after a handwriting reader fails.
    return text, 0.0 if vision_reader is not None else confidence, source, None


def _escalation_reason(text: str, confidence: float | None) -> str:
    if not text.strip():
        return "OCR read nothing"
    return f"OCR confidence {confidence:.0f}%" if confidence is not None else "OCR unsure"


def _build_document_from_assignment_template(
    pdf_path: Path,
    submission_dir: Path,
    template: AssignmentTemplate,
    *,
    vision_reader: "VisionReader | None" = None,
    llm_responses: bool = False,
    llm_math: bool = False,
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
    progress = progress or ExtractionProgress()
    rendered_pages = _render_scan_all_pages(pdf_path, dpi=dpi)

    field_crops_dir = submission_dir / "identifiers"
    responses_dir = submission_dir / "responses"

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
            name_image = _save_field_crop(
                page_image, name_field, field_crops_dir, "name"
            )

    id_image: str | None = None
    if id_field is not None:
        page_image = rendered_pages.get(id_field.page)
        if page_image is not None:
            id_image = _save_field_crop(
                page_image, id_field, field_crops_dir, "id"
            )

    template_questions = list(template.questions)

    if scan_layout is None:
        questions = [
            _question_with_mapped_crop(question, rendered_pages, responses_dir)
            for question in template_questions
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
            for question in template_questions
        ]

    if llm_responses and vision_reader is not None:
        crop_count = sum(1 for question in questions if question.response_image_path)
        progress.file_start(pdf_path.name, crop_count)
        questions = _fill_responses_with_vlm(
            questions,
            submission_dir,
            vision_reader,
            pdf_name=pdf_path.name,
            progress=progress,
            progress_callback=progress_callback,
            math_latex=llm_math,
            math_transcriber=math_transcriber,
            escalate_floor=escalate_floor,
        )

    student_name = ""
    name_confidence: float | None = None
    if name_image:
        name_path = submission_dir / name_image
        if vision_reader is not None:
            try:
                student_name = vision_reader.extract_field_text(
                    image_path=name_path,
                    field_kind="name",
                )
            except Exception as exc:
                progress_callback(f"  Warning: could not read student name: {exc}")
        if not student_name:
            student_name, name_confidence = _ocr_field_with_confidence(
                name_path, digits=False
            )

    student_id = ""
    id_confidence: float | None = None
    if id_image:
        id_path = submission_dir / id_image
        if vision_reader is not None:
            try:
                student_id = vision_reader.extract_field_text(
                    image_path=id_path,
                    field_kind="sid",
                )
            except Exception as exc:
                progress_callback(f"  Warning: could not read student ID: {exc}")
        if not student_id:
            student_id, id_confidence = _ocr_field_with_confidence(
                id_path, digits=True
            )

    title_metadata = parse_assignment_metadata_from_title(template.assignment_title)
    document = StudentSubmissionDocument(
        student_name=student_name,
        student_id=student_id,
        course_code=title_metadata.course_code,
        assignment_title=title_metadata.assignment_title,
        assignment_version=title_metadata.assignment_version,
        source_pdf=source_pdf_relpath(pdf_path, submission_dir),
        student_score="",
        name_image_path=name_image,
        id_image_path=id_image,
        name_confidence=name_confidence,
        id_confidence=id_confidence,
        questions=questions,
    )
    return document, name_field, id_field, name_image, id_image


def _build_document_from_page_catalog(
    pdf_path: Path,
    submission_dir: Path,
    catalog: "PageCatalog",
    *,
    vision_reader: "VisionReader | None" = None,
    llm_responses: bool = False,
    llm_math: bool = False,
    math_transcriber: "MathTranscriber | None" = None,
    escalate_floor: float | None = None,
    dpi: int = 300,
    debug: bool = False,
    progress: ExtractionProgress | None = None,
    progress_callback: Callable[[str], None] = lambda _message: None,
) -> StudentSubmissionDocument:
    from opengrader.core.pages.extract import crop_submission

    progress = progress or ExtractionProgress()
    crops = crop_submission(pdf_path, submission_dir, catalog, dpi=dpi, debug=debug)

    transcribe = llm_responses or escalate_floor is not None
    reader = vision_reader if transcribe else None
    if reader is not None:
        progress.file_start(pdf_path.name, sum(1 for q in crops.questions if q.response_image_path))
    questions = []
    for question in crops.questions:
        if transcribe and question.response_image_path:
            text, confidence, source, provider = _transcribe_crop(
                submission_dir / question.response_image_path,
                question_id=question.question_id,
                vision_reader=reader,
                progress_callback=progress_callback,
                math_latex=llm_math,
                math_transcriber=math_transcriber,
                escalate_floor=escalate_floor,
            )
            question = replace(
                question,
                response_text=text,
                response_confidence=confidence,
                response_source=source,
                response_provider=provider,
            )
            if reader is not None:
                progress.response(pdf_path.name, question.question_id)
        questions.append(question)

    student_name, name_confidence = _read_identity_field(
        submission_dir, crops.name_image, "name", vision_reader, progress_callback,
        crops.print_boxes.get("name", ()), crops.written_width.get("name", 1.0),
    )
    student_id, id_confidence = _read_identity_field(
        submission_dir, crops.id_image, "sid", vision_reader, progress_callback,
        crops.print_boxes.get("id", ()), crops.written_width.get("id", 1.0),
    )
    title = _read_title(submission_dir, crops, progress_callback)
    return StudentSubmissionDocument(
        student_name=student_name,
        student_id=student_id,
        course_code=title.course_code,
        assignment_title=title.assignment_title,
        assignment_version=title.assignment_version,
        source_pdf=source_pdf_relpath(pdf_path, submission_dir),
        student_score="",
        name_image_path=crops.name_image,
        id_image_path=crops.id_image,
        name_confidence=name_confidence,
        id_confidence=id_confidence,
        questions=questions,
        scan_warnings=tuple(crops.warnings),
    )


def _read_title(
    submission_dir: Path, crops: "CatalogCrops", progress_callback: Callable[[str], None]
) -> AssignmentTitleMetadata:
    from PIL import Image

    from opengrader.core.pages.printed import read_title

    usual = parse_assignment_metadata_from_title(crops.title_text)
    if not crops.title_image:
        return usual
    try:
        with Image.open(submission_dir / crops.title_image) as image:
            text = read_title(image)
    except Exception as exc:
        progress_callback(f"  Warning: could not read the title ({exc}); using the page's usual title.")
        return usual
    own = parse_assignment_metadata_from_title(text)
    if not crops.title_text or (own.course_code, own.assignment_title) == (usual.course_code, usual.assignment_title):
        return own
    return usual


def _is_scan(pdf_path: Path) -> bool:
    from opengrader.core.pages.catalog import is_scan

    return is_scan(pdf_path)


def _read_identity_field(
    submission_dir: Path,
    image: str | None,
    field_kind: str,
    vision_reader: "VisionReader | None",
    progress_callback: Callable[[str], None],
    printed: tuple[tuple[float, float, float, float], ...] = (),
    written_width: float = 1.0,
) -> tuple[str, float | None]:
    from PIL import Image

    from opengrader.core.pages.printed import read_field

    if not image:
        return "", None
    path = submission_dir / image
    if vision_reader is not None:
        try:
            text = vision_reader.extract_field_text(image_path=path, field_kind=field_kind)
            if text:
                return text, None
        except Exception as exc:
            progress_callback(f"  Warning: could not read the {field_kind}: {exc}")
    with Image.open(path) as crop:
        return read_field(crop, field_kind, printed, written_width)


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
        source_pdf=source_pdf_relpath(pdf_path, submission_dir),
        student_score="",
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
    vision_reader: "VisionReader | None",
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
    if not submission_result.questions:
        raise ValueError("No question regions were detected. Check the scan orientation and layout settings before retrying.")

    student_name = ""
    student_id = ""
    name_image_path = None
    id_image_path = None
    name_confidence: float | None = None
    id_confidence: float | None = None

    for field_res in submission_result.fields:
        if not field_res.crop_path:
            continue
        field_crop = Path(field_res.crop_path)

        if field_res.field_id == "student_name":
            name_image_path = field_crop.relative_to(submission_dir).as_posix()
            if vision_reader:
                progress_callback("Extracting name with Ollama VLM...")
                try:
                    student_name = vision_reader.extract_field_text(image_path=field_crop, field_kind="name")
                except Exception as e:
                    progress_callback(f"  Warning: Ollama VLM failed for name: {e}")
            if not student_name:
                student_name, name_confidence = _ocr_field_with_confidence(
                    field_crop, digits=False
                )
        elif field_res.field_id == "student_id":
            id_image_path = field_crop.relative_to(submission_dir).as_posix()
            if vision_reader:
                progress_callback("Extracting student ID with Ollama VLM...")
                try:
                    student_id = vision_reader.extract_field_text(image_path=field_crop, field_kind="sid")
                except Exception as e:
                    progress_callback(f"  Warning: Ollama VLM failed for student ID: {e}")
            if not student_id:
                student_id, id_confidence = _ocr_field_with_confidence(
                    field_crop, digits=True
                )

    vlm_active = bool(not skip_ollama_qa and vision_reader)
    if vlm_active:
        vlm_count = sum(1 for q_res in submission_result.questions if q_res.output_path)
        progress.file_start(pdf_path.name, vlm_count)
    questions: list[StudentSubmissionQuestion] = []
    for q_res in submission_result.questions:
        response_text = ""
        response_confidence: float | None = None
        response_source: str | None = None
        response_provider: str | None = None
        if q_res.output_path:
            crop_path = Path(q_res.output_path)
            response_text, response_confidence, response_source, response_provider = (
                _transcribe_crop(
                    crop_path,
                    question_id=q_res.question_id,
                    vision_reader=None if skip_ollama_qa else vision_reader,
                    progress_callback=progress_callback,
                    math_latex=llm_math,
                    math_transcriber=math_transcriber,
                    escalate_floor=escalate_floor,
                )
            )
            if vlm_active:
                progress.response(pdf_path.name, q_res.question_id)

        questions.append(
            StudentSubmissionQuestion(
                question_id=q_res.question_id,
                prompt="",
                possible_points=None,
                question_type=q_res.question_type,
                response_image_path=Path(q_res.output_path).relative_to(submission_dir).as_posix() if q_res.output_path else None,
                response_text=response_text,
                source_segments=q_res.source_segments,
                response_confidence=response_confidence,
                response_source=response_source,
                response_provider=response_provider,
            )
        )

    doc = StudentSubmissionDocument(
        student_name=student_name,
        student_id=student_id,
        course_code=None,
        assignment_title="",
        assignment_version=submission_result.version_id,
        source_pdf=source_pdf_relpath(pdf_path, submission_dir),
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


def build_student_submission_document_unified(
    pdf_path: Path,
    output_dir: Path,
    ocr_config: OcrConfig | None,
    dpi: int,
    split_subquestions: bool,
    vision_reader: "VisionReader | None",
    skip_ollama_qa: bool,
    save_layout_debug: bool,
    blank_assignment_template: AssignmentTemplate | None,
    progress_callback: Callable[[str], None],
    roster: list[_RosterEntry] | None = None,
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
    page_catalog: "PageCatalog | None" = None,
) -> tuple[StudentSubmissionDocument, Path]:
    """Route precedence: OMR, template, page catalog, text layer, OCR layout. Text layers
    bypass models; escalate_floor puts OCR before models.
    """
    progress = progress or ExtractionProgress()
    submission_dir = output_dir / pdf_path.stem
    submission_dir.mkdir(parents=True, exist_ok=True)

    math_transcriber = (
        resolve_math_transcriber(math_backend, strict=False) if llm_math else None
    )

    if bubble_sheet_layout is not None:
        qr_student_id = _read_qr_student_id(pdf_path)
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
        qr_student_id = _read_qr_student_id(pdf_path)
        progress_callback("Using blank assignment template for extraction...")
        document, name_field, id_field, name_image, id_image = (
            _build_document_from_assignment_template(
                pdf_path,
                submission_dir,
                blank_assignment_template,
                vision_reader=vision_reader,
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

    import fitz

    doc = fitz.open(str(pdf_path))
    has_text_layer = False
    try:
        for page_index in range(len(doc)):
            if doc[page_index].get_text("text").strip():
                has_text_layer = True
                break
    finally:
        doc.close()

    scanned_with_catalog = page_catalog is not None and _is_scan(pdf_path)
    qr_student_id = None if scanned_with_catalog else _read_qr_student_id(pdf_path)
    if has_text_layer and not scanned_with_catalog:
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
    elif scanned_with_catalog:
        progress_callback("Cropping with the assignment's page catalog...")
        document = _build_document_from_page_catalog(
            pdf_path,
            submission_dir,
            page_catalog,
            vision_reader=vision_reader,
            llm_responses=llm_responses,
            llm_math=llm_math,
            math_transcriber=math_transcriber,
            escalate_floor=escalate_floor,
            dpi=dpi,
            debug=save_layout_debug,
            progress=progress,
            progress_callback=progress_callback,
        )
        document = _apply_roster_identity(
            document, roster, progress_callback, resolve_ambiguous, qr_student_id
        )
        document = _apply_section(
            document, default_section, section_by_id, progress_callback
        )
        markdown_path = submission_dir / f"{pdf_path.stem}.submission.md"
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
            vision_reader=vision_reader,
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
