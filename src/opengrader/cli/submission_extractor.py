from __future__ import annotations

import json
import os
import re
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from dataclasses import dataclass, replace
from pathlib import Path

import fitz
import numpy as np
import pytesseract
from PIL import Image

from opengrader.core.ocr.extractor import crop_region, render_page_to_image
from opengrader.core.source_location import QuestionSourceSegment
from opengrader.core.submissions import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    render_student_submission_markdown,
)

"""
Extract per-student .submission.md files from PDFs.

Without --blank-exam: each --input PDF must have a real text layer. fitz
text extraction + regex pulls out the assignment title, student name/ID,
and question prompts/points. The command exits with an error if the PDF
has no text layer.

With --blank-exam PATH.pdf: PATH.pdf is the blank version of the exam
(same layout as the scans, no answers, real text layer required). Its
question prompts, points, answer-region bboxes, and name/ID field bboxes
are extracted once and applied to every scan in --input. The scans
themselves do not need a text layer; each scan page is rendered and run
through pytesseract to find the actual on-paper positions of question
prefixes (``N. (M points) ...``) and Name/ID labels, so per-question
response crops land on the real student-writing region even when the scan
sits at a slightly different vertical position than the blank exam.

Per-question response crops are saved under ``responses/``, and Name/ID
field crops are saved under ``field_crops/`` inside each submission's
folder. The frontmatter holds ``name_image`` and ``id_image`` references
to those crops at the top, plus normalized bboxes for the Name/ID fields
under ``name_field`` / ``student_id_field``. No value-extraction OCR is
done here — populating ``name`` / ``student_id`` / per-question Response
text is the job of a downstream OCR pass.
"""

EPILOG = """
examples:
  uv run submission-extractor --input split/ --output out/student-submissions/
  uv run submission-extractor --input split/submission-0001.pdf --output out/student-submissions/
  uv run submission-extractor --input scans/ --output out/student-submissions/ \\
    --blank-exam blank.pdf
"""

# Patterns applied to fitz-extracted text from a PDF text layer (blank exam
# or text-layer student PDF).
#
# Points spec accepts the regular form ``(N points)`` plus the two bonus
# forms we render: ``(N bonus points)`` and ``(N points (bonus))``.
_POINTS_SPEC = (
    r"\(\s*(\d+(?:\.\d+)?)\s*(?:bonus\s+)?"
    r"(?:points?|pts)(?:\s*\(\s*bonus\s*\))?\s*\)"
)
_QUESTION_PREFIX_RE = re.compile(
    r"^\s*(\d+)\.\s*" + _POINTS_SPEC + r"\s*(.*)$",
    re.IGNORECASE,
)
_QUESTION_INLINE_RE = re.compile(
    r"(\d+)\.\s*" + _POINTS_SPEC + r"\s*(.+?)(?=\n\s*\d+\.\s*\(|\Z)",
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
_SCAN_QUESTION_LINE_RE = re.compile(
    r"^\s*(\d{1,2})\s*[\.)]\s*\(\s*(\d+(?:\.\d+)?)\s*(?:bonus\s+)?(?:points?|pts)\b",
    re.IGNORECASE,
)
_SCAN_NAME_WORD_RE = re.compile(r"^name[:.,]?$", re.IGNORECASE)
_SCAN_ID_WORD_RE = re.compile(r"^id[:.,]?$", re.IGNORECASE)
_SCAN_STUDENT_WORD_RE = re.compile(r"^student[:.,]?$", re.IGNORECASE)
_SCAN_PUNCT_WORD_RE = re.compile(r"^[:.,;]+$")
_SCAN_QUESTION_MAX_X_NORM = 0.30

# Field-bbox geometry constants (PDF points, page-size relative).
_FIELD_BOTTOM_PAD_POINTS = 10.0  # ~3.5mm below the label baseline


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


def _crop_norm(
    image: Image.Image,
    bbox_norm: tuple[float, float, float, float],
    *,
    pad_x: float = 0.0,
    pad_y: float = 0.0,
) -> Image.Image | None:
    width, height = image.size
    x, y, w, h = bbox_norm
    x0 = max(0, int(round((x - pad_x) * width)))
    y0 = max(0, int(round((y - pad_y) * height)))
    x1 = min(width, int(round((x + w + pad_x) * width)))
    y1 = min(height, int(round((y + h + pad_y) * height)))
    if x1 <= x0 or y1 <= y0:
        return None
    return crop_region(image, (x0, y0, x1, y1))


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
        try:
            data = pytesseract.image_to_data(
                image,
                output_type=pytesseract.Output.DICT,
                config="--psm 6",
            )
        except Exception:
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

            question_match = _SCAN_QUESTION_LINE_RE.match(line_text)
            if (
                question_match is not None
                and (line_left / width) < _SCAN_QUESTION_MAX_X_NORM
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


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Extract per-student submission markdown files from PDFs. Reads "
            "the text layer when present; with --blank-exam, applies a "
            "template extracted from a blank exam PDF and saves Name/ID "
            "field crops plus per-question response crops, referenced from "
            "the produced .submission.md."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to one submission PDF or a directory containing PDFs.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output directory for generated per-submission folders.",
    )
    parser.add_argument(
        "--blank-exam",
        metavar="PDF",
        default=None,
        help=(
            "Path to a blank version of the exam PDF (same layout as the "
            "scans, real text layer required). When given, its question "
            "prompts/points/bboxes and name/ID field bboxes populate every "
            "scan's .submission.md, and the scans no longer need a text "
            "layer themselves."
        ),
    )
    return parser


def _collect_input_pdfs(input_path: str) -> list[Path]:
    path = Path(input_path)
    if path.is_file():
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"input file must be a PDF: {path}")
        return [path]
    if path.is_dir():
        pdfs = sorted(path.glob("*.pdf"))
        if not pdfs:
            raise ValueError(f"no PDF files found in directory: {path}")
        return pdfs
    raise FileNotFoundError(f"input path not found: {path}")


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


def _extract_questions_from_text(all_text: str) -> list[StudentSubmissionQuestion]:
    """Plain student-submission mode: pull prompts/points from raw text."""
    questions: list[StudentSubmissionQuestion] = []
    for qnum_str, pts_str, qtext in _QUESTION_INLINE_RE.findall(all_text):
        qnum = int(qnum_str)
        qtext_clean = re.sub(r"\s+", " ", qtext).strip()
        questions.append(
            StudentSubmissionQuestion(
                question_id=f"q{qnum}",
                prompt=qtext_clean,
                possible_points=float(pts_str),
                response_image_path=None,
                response_text="",
            )
        )
    return questions


def _find_question_starts(lines: list[_TextLine]) -> list[tuple[int, int, float, str]]:
    """Locate question prefix lines.

    Returns a list of (line_index, qnum, points, prompt_text). The qnum is the
    parsed question number; points is the per-question max from the "(N points)"
    prefix; prompt_text is the trailing prompt text on the same line.
    """
    matches: list[tuple[int, int, float, str]] = []
    for index, line in enumerate(lines):
        matched = _QUESTION_PREFIX_RE.match(line.text)
        if matched is None:
            continue
        qnum = int(matched.group(1))
        points = float(matched.group(2))
        prompt = matched.group(3).strip()
        matches.append((index, qnum, points, prompt))
    return matches


def _collect_question_prompt(
    lines: list[_TextLine],
    start_index: int,
    next_question_index: int | None,
    head_text: str,
) -> str:
    """Join all lines that belong to a single question's prompt."""
    pieces = [head_text] if head_text else []
    start_line = lines[start_index]
    end_index = next_question_index if next_question_index is not None else len(lines)

    for line in lines[start_index + 1 : end_index]:
        if line.page != start_line.page:
            break
        pieces.append(line.text.strip())

    return re.sub(r"\s+", " ", " ".join(piece for piece in pieces if piece)).strip()


def _answer_bbox_for_question(
    lines: list[_TextLine],
    start_index: int,
    next_question_index: int | None,
) -> tuple[int, tuple[float, float, float, float]]:
    """Compute the (page, normalized bbox) covering a question's full region."""
    start_line = lines[start_index]
    page = start_line.page
    page_h = start_line.page_h

    answer_top = start_line.y0

    next_question_same_page_y: float | None = None
    if next_question_index is not None:
        next_line = lines[next_question_index]
        if next_line.page == page:
            next_question_same_page_y = next_line.y0

    total_points_y: float | None = None
    for line in lines[start_index + 1 :]:
        if line.page != page:
            break
        if next_question_same_page_y is not None and line.y0 >= next_question_same_page_y:
            break
        if _TOTAL_POINTS_LINE_RE.search(line.text):
            total_points_y = line.y0
            break

    candidate_bottoms = [page_h - 18.0]
    if next_question_same_page_y is not None:
        candidate_bottoms.append(next_question_same_page_y - 2.0)
    if total_points_y is not None:
        candidate_bottoms.append(total_points_y - 2.0)
    answer_bottom = max(answer_top + 1.0, min(candidate_bottoms))

    same_page_lines = [line for line in lines if line.page == page]
    left_margin = min(line.x0 for line in same_page_lines)
    right_margin = max(line.x1 for line in same_page_lines)

    bbox = _norm_bbox(
        start_line,
        x0=max(0.0, left_margin),
        y0=answer_top,
        x1=min(start_line.page_w, right_margin),
        y1=answer_bottom,
    )
    return page, bbox


def _find_field_location(
    lines: list[_TextLine],
    label_pattern: re.Pattern[str],
) -> _FieldLocation | None:
    """Locate the field-entry rectangle for a label like "Name:" from text layer."""
    for line in lines:
        if label_pattern.search(line.text) is None:
            continue

        right_neighbors = [
            other
            for other in lines
            if other.page == line.page
            and other is not line
            and other.x0 >= line.x1
            and abs((other.y0 + other.y1) / 2 - (line.y0 + line.y1) / 2)
            <= max(2.0, (line.y1 - line.y0))
        ]
        right_neighbors.sort(key=lambda neighbor: neighbor.x0)

        x0 = line.x1 + 2.0
        if right_neighbors and right_neighbors[0].x0 > line.x1 + 4.0:
            x1 = right_neighbors[0].x0 - 2.0
        else:
            x1 = line.page_w * 0.95

        y0 = 0.0
        y1 = min(line.page_h, line.y1 + _FIELD_BOTTOM_PAD_POINTS)

        return _FieldLocation(
            page=line.page,
            bbox=_norm_bbox(line, x0=x0, y0=y0, x1=x1, y1=y1),
        )
    return None


def _build_blank_exam_questions(
    lines: list[_TextLine],
) -> list[StudentSubmissionQuestion]:
    starts = _find_question_starts(lines)
    questions: list[StudentSubmissionQuestion] = []

    for ordinal, (line_index, qnum, points, head_text) in enumerate(starts):
        next_line_index = starts[ordinal + 1][0] if ordinal + 1 < len(starts) else None
        prompt = _collect_question_prompt(lines, line_index, next_line_index, head_text)
        page, bbox = _answer_bbox_for_question(lines, line_index, next_line_index)
        questions.append(
            StudentSubmissionQuestion(
                question_id=f"q{qnum}",
                prompt=prompt,
                possible_points=points,
                response_image_path=None,
                response_text="",
                source_segments=(
                    QuestionSourceSegment(page_number=page, bbox_norm=bbox),
                ),
            )
        )

    return questions


def _assignment_title_from_lines(lines: list[_TextLine]) -> str:
    """Return the first non-empty, non-form-label line as the assignment title."""
    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        if _FORM_LABEL_RE.match(text):
            continue
        return text
    for line in lines:
        text = line.text.strip()
        if text:
            return text
    return ""


def _source_pdf_relpath(pdf_path: Path, submission_dir: Path) -> str:
    return os.path.relpath(
        pdf_path.expanduser().resolve(),
        start=submission_dir.expanduser().resolve(),
    )


def _build_submission_document(
    pdf_path: Path,
    submission_dir: Path,
    lines: list[_TextLine],
) -> StudentSubmissionDocument:
    """Build a document directly from a text-layer student PDF (no scan crops)."""
    all_text = "\n".join(line.text for line in lines)

    id_match = _ID_VALUE_RE.search(all_text)
    name_match = _NAME_VALUE_RE.search(all_text)

    return StudentSubmissionDocument(
        student_name=name_match.group(1).strip() if name_match else "",
        student_id=id_match.group(1) if id_match else "",
        course_code=None,
        assignment_title=_assignment_title_from_lines(lines),
        assignment_version=None,
        source_pdf=_source_pdf_relpath(pdf_path, submission_dir),
        score="",
        questions=_extract_questions_from_text(all_text),
    )


def _format_field_location_yaml(location: _FieldLocation | None) -> str:
    if location is None:
        return "null"
    payload = {"page": location.page, "bbox": list(location.bbox)}
    return json.dumps(payload, separators=(",", ":"))


def _render_submission_markdown(
    document: StudentSubmissionDocument,
    name_field: _FieldLocation | None,
    student_id_field: _FieldLocation | None,
    name_image: str | None,
    id_image: str | None,
) -> str:
    """Render a submission with image references at the top of the frontmatter.

    ``name_image`` and ``id_image`` are inserted right after the opening
    ``---`` so downstream viewers can display the saved Name and ID crops
    even before any value-extraction OCR pass has populated the textual
    ``name`` and ``student_id`` fields. The normalized bboxes used for
    cropping land at the end of the frontmatter under ``name_field`` and
    ``student_id_field`` for tools that re-crop or further refine the
    field regions.
    """
    markdown = render_student_submission_markdown(document)
    parts = markdown.split("---\n", 2)
    if len(parts) < 3:
        return markdown

    top_lines: list[str] = []
    if name_image:
        top_lines.append(f'name_image: "{name_image}"')
    if id_image:
        top_lines.append(f'id_image: "{id_image}"')

    body = parts[1].rstrip() + "\n"
    extra_bottom = (
        f"name_field: {_format_field_location_yaml(name_field)}\n"
        f"student_id_field: {_format_field_location_yaml(student_id_field)}\n"
    )

    front = ""
    if top_lines:
        front = "\n".join(top_lines) + "\n"
    return "---\n" + front + body + extra_bottom + "---\n" + parts[2]


@dataclass(frozen=True)
class _BlankExamTemplate:
    assignment_title: str
    name_field: _FieldLocation | None
    student_id_field: _FieldLocation | None
    questions: tuple[StudentSubmissionQuestion, ...]


def _load_blank_exam_template(pdf_path: Path) -> _BlankExamTemplate:
    lines = _read_pdf_lines(pdf_path)
    _ensure_text_layer(lines, pdf_path)
    return _BlankExamTemplate(
        assignment_title=_assignment_title_from_lines(lines),
        name_field=_find_field_location(lines, _NAME_LABEL_RE),
        student_id_field=_find_field_location(lines, _ID_LABEL_RE),
        questions=tuple(_build_blank_exam_questions(lines)),
    )


def _save_field_crop(
    image: Image.Image,
    field: _FieldLocation,
    field_crops_dir: Path,
    kind: str,
) -> str | None:
    """Save a Name/ID field crop and return its submission-relative path."""
    crop = _crop_norm(image, field.bbox, pad_x=0.005, pad_y=0.0)
    if crop is None:
        return None
    field_crops_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{kind}_p{field.page}.png"
    crop.save(field_crops_dir / filename)
    return f"field_crops/{filename}"


def _save_response_crop(
    question: StudentSubmissionQuestion,
    rendered_pages: dict[int, Image.Image],
    responses_dir: Path,
) -> str | None:
    if not question.source_segments:
        return None
    segment = question.source_segments[0]
    page_image = rendered_pages.get(segment.page_number)
    if page_image is None:
        return None
    crop = _crop_norm(page_image, segment.bbox_norm)
    if crop is None:
        return None
    responses_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{question.question_id}.png"
    crop.save(responses_dir / filename)
    return f"responses/{filename}"


def _select_field(
    *,
    label: _ScanLabelBox | None,
    right_boundary: _ScanLabelBox | None,
    template_field: _FieldLocation | None,
    line_boxes_by_page: dict[int, list[tuple[float, float, float]]],
    rendered_pages: dict[int, Image.Image],
    tight_top: bool = False,
) -> _FieldLocation | None:
    """Pick scan-detected field bbox when available, else fall back to the template's."""
    if label is None:
        return template_field
    return _field_location_from_scan_label(
        label,
        right_boundary=right_boundary,
        line_boxes=line_boxes_by_page.get(label.page, []),
        page_image=rendered_pages.get(label.page),
        tight_top=tight_top,
    )


def _combined_question_positions(
    template: _BlankExamTemplate,
    scan_layout: _ScanLayout,
    page_offsets: dict[int, float],
) -> dict[int, tuple[int, float]]:
    """Per-question (page, y_top_norm) using scan detection when available."""
    positions: dict[int, tuple[int, float]] = {}
    for question in template.questions:
        qnum = _question_number(question)
        if qnum is None:
            continue
        scan_position = scan_layout.question_positions.get(qnum)
        if scan_position is not None:
            positions[qnum] = scan_position
        elif question.source_segments:
            segment = question.source_segments[0]
            offset = page_offsets.get(segment.page_number, 0.0)
            shifted_y = max(0.0, min(1.0, segment.bbox_norm[1] + offset))
            positions[qnum] = (segment.page_number, shifted_y)
    return positions


def _scan_offset_by_page(
    template: _BlankExamTemplate,
    scan_layout: _ScanLayout,
) -> dict[int, float]:
    """Per-page median y shift between scan and blank-exam question positions."""
    blank_positions: dict[int, tuple[int, float]] = {}
    for question in template.questions:
        qnum = _question_number(question)
        if qnum is None or not question.source_segments:
            continue
        segment = question.source_segments[0]
        blank_positions[qnum] = (segment.page_number, segment.bbox_norm[1])

    deltas_by_page: dict[int, list[float]] = {}
    for qnum, (scan_page, scan_y) in scan_layout.question_positions.items():
        blank = blank_positions.get(qnum)
        if blank is None:
            continue
        blank_page, blank_y = blank
        if blank_page != scan_page:
            continue
        deltas_by_page.setdefault(scan_page, []).append(scan_y - blank_y)

    offsets: dict[int, float] = {}
    for page, deltas in deltas_by_page.items():
        if not deltas:
            continue
        deltas_sorted = sorted(deltas)
        mid = len(deltas_sorted) // 2
        if len(deltas_sorted) % 2:
            offsets[page] = deltas_sorted[mid]
        else:
            offsets[page] = 0.5 * (deltas_sorted[mid - 1] + deltas_sorted[mid])
    return offsets


def _question_with_scan_crop(
    question: StudentSubmissionQuestion,
    positions: dict[int, tuple[int, float]],
    total_points_positions: dict[int, float],
    rendered_pages: dict[int, Image.Image],
    responses_dir: Path,
) -> StudentSubmissionQuestion:
    """Place a question at its combined-map bbox and save its response crop."""
    qnum = _question_number(question)
    bbox_info = (
        _response_bbox_from_positions(qnum, positions, total_points_positions)
        if qnum is not None
        else None
    )
    if bbox_info is not None:
        page_num, bbox = bbox_info
        placed = replace(
            question,
            source_segments=(
                QuestionSourceSegment(page_number=page_num, bbox_norm=bbox),
            ),
        )
    else:
        placed = question

    image_path = _save_response_crop(placed, rendered_pages, responses_dir)
    return replace(placed, response_image_path=image_path, response_text="")


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

    field_crops_dir = submission_dir / "field_crops"
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
        score="",
        questions=questions,
    )
    return document, name_field, id_field, name_image, id_image


def _process_student_pdf(pdf_path: Path, output_dir: Path) -> Path:
    submission_dir = output_dir / pdf_path.stem
    submission_dir.mkdir(parents=True, exist_ok=True)

    lines = _read_pdf_lines(pdf_path)
    _ensure_text_layer(lines, pdf_path)

    document = _build_submission_document(pdf_path, submission_dir, lines)
    markdown_path = submission_dir / f"{pdf_path.stem}.submission.md"
    markdown_path.write_text(render_student_submission_markdown(document))
    return markdown_path


def _process_student_pdf_with_template(
    pdf_path: Path,
    output_dir: Path,
    template: _BlankExamTemplate,
) -> Path:
    submission_dir = output_dir / pdf_path.stem
    submission_dir.mkdir(parents=True, exist_ok=True)

    document, name_field, id_field, name_image, id_image = (
        _build_document_from_template(pdf_path, submission_dir, template)
    )
    markdown_path = submission_dir / f"{pdf_path.stem}.submission.md"
    markdown = _render_submission_markdown(
        document, name_field, id_field, name_image, id_image
    )
    markdown_path.write_text(markdown)
    return markdown_path


def run_cli() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    pdfs = _collect_input_pdfs(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    template: _BlankExamTemplate | None = None
    if args.blank_exam:
        try:
            template = _load_blank_exam_template(Path(args.blank_exam))
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)

    print(f"Found {len(pdfs)} submission PDF(s)")
    for index, pdf_path in enumerate(pdfs, start=1):
        try:
            if template is not None:
                markdown_path = _process_student_pdf_with_template(
                    pdf_path, output_dir, template
                )
            else:
                markdown_path = _process_student_pdf(pdf_path, output_dir)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"[{index}/{len(pdfs)}] Wrote {markdown_path}")


if __name__ == "__main__":
    run_cli()
