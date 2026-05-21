from __future__ import annotations

import json
import os
import re
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from difflib import SequenceMatcher
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Literal

import fitz
import pytesseract # Added for Tesseract fallback
import numpy as np
from PIL import Image

from opengrader.core.source_location import QuestionSourceSegment
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
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    render_student_submission_markdown,
)
from opengrader.core.ocr.extractor import process_submission_pdf, SubmissionResult, OcrResult
from opengrader.core.ocr import OcrConfig
from opengrader.core.ocr.ollama_vlm import (
    OllamaVisionConfig,
    # ensure_model_available, # This is not used in the processor, but in the CLI
    extract_field_text,
    extract_question_prompt_and_answer,
)

# ==============================================================================
# CONFIGURATION & REGEX PATTERNS
# ==============================================================================

# Patterns applied to fitz-extracted text from a PDF text layer (blank exam
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
class _FieldLocation:
    page: int
    bbox: tuple[float, float, float, float]  # normalized [x, y, w, h]


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
class _BlankExamTemplate:
    assignment_title: str
    name_field: _FieldLocation | None
    student_id_field: _FieldLocation | None
    questions: tuple[StudentSubmissionQuestion, ...]


@dataclass(frozen=True)
class _RosterEntry:
    student_id: str
    name: str


# ==============================================================================
# UTILITY AND PROCESSING FUNCTIONS
# ==============================================================================

def _load_roster(path: Path) -> list[_RosterEntry]:
    """Load a roster from a tab-separated file."""
    entries: list[_RosterEntry] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            entries.append(_RosterEntry(student_id=parts[0].strip(), name=parts[1].strip()))
    return entries


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


def _question_number(question: StudentSubmissionQuestion) -> int | None:
    match = re.fullmatch(r"q(\d+)", question.question_id)
    return int(match.group(1)) if match is not None else None


def _render_scan_all_pages(
    scan_pdf: Path,
    *,
    dpi: int = 300,
) -> dict[int, Image.Image]:
    """Render every page of a scan PDF as PIL images, keyed by 1-indexed page."""
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


def _detect_scan_layout(rendered_pages: dict[int, Image.Image]) -> _ScanLayout:
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
) -> _FieldLocation:
    """Convert a scan-detected label box into a normalized _FieldLocation.

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

    return _FieldLocation(
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
    for the questions whose prefixes were OCR'd on the scan and blank-exam
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
            "Pass --blank-exam PATH.pdf to apply a blank-exam template instead, "
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


def _extract_questions_from_text(lines: list[_TextLine]) -> list[StudentSubmissionQuestion]:
    """Extract prompts, points, and normalized bboxes from PDF text lines."""
    all_text = "\n".join(l.text for l in lines)
    questions: list[StudentSubmissionQuestion] = []
    
    for match in _QUESTION_INLINE_RE.finditer(all_text):
        qnum_str, pts_str, qtext = match.groups()
        qnum = int(qnum_str)
        
        # Find the physical line matching the start of this question to get bbox
        start_line = next((l for l in lines if re.search(rf"^{qnum_str}\s*\.", l.text.strip())), lines[0])
        bbox = _norm_bbox(start_line)

        questions.append(
            StudentSubmissionQuestion(
                question_id=f"q{qnum}",
                prompt=re.sub(r"\s+", " ", qtext).strip(),
                possible_points=float(pts_str),
                response_image_path=None,
                response_text="",
                source_segments=(QuestionSourceSegment(page_number=start_line.page, bbox_norm=bbox),),
            )
        )
    return questions


def _ocr_image(image_path: Path, *, psm: int = 6) -> str:
    """Perform Tesseract OCR on an image path."""
    try:
        with Image.open(image_path) as img:
            return pytesseract.image_to_string(img, config=f"--psm {psm}").strip()
    except Exception:
        return ""


def _ocr_image_digits(image_path: Path) -> str:
    """Perform Tesseract OCR on an image path, optimized for digits."""
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

def _load_blank_exam_template(pdf_path: Path) -> _BlankExamTemplate:
    """Extract the structural template from a blank exam PDF."""
    lines = _read_pdf_lines(pdf_path)
    _ensure_text_layer(lines, pdf_path)

    name_field = None
    id_field = None
    for line in lines:
        if _NAME_LABEL_RE.match(line.text):
            name_field = _FieldLocation(line.page, _norm_bbox(line))
        elif _ID_LABEL_RE.match(line.text):
            id_field = _FieldLocation(line.page, _norm_bbox(line))

    return _BlankExamTemplate(
        assignment_title=_assignment_title_from_lines(lines),
        name_field=name_field,
        student_id_field=id_field,
        questions=tuple(_extract_questions_from_text(lines)),
    )

def _select_field(
    label: _ScanLabelBox | None,
    right_boundary: _ScanLabelBox | None,
    template_field: _FieldLocation | None,
    line_boxes_by_page: dict[int, list[tuple[float, float, float]]],
    rendered_pages: dict[int, Image.Image],
    tight_top: bool = False,
) -> _FieldLocation | None:
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
    field_loc: _FieldLocation,
    output_dir: Path,
    prefix: str,
) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    crop = crop_norm(image, field_loc.bbox)
    filename = f"{prefix}.png"
    crop.save(output_dir / filename)
    return str(Path(output_dir.name) / filename)

def _scan_offset_by_page(
    template: _BlankExamTemplate,
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
    template: _BlankExamTemplate,
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

def _build_document_from_template(
    pdf_path: Path,
    submission_dir: Path,
    template: _BlankExamTemplate,
) -> tuple[
    StudentSubmissionDocument,
    _FieldLocation | None,
    _FieldLocation | None,
    str | None,
    str | None,
]:
    """Build a student .submission.md skeleton using the blank-exam layout.

    Renders every page of the scan, detects on-paper question and Name/ID
    label positions via pytesseract (alignment only — no value-extraction
    OCR), saves Name/ID field crops to ``field_crops/`` and per-question
    response crops to ``responses/``, and assembles a submission document
    with empty ``name`` / ``student_id`` / per-question Response text. A
    downstream OCR pass is expected to populate those fields.
    """
    rendered_pages = _render_scan_all_pages(pdf_path)
    scan_layout = _detect_scan_layout(rendered_pages)

    field_crops_dir = submission_dir / "identifiers"
    responses_dir = submission_dir / "responses"

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
) -> tuple[StudentSubmissionDocument, Path]:
    """Build a document directly from a text-layer student PDF (no scan crops)."""
    progress_callback("Extracting from text layer...")

    lines = _read_pdf_lines(pdf_path)
    _ensure_text_layer(lines, pdf_path)

    all_text = "\n".join(line.text for line in lines)

    raw_id = id_match.group(1) if (id_match := _ID_VALUE_RE.search(all_text)) else ""
    raw_name = name_match.group(1).strip() if (name_match := _NAME_VALUE_RE.search(all_text)) else ""

    final_name, final_id = raw_name, raw_id
    if roster and (match := _match_roster(raw_id, raw_name, roster)) and match[1] > 0.6:
        final_name, final_id = match[0].name, match[0].student_id

    document = StudentSubmissionDocument(
        student_name=final_name,
        student_id=final_id,
        course_code=None,
        assignment_title=_assignment_title_from_lines(lines),
        assignment_version=None,
        source_pdf=_source_pdf_relpath(pdf_path, submission_dir),
        student_score="",
        questions=_extract_questions_from_text(lines),
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
) -> tuple[StudentSubmissionDocument, Path]:
    """Processes a single student PDF using OCR and VLM to extract submission data."""
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
                except Exception as e:
                    progress_callback(f"  Warning: Ollama VLM failed for name: {e}")
            if not student_name:
                student_name = _ocr_image(field_crop, psm=7)  # Fallback
        elif field_res.field_id == "student_id":
            id_image_path = field_crop.relative_to(submission_dir).as_posix()
            if ollama_config:
                progress_callback("Extracting student ID with Ollama VLM...")
                try:
                    student_id = extract_field_text(image_path=field_crop, field_kind="sid", config=ollama_config)
                except Exception as e:
                    progress_callback(f"  Warning: Ollama VLM failed for student ID: {e}")
            if not student_id:
                student_id = _ocr_image_digits(field_crop)  # Fallback

    if roster and (match := _match_roster(student_id, student_name, roster)) and match[1] > 0.5:
        student_name, student_id = match[0].name, match[0].student_id

    # Extract question responses
    questions: list[StudentSubmissionQuestion] = []
    for q_res in submission_result.questions:
        response_text = ""
        if q_res.output_path:
            crop_path = Path(q_res.output_path)
            if not skip_ollama_qa and ollama_config:
                progress_callback(f"Extracting response for {q_res.question_id} with Ollama VLM...")
                try:
                    _prompt, answer = extract_question_prompt_and_answer(
                        image_path=crop_path,
                        config=ollama_config,
                    )
                    response_text = answer or ""
                except Exception as e:
                    progress_callback(f"  Warning: Ollama VLM failed for {q_res.question_id}: {e}")
        
            if not response_text:
                response_text = _ocr_image(crop_path, psm=6)

        questions.append(
            StudentSubmissionQuestion(
                question_id=q_res.question_id,
                prompt="", 
                possible_points=None, 
                response_image_path=Path(q_res.output_path).relative_to(submission_dir).as_posix() if q_res.output_path else None,
                response_text=response_text,
                source_segments=q_res.source_segments,
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
        questions=questions,
    )

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
    blank_exam_template: _BlankExamTemplate | None,
    progress_callback: Callable[[str], None],
    roster: list[_RosterEntry] | None = None,
    update: bool = False,
    refresh_layout: bool = False,
) -> tuple[StudentSubmissionDocument, Path]:
    """Unified function to build a student submission document from a PDF.

    Chooses between text-layer extraction (with blank exam template),
    text-layer only, or OCR-layout extraction.
    """
    submission_dir = output_dir / pdf_path.stem
    submission_dir.mkdir(parents=True, exist_ok=True)

    if blank_exam_template:
        progress_callback("Using blank exam template for extraction...")
        document, name_field, id_field, name_image, id_image = (
            _build_document_from_template(pdf_path, submission_dir, blank_exam_template)
        )
        markdown_path = submission_dir / f"{pdf_path.stem}.submission.md"
        markdown_path.write_text(render_student_submission_markdown(document))
        return document, markdown_path
    
    # Check if PDF has a text layer for the text-layer-only strategy
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
        return _build_document_from_text_layer_only(
            pdf_path=pdf_path,
            submission_dir=submission_dir,
            progress_callback=progress_callback,
            roster=roster,
        )
    else:
        if ocr_config is None:
            raise ValueError("OCR configuration is required for scanned PDFs without a text layer.")
        progress_callback("Using OCR layout detection for extraction (no text layer detected)...")
        return _build_document_from_ocr_result(
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
        )