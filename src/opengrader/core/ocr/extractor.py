from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from statistics import median
from typing import Literal

import cv2
import fitz
import numpy as np
from PIL import Image

from opengrader.core.question_types import FREE_RESPONSE, MULTIPLE_CHOICE, QuestionType
from opengrader.core.source_location import QuestionSourceSegment

from opengrader.core.pdf_utils import (
    render_page_to_image,
    bbox_norm_to_px,
    clamp_bbox_px,
)

from .layout import (
    SubmissionLayout,
    save_layout_debug as write_layout_debug,
    scan_submission_layout,
)
from .question_classifier import classify_question_image

ConfidenceThreshold = float

# Keep OCR confidence defaults centralized so callers can tune validation
# without changing the crop-discovery heuristics below.
DEFAULT_CONFIDENCE_THRESHOLD = 60.0
MIN_TEXT_LENGTH = 1

# Question crops get small padding so border-touching handwriting survives
# trimming, while printed rules still remain close enough to remove later.
QUESTION_CROP_PAD_X_NORM = 0.015
QUESTION_CROP_PAD_TOP_NORM = 0.004
QUESTION_CROP_PAD_BOTTOM_NORM = 0.008

# Structural answer-box limits reject page borders and tiny decoration before a
# box is allowed to influence question crop bounds.
QUESTION_BOX_MIN_WIDTH_NORM = 0.36
QUESTION_BOX_MIN_HEIGHT_NORM = 0.05
QUESTION_BOX_MAX_HEIGHT_NORM = 0.72
QUESTION_BOX_PAGE_BORDER_Y_NORM = 0.055
QUESTION_BOX_MARKER_REL_Y_MAX = 0.34
QUESTION_NEXT_STRUCTURAL_MIN_GAP_NORM = 0.05
QUESTION_NEXT_STRUCTURAL_X_MARGIN_NORM = 0.06
QUESTION_STRUCTURAL_USE_MATCHED_BOX_MAX_HEIGHT_NORM = 0.55
QUESTION_STRUCTURAL_MATCHED_BOX_FRACTION = 0.75

# Trim thresholds favor keeping ambiguous student marks over tight crops; rules
# and large edge artifacts are filtered separately before these are applied.
QUESTION_TRIM_DARK_THRESHOLD = 232
QUESTION_TRIM_MIN_COMPONENT_AREA = 18
QUESTION_TRIM_MIN_COMPONENT_WIDTH = 3
QUESTION_TRIM_MIN_COMPONENT_HEIGHT = 3
QUESTION_TRIM_LINE_COMPONENT_MIN_SPAN_NORM = 0.65
QUESTION_TRIM_LINE_COMPONENT_MAX_THICKNESS_NORM = 0.04
QUESTION_TRIM_MIN_AREA_RATIO = 0.01

# Field slices are intentionally wider than parsed text so saved artifacts show
# surrounding label context for manual review.
FIELD_SLICE_MIN_WIDTH_NORM = 0.42
FIELD_SLICE_MAX_WIDTH_NORM = 0.82
FIELD_SLICE_PAD_LEFT_NORM = 0.02
FIELD_SLICE_PAD_RIGHT_NORM = 0.15
FIELD_SLICE_TOP_PAD_FACTOR = 1.9
FIELD_SLICE_BOTTOM_PAD_FACTOR = 1.45
FIELD_SLICE_MIN_HEIGHT_NORM = 0.03
FIELD_SLICE_MAX_HEIGHT_NORM = 0.145

# Header underline detection is constrained to plausible metadata blanks so
# answer boxes and page separators do not get reused as name/ID fields.
HEADER_UNDERLINE_SCAN_END_NORM = 0.42
HEADER_UNDERLINE_MIN_WIDTH_NORM = 0.18
HEADER_UNDERLINE_MAX_WIDTH_NORM = 0.88
HEADER_UNDERLINE_MAX_HEIGHT_NORM = 0.012
HEADER_UNDERLINE_MIN_Y_NORM = 0.06

# Underline-derived crops include nearby label text because field values may be
# blank, handwritten, or poorly recognized by OCR.
HEADER_CONTEXT_LEFT_PAD_NORM = 0.14
HEADER_CONTEXT_RIGHT_PAD_NORM = 0.02
HEADER_CONTEXT_TOP_PAD_NORM = 0.034
HEADER_CONTEXT_BOTTOM_PAD_NORM = 0.012


@dataclass
class OcrResult:
    text: str
    confidence: float
    bbox_px: tuple[int, int, int, int]


@dataclass
class FieldExtractionResult:
    field_id: str
    ocr_result: OcrResult | None
    expected: str | None = None
    validated: bool = False
    error: str | None = None
    crop_path: str | None = None


@dataclass
class QuestionExtractionResult:
    question_id: str
    page_number: int
    output_path: str | None = None
    skipped: bool = False
    error: str | None = None
    question_type: QuestionType = FREE_RESPONSE
    source_segments: tuple[QuestionSourceSegment, ...] = ()


@dataclass
class SubmissionResult:
    filename: str
    version_id: str | None = None
    version_confidence: float | None = None
    version_validated: bool = False
    version_error: str | None = None
    title_crop_path: str | None = None
    fields: list[FieldExtractionResult] = field(default_factory=list)
    questions: list[QuestionExtractionResult] = field(default_factory=list)
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)


@dataclass
class OcrConfig:
    lang: str = "eng"
    deskew: bool = True
    confidence_threshold: ConfidenceThreshold = DEFAULT_CONFIDENCE_THRESHOLD


@dataclass(frozen=True)
class _OcrWord:
    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int
    line_id: tuple[int, int, int]

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


@dataclass(frozen=True)
class _QuestionMarker:
    page_number: int
    question_number: int
    x_px: int
    y_px: int
    conf: float
    has_points_hint: bool


def _normalize_name_text(raw: str) -> str:
    """Clean OCR output into a plausible human name string."""

    text = raw.replace("|", " ").replace("_", " ")
    text = text.replace("“", " ").replace("”", " ").replace("`", " ")
    text = re.sub(r"[^A-Za-z\s\-'\.]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(?i:name)\s+", "", text)
    return text


def _normalize_sid_text(raw: str) -> str:
    """Keep only digits from OCR output intended to contain a student ID."""

    return "".join(re.findall(r"\d", raw))


def crop_region(image: Image.Image, bbox_px: tuple[int, int, int, int]) -> Image.Image:
    """Crop a PIL image using a pixel bbox."""
    # We use a helper here because manual slicing with (x0, y0, x1, y1)
    # is often slightly offset from what users expect compared to 
    # normalized boxes.

    x0, y0, x1, y1 = bbox_px
    return image.crop((x0, y0, x1, y1))


def _expand_question_bbox_px(
    bbox_px: tuple[int, int, int, int],
    *,
    page_w: int,
    page_h: int,
) -> tuple[int, int, int, int]:
    """Apply standard question-crop padding to a pixel bbox."""

    x0, y0, x1, y1 = bbox_px
    x_pad = max(4, int(round(page_w * QUESTION_CROP_PAD_X_NORM)))
    top_pad = max(2, int(round(page_h * QUESTION_CROP_PAD_TOP_NORM)))
    bottom_pad = max(4, int(round(page_h * QUESTION_CROP_PAD_BOTTOM_NORM)))
    return clamp_bbox_px(
        (x0 - x_pad, y0 - top_pad, x1 + x_pad, y1 + bottom_pad),
        page_w=page_w,
        page_h=page_h,
    )


def _trim_extent_by_gap(
    active: np.ndarray,
    dimension_size: int,
    *,
    min_gap_norm: float = 0.06,
) -> tuple[int, int]:
    """Trim isolated edge clusters from an active row or column extent."""

    if active.size <= 1:
        return (int(active[0]), int(active[-1])) if active.size else (0, 0)

    start = int(active[0])
    end = int(active[-1])
    min_gap = max(20, int(dimension_size * min_gap_norm))

    diffs = np.diff(active)

    # Treat leading specks as scan noise only when they are tiny compared with
    # the main content span.
    for i in range(len(diffs)):
        if diffs[i] > min_gap:
            before_count = i + 1
            after_count = len(active) - before_count
            before_span = int(active[i]) - start
            after_span = end - int(active[i + 1])
            if before_count <= max(3, int(after_count * 0.10)) and before_span < max(
                20, after_span * 0.15
            ):
                start = int(active[i + 1])
            break

    # Apply the same caution at the trailing edge so sparse answers are not
    # cropped away as if they were border noise.
    for i in range(len(diffs) - 1, -1, -1):
        if diffs[i] > min_gap:
            before_idx = i
            after_count = len(active) - (before_idx + 1)
            before_count = before_idx + 1
            after_span = end - int(active[i + 1])
            before_span = int(active[i]) - start
            if after_count <= max(3, int(before_count * 0.10)) and after_span < max(
                20, before_span * 0.15
            ):
                end = int(active[i])
            break

    return start, end


def _restore_same_line_answer_right_edge(
    x1: int,
    y0: int,
    components: list[tuple[int, int, int, int, int]],
    *,
    region_w: int,
    region_h: int,
) -> int:
    """Keep short same-line answers from being treated as right-side tails."""
    top_band_bottom = y0 + max(80, int(region_h * 0.22))
    horizontal_line_height = max(
        12,
        int(region_h * QUESTION_TRIM_LINE_COMPONENT_MAX_THICKNESS_NORM),
    )
    vertical_line_width = max(
        12,
        int(region_w * QUESTION_TRIM_LINE_COMPONENT_MAX_THICKNESS_NORM),
    )
    restored_x1 = x1

    for x, y, w, h, area in components:
        component_right = x + w
        if component_right <= x1:
            continue

        component_center_y = y + (h / 2.0)
        if component_center_y > top_band_bottom:
            continue

        if area < max(QUESTION_TRIM_MIN_COMPONENT_AREA, 40):
            continue
        if (
            w < QUESTION_TRIM_MIN_COMPONENT_WIDTH
            or h < QUESTION_TRIM_MIN_COMPONENT_HEIGHT
        ):
            continue
        # Printed rules are ignored here so same-line answers can extend right
        # without reintroducing answer-box borders.
        if (
            w >= int(region_w * QUESTION_TRIM_LINE_COMPONENT_MIN_SPAN_NORM)
            and h <= horizontal_line_height
        ):
            continue
        if (
            h >= int(region_h * QUESTION_TRIM_LINE_COMPONENT_MIN_SPAN_NORM)
            and w <= vertical_line_width
        ):
            continue

        restored_x1 = max(restored_x1, component_right)

    return restored_x1


def _trim_question_region_once_with_bbox(
    region: Image.Image, *, margin_px: int = 8
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """
    Trim a question crop down to likely answer content.

    Printed answer-box rules, dark scanner gutters, and page-edge fragments are
    removed before computing the content bounds. The original region is returned
    when the trim looks unsafe.
    """

    gray = np.array(region.convert("L"))
    if gray.size == 0:
        return region, (0, 0, region.width, region.height)

    _, binary_inv = cv2.threshold(
        gray,
        QUESTION_TRIM_DARK_THRESHOLD,
        255,
        cv2.THRESH_BINARY_INV,
    )

    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (max(24, region.width // 5), 1),
    )
    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (1, max(24, region.height // 5)),
    )
    horizontal_lines = cv2.morphologyEx(binary_inv, cv2.MORPH_OPEN, horizontal_kernel)
    vertical_lines = cv2.morphologyEx(binary_inv, cv2.MORPH_OPEN, vertical_kernel)
    # Remove printed rules before measuring content; otherwise the answer box
    # frame becomes the crop instead of the student's response.
    content_mask = cv2.subtract(binary_inv, horizontal_lines)
    content_mask = cv2.subtract(content_mask, vertical_lines)
    content_mask = cv2.morphologyEx(
        content_mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
    )

    _GUTTER_SCAN_WIDTH = max(10, int(region.width * 0.08))
    _GUTTER_FILL_THRESHOLD = 0.25

    gutter_right = 0
    # Dark scanner gutters and page borders can look like real content unless
    # dense edge columns are wiped before bounding-box estimation.
    for col_idx in range(_GUTTER_SCAN_WIDTH):
        col_fill = np.count_nonzero(content_mask[:, col_idx]) / max(1, region.height)
        if col_fill >= _GUTTER_FILL_THRESHOLD:
            gutter_right = col_idx + 1
        else:
            if gutter_right > 0 and col_idx - gutter_right <= 3:
                continue
            break
    if gutter_right > 0:
        content_mask[:, :gutter_right] = 0

    gutter_left_r = region.width
    for col_idx in range(
        region.width - 1, max(0, region.width - _GUTTER_SCAN_WIDTH) - 1, -1
    ):
        col_fill = np.count_nonzero(content_mask[:, col_idx]) / max(1, region.height)
        if col_fill >= _GUTTER_FILL_THRESHOLD:
            gutter_left_r = col_idx
        else:
            if (
                gutter_left_r < region.width
                and (region.width - 1 - col_idx) - (region.width - gutter_left_r) <= 3
            ):
                continue
            break
    if gutter_left_r < region.width:
        content_mask[:, gutter_left_r:] = 0

    _BORDER_SCAN_HEIGHT = max(10, int(region.height * 0.15))
    _BORDER_CORE_FILL = 0.50
    _BORDER_EDGE_BAND = max(10, int(region.width * 0.04))
    _BORDER_EDGE_FILL = 0.35

    border_core_top = region.height
    found_core = False
    for row_idx in range(
        region.height - 1,
        max(0, region.height - _BORDER_SCAN_HEIGHT) - 1,
        -1,
    ):
        row = binary_inv[row_idx, :]
        row_fill = np.count_nonzero(row) / max(1, region.width)
        left_edge_fill = np.count_nonzero(row[:_BORDER_EDGE_BAND]) / max(
            1, _BORDER_EDGE_BAND
        )
        right_edge_fill = np.count_nonzero(row[-_BORDER_EDGE_BAND:]) / max(
            1, _BORDER_EDGE_BAND
        )
        if (
            row_fill >= _BORDER_CORE_FILL
            and left_edge_fill >= _BORDER_EDGE_FILL
            and right_edge_fill >= _BORDER_EDGE_FILL
        ):
            border_core_top = row_idx
            found_core = True
        else:
            if found_core:
                if (row_idx - border_core_top) >= -3:
                    continue
                break
    if border_core_top < region.height:
        core_height = region.height - border_core_top
        fringe = max(6, int(core_height * 1.5))
        wipe_from = max(0, border_core_top - fringe)
        # Keep surviving bottom frame lines from stretching the crop to the
        # printed answer-box edge.
        content_mask[wipe_from:, :] = 0

    border_core_bottom = 0
    found_top_core = False
    for row_idx in range(_BORDER_SCAN_HEIGHT):
        row = binary_inv[row_idx, :]
        row_fill = np.count_nonzero(row) / max(1, region.width)
        left_edge_fill = np.count_nonzero(row[:_BORDER_EDGE_BAND]) / max(
            1, _BORDER_EDGE_BAND
        )
        right_edge_fill = np.count_nonzero(row[-_BORDER_EDGE_BAND:]) / max(
            1, _BORDER_EDGE_BAND
        )
        if (
            row_fill >= _BORDER_CORE_FILL
            and left_edge_fill >= _BORDER_EDGE_FILL
            and right_edge_fill >= _BORDER_EDGE_FILL
        ):
            border_core_bottom = row_idx + 1
            found_top_core = True
        else:
            if found_top_core:
                if row_idx - border_core_bottom <= 3:
                    continue
                break
    if border_core_bottom > 0:
        core_height = border_core_bottom
        fringe = max(6, int(core_height * 1.5))
        wipe_to = min(region.height, border_core_bottom + fringe)
        # Top frame lines are removed for the same reason as bottom borders:
        # they are template structure, not answer content.
        content_mask[:wipe_to, :] = 0

    _MIN_COMPONENT_AREA = max(QUESTION_TRIM_MIN_COMPONENT_AREA, 40)

    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(content_mask, 8)
    component_mask = np.zeros_like(content_mask)
    components: list[tuple[int, int, int, int, int]] = []
    for label in range(1, num_labels):
        x, y, w, h, area = stats[label]
        if area < _MIN_COMPONENT_AREA:
            continue
        if w >= int(
            region.width * QUESTION_TRIM_LINE_COMPONENT_MIN_SPAN_NORM
        ) and h <= max(
            12, int(region.height * QUESTION_TRIM_LINE_COMPONENT_MAX_THICKNESS_NORM)
        ):
            continue
        if h >= int(
            region.height * QUESTION_TRIM_LINE_COMPONENT_MIN_SPAN_NORM
        ) and w <= max(
            12, int(region.width * QUESTION_TRIM_LINE_COMPONENT_MAX_THICKNESS_NORM)
        ):
            continue
        if (
            y + h >= region.height - 2
            and h <= max(16, int(region.height * 0.06))
            and w >= int(region.width * 0.2)
        ):
            continue
        if (
            (x <= 2 or x + w >= region.width - 2)
            and w <= max(16, int(region.width * 0.06))
            and h >= int(region.height * 0.2)
        ):
            continue
        if (
            w < QUESTION_TRIM_MIN_COMPONENT_WIDTH
            and h < QUESTION_TRIM_MIN_COMPONENT_HEIGHT
        ):
            continue
        # Keep likely handwriting/OCR text while excluding leftover rules and
        # edge fragments that morphology did not fully remove.
        components.append((int(x), int(y), int(w), int(h), int(area)))
        component_mask[y : y + h, x : x + w] = 255

    bbox_mask = component_mask if np.count_nonzero(component_mask) > 0 else binary_inv
    row_counts = np.count_nonzero(bbox_mask, axis=1)
    col_counts = np.count_nonzero(bbox_mask, axis=0)

    # Suppress full-span edge lines (borders that survived morphology).
    edge_row_band = max(6, int(region.height * 0.05))
    edge_col_band = max(6, int(region.width * 0.08))
    full_row_threshold = int(region.width * 0.65)
    full_col_threshold = int(region.height * 0.65)

    for idx in range(min(edge_row_band, len(row_counts))):
        if row_counts[idx] >= full_row_threshold:
            row_counts[idx] = 0
        bottom_idx = len(row_counts) - 1 - idx
        if bottom_idx >= 0 and row_counts[bottom_idx] >= full_row_threshold:
            row_counts[bottom_idx] = 0

    for idx in range(min(edge_col_band, len(col_counts))):
        if col_counts[idx] >= full_col_threshold:
            col_counts[idx] = 0
        right_idx = len(col_counts) - 1 - idx
        if right_idx >= 0 and col_counts[right_idx] >= full_col_threshold:
            col_counts[right_idx] = 0

    row_threshold = max(4, int(region.width * 0.004))
    col_threshold = max(4, int(region.height * 0.01))
    active_rows = np.where(row_counts >= row_threshold)[0]
    active_cols = np.where(col_counts >= col_threshold)[0]

    if active_rows.size == 0 or active_cols.size == 0:
        return region, (0, 0, region.width, region.height)

    y0, y1 = _trim_extent_by_gap(active_rows, region.height, min_gap_norm=0.06)
    x0, x1 = _trim_extent_by_gap(active_cols, region.width, min_gap_norm=0.06)
    x1 = _restore_same_line_answer_right_edge(
        x1,
        y0,
        components,
        region_w=region.width,
        region_h=region.height,
    )

    x0 = max(0, x0 - margin_px)
    y0 = max(0, y0 - margin_px)
    x1 = min(region.width, x1 + margin_px + 1)
    y1 = min(region.height, y1 + margin_px + 1)

    if x1 <= x0 or y1 <= y0:
        return region, (0, 0, region.width, region.height)

    trimmed = region.crop((x0, y0, x1, y1))
    original_area = max(1, region.width * region.height)
    trimmed_area = trimmed.width * trimmed.height
    area_ratio = trimmed_area / original_area

    # Guardrails: preserve the original crop when sparse/noisy scans make the
    # trim either implausibly small or effectively unchanged.
    if area_ratio < QUESTION_TRIM_MIN_AREA_RATIO or area_ratio > 0.995:
        return region, (0, 0, region.width, region.height)

    return trimmed, (x0, y0, x1, y1)


def _trim_question_region_with_bbox(
    region: Image.Image, *, margin_px: int = 8
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Trim a question region and return the trim bbox in region coordinates."""

    return _trim_question_region_once_with_bbox(region, margin_px=margin_px)


def _trim_question_region(region: Image.Image, *, margin_px: int = 8) -> Image.Image:
    """Return a question region trimmed to likely answer content."""

    trimmed, _bbox = _trim_question_region_with_bbox(region, margin_px=margin_px)
    return trimmed


def _question_final_crop_bbox_px(
    page_image: Image.Image,
    bbox_norm: list[float],
    page_w: int,
    page_h: int,
) -> tuple[int, int, int, int]:
    """Compute the final expanded-and-trimmed question crop bbox in page pixels."""

    expanded_bbox_px = _expand_question_bbox_px(
        bbox_norm_to_px(bbox_norm, page_w, page_h),
        page_w=page_w,
        page_h=page_h,
    )
    region = crop_region(page_image, expanded_bbox_px)
    _trimmed, trim_bbox_px = _trim_question_region_with_bbox(region)
    x0, y0, _, _ = expanded_bbox_px
    tx0, ty0, tx1, ty1 = trim_bbox_px
    return clamp_bbox_px(
        (x0 + tx0, y0 + ty0, x0 + tx1, y0 + ty1),
        page_w=page_w,
        page_h=page_h,
    )


# TODO: this can be shared


def _group_words_by_line(
    words: list[_OcrWord],
) -> dict[tuple[int, int, int], list[_OcrWord]]:
    """Group OCR words by Tesseract line identifiers."""

    lines: dict[tuple[int, int, int], list[_OcrWord]] = {}
    for w in words:
        lines.setdefault(w.line_id, []).append(w)
    for line_words in lines.values():
        line_words.sort(key=lambda word: word.left)
    return lines


def _merge_question_type_votes(*votes: QuestionType | None) -> QuestionType:
    """Return the most specific question type found by any extraction stage."""

    if any(vote == MULTIPLE_CHOICE for vote in votes):
        return MULTIPLE_CHOICE
    return FREE_RESPONSE


def _question_region_key(region: "DetectedQuestionRegion") -> tuple:
    """Return a stable key for matching layout regions to extracted artifacts."""

    return (
        region.question_id,
        region.question_number,
        region.page_number,
        tuple(region.crop_bbox_norm),
    )


def _layout_with_final_question_types(
    layout: SubmissionLayout,
    final_types_by_region_key: dict[tuple, QuestionType],
) -> SubmissionLayout:
    """Return layout debug data with question types from final extraction."""

    return replace(
        layout,
        questions=[
            replace(
                question,
                question_type=final_types_by_region_key.get(
                    _question_region_key(question),
                    question.question_type,
                ),
            )
            for question in layout.questions
        ],
    )


def _extract_question_crop_norm(
    page_image: Image.Image,
    question_id: str,
    bbox_norm: list[float],
    page_w: int,
    page_h: int,
    page_num: int,
    response_format: Literal["png", "pdf"],
    sub_dir: Path,
    detected_question_type: QuestionType | None = None,
) -> QuestionExtractionResult:
    """Write a single-page question crop artifact from a normalized bbox."""

    expanded_bbox_px = _expand_question_bbox_px(
        bbox_norm_to_px(bbox_norm, page_w, page_h),
        page_w=page_w,
        page_h=page_h,
    )
    region = crop_region(page_image, expanded_bbox_px)
    region, trim_bbox_px = _trim_question_region_with_bbox(region)
    source_bbox_px = clamp_bbox_px(
        (
            expanded_bbox_px[0] + trim_bbox_px[0],
            expanded_bbox_px[1] + trim_bbox_px[1],
            expanded_bbox_px[0] + trim_bbox_px[2],
            expanded_bbox_px[1] + trim_bbox_px[3],
        ),
        page_w=page_w,
        page_h=page_h,
    )
    question_type = _merge_question_type_votes(
        detected_question_type, classify_question_image(region)
    )
    out_path = sub_dir / f"{question_id}.{response_format}"

    if response_format == "pdf":
        region.convert("RGB").save(out_path, format="PDF")
    else:
        region.save(out_path)

    return QuestionExtractionResult(
        question_id=question_id,
        page_number=page_num,
        question_type=question_type,
        output_path=str(out_path),
        source_segments=(
            QuestionSourceSegment(
                page_number=page_num,
                bbox_norm=tuple(
                    (
                        source_bbox_px[0] / page_w, source_bbox_px[1] / page_h,
                        (source_bbox_px[2] - source_bbox_px[0]) / page_w, (source_bbox_px[3] - source_bbox_px[1]) / page_h
                    )
                ),
            ),
        ),
    )


def _extract_question_crop_segments(
    *,
    rendered_pages: dict[int, tuple[Image.Image, int, int]],
    question_id: str,
    segments: list[tuple[int, list[float]]],
    response_format: Literal["png", "pdf"],
    sub_dir: Path,
    detected_question_type: QuestionType | None = None,
) -> QuestionExtractionResult:
    """
    Write one question artifact from one or more page segments.

    Continuation segments are cropped independently and vertically stitched so
    callers receive a single output file for the logical question.
    """

    if not segments:
        return QuestionExtractionResult(
            question_id=question_id,
            page_number=1,
            question_type=FREE_RESPONSE,
            skipped=True,
            error="missing_segments",
        )

    crops: list[Image.Image] = []
    source_segments: list[QuestionSourceSegment] = []
    first_page_number = segments[0][0]
    for page_number, bbox_norm in segments:
        if page_number not in rendered_pages:
            continue
        page_image, page_w, page_h = rendered_pages[page_number]
        expanded_bbox_px = _expand_question_bbox_px(
            bbox_norm_to_px(bbox_norm, page_w, page_h),
            page_w=page_w,
            page_h=page_h,
        )
        region = crop_region(page_image, expanded_bbox_px)
        region, trim_bbox_px = _trim_question_region_with_bbox(region)
        if region.width <= 0 or region.height <= 0:
            continue
        source_bbox_px = clamp_bbox_px(
            (
                expanded_bbox_px[0] + trim_bbox_px[0],
                expanded_bbox_px[1] + trim_bbox_px[1],
                expanded_bbox_px[0] + trim_bbox_px[2],
                expanded_bbox_px[1] + trim_bbox_px[3],
            ),
            page_w=page_w,
            page_h=page_h,
        )
        source_segments.append(
            QuestionSourceSegment(
                page_number=page_number,
                bbox_norm=tuple(
                    (
                        source_bbox_px[0] / page_w, source_bbox_px[1] / page_h,
                        (source_bbox_px[2] - source_bbox_px[0]) / page_w, (source_bbox_px[3] - source_bbox_px[1]) / page_h
                    )
                ),
            )
        )
        crops.append(region.convert("RGB"))

    if not crops:
        return QuestionExtractionResult(
            question_id=question_id,
            page_number=first_page_number,
            question_type=FREE_RESPONSE,
            skipped=True,
            error="empty_crop",
        )

    if len(crops) == 1:
        stitched = crops[0]
    else:
        # Downstream grading expects one artifact per question, so continuation
        # crops are stacked into a single response image.
        width = max(crop.width for crop in crops)
        height = sum(crop.height for crop in crops)
        stitched = Image.new("RGB", (width, height), color=(255, 255, 255))
        y = 0
        for crop in crops:
            x = (width - crop.width) // 2
            stitched.paste(crop, (x, y))
            y += crop.height
        stitched = _trim_question_region(stitched, margin_px=8)

    out_path = sub_dir / f"{question_id}.{response_format}"
    if response_format == "pdf":
        stitched.save(out_path, format="PDF")
    else:
        stitched.save(out_path)

    return QuestionExtractionResult(
        question_id=question_id,
        page_number=first_page_number,
        question_type=_merge_question_type_votes(
            detected_question_type, classify_question_image(stitched)
        ),
        output_path=str(out_path),
        source_segments=tuple(source_segments),
    )


def _normalized_label_token(text: str) -> str:
    """Normalize OCR label text for fuzzy header-field matching."""

    return re.sub(r"[^a-z0-9]", "", text.lower())


def _looks_like_name_label(token_norm: str) -> bool:
    """Return whether a normalized token is a likely name label."""

    if token_norm in {"name", "nane", "naine", "narne"}:
        return True
    return token_norm.startswith("name") or token_norm.startswith("narn")


def _looks_like_sid_label(token_norm: str) -> bool:
    """Return whether a normalized token is a likely student ID label."""

    if token_norm in {"studentid", "sid", "id", "studentld"}:
        return True
    if token_norm.startswith("student") and ("id" in token_norm or "ld" in token_norm):
        return True
    return False


def _words_bbox(words: list[_OcrWord]) -> tuple[int, int, int, int]:
    """Return the union bbox for OCR words."""

    if not words:
        return (0, 0, 0, 0)
    return (
        min(w.left for w in words),
        min(w.top for w in words),
        max(w.right for w in words),
        max(w.bottom for w in words),
    )


def _extract_name_from_line(
    line_words: list[_OcrWord],
    *,
    page_w_est: int,
) -> tuple[str, float, tuple[int, int, int, int]] | None:
    """Parse a name value from one OCR line when a name label is present."""

    if not line_words:
        return None

    tokens = [w.text for w in line_words]
    norms = [_normalized_label_token(token) for token in tokens]

    label_idx: int | None = None
    for i, norm in enumerate(norms):
        if _looks_like_name_label(norm):
            label_idx = i
            break
    if label_idx is None:
        return None

    if page_w_est > 0 and (line_words[label_idx].left / page_w_est) > 0.42:
        return None

    value_words: list[_OcrWord] = []
    label_token = tokens[label_idx]
    if ":" in label_token:
        suffix = label_token.split(":", 1)[1].strip()
        if suffix:
            value_words.append(
                _OcrWord(
                    text=suffix,
                    conf=line_words[label_idx].conf,
                    left=line_words[label_idx].left,
                    top=line_words[label_idx].top,
                    width=line_words[label_idx].width,
                    height=line_words[label_idx].height,
                    line_id=line_words[label_idx].line_id,
                )
            )

    for i in range(label_idx + 1, len(line_words)):
        norm = norms[i]
        if _looks_like_sid_label(norm) or norm == "student":
            # Adjacent header fields often share a line; stop before the next
            # label so the name value does not swallow the ID.
            break
        value_words.append(line_words[i])

    if not value_words:
        return None

    candidate = _normalize_name_text(" ".join(w.text for w in value_words))
    letters = len(re.findall(r"[A-Za-z]", candidate))
    if letters < 2:
        return None

    parts = [part for part in candidate.split(" ") if part]
    if len(parts) > 6:
        return None

    avg_conf = sum(w.conf for w in value_words) / len(value_words)
    conf = max(avg_conf, 72.0 if letters >= 4 else avg_conf)
    return candidate, conf, _words_bbox(value_words)


def _extract_sid_from_line(
    line_words: list[_OcrWord],
    *,
    page_w_est: int,
) -> tuple[str, float, tuple[int, int, int, int]] | None:
    """Parse a student ID value from one OCR line when an ID label is present."""

    if not line_words:
        return None

    tokens = [w.text for w in line_words]
    norms = [_normalized_label_token(token) for token in tokens]

    label_end_idx: int | None = None
    for i, norm in enumerate(norms):
        if (
            norm == "student"
            and i + 1 < len(norms)
            and _looks_like_sid_label(norms[i + 1])
        ):
            label_end_idx = i + 1
            break
        if _looks_like_sid_label(norm):
            label_end_idx = i
            break

    if label_end_idx is None:
        return None

    if page_w_est > 0 and (line_words[label_end_idx].left / page_w_est) > 0.42:
        return None

    value_words: list[_OcrWord] = []
    label_token = tokens[label_end_idx]
    if ":" in label_token:
        suffix = label_token.split(":", 1)[1].strip()
        if suffix:
            value_words.append(
                _OcrWord(
                    text=suffix,
                    conf=line_words[label_end_idx].conf,
                    left=line_words[label_end_idx].left,
                    top=line_words[label_end_idx].top,
                    width=line_words[label_end_idx].width,
                    height=line_words[label_end_idx].height,
                    line_id=line_words[label_end_idx].line_id,
                )
            )

    for i in range(label_end_idx + 1, len(line_words)):
        norm = norms[i]
        if _looks_like_name_label(norm):
            # Stop at a neighboring name label for the same reason: headers may
            # be compact and label/value pairs can run together.
            break
        value_words.append(line_words[i])

    if not value_words:
        return None

    digits = _normalize_sid_text(" ".join(w.text for w in value_words))
    if len(digits) < 5 or len(digits) > 12:
        return None

    avg_conf = sum(w.conf for w in value_words) / len(value_words)
    conf = max(avg_conf, 78.0 if len(digits) >= 8 else avg_conf)
    return digits, conf, _words_bbox(value_words)


def _extract_header_field_values(words: list[_OcrWord]) -> dict[str, OcrResult]:
    """Extract best-effort name and student ID values from OCR header text."""

    if not words:
        return {}

    line_map = _group_words_by_line(words)
    ordered_lines = sorted(
        line_map.values(),
        key=lambda line_words: (
            min(w.top for w in line_words),
            min(w.left for w in line_words),
        ),
    )

    page_w_est = max(w.right for w in words)

    best_name: tuple[float, OcrResult] | None = None
    best_sid: tuple[float, OcrResult] | None = None

    for line_words in ordered_lines:
        line_left = min(w.left for w in line_words)
        if page_w_est > 0 and (line_left / page_w_est) > 0.48:
            continue

        name_candidate = _extract_name_from_line(line_words, page_w_est=page_w_est)
        if name_candidate is not None:
            text, conf, bbox = name_candidate
            # Confidence ranks competing parses, but field crops are still saved
            # for review rather than treated as verified identity data.
            score = conf + len(re.findall(r"[A-Za-z]", text)) * 2.0
            if " " in text:
                score += 6.0
            candidate = OcrResult(text=text, confidence=conf, bbox_px=bbox)
            if best_name is None or score > best_name[0]:
                best_name = (score, candidate)

        sid_candidate = _extract_sid_from_line(line_words, page_w_est=page_w_est)
        if sid_candidate is not None:
            text, conf, bbox = sid_candidate
            score = conf + min(12, len(text)) * 4.0
            if len(text) >= 8:
                score += 12.0
            candidate = OcrResult(text=text, confidence=conf, bbox_px=bbox)
            if best_sid is None or score > best_sid[0]:
                best_sid = (score, candidate)

    out: dict[str, OcrResult] = {}
    if best_name is not None:
        out["name"] = best_name[1]
    if best_sid is not None:
        out["sid"] = best_sid[1]
    return out


def _find_label_word(words: list[_OcrWord], target: str) -> _OcrWord | None:
    """Find the first likely OCR word for a requested header label."""

    normalized_target = target.lower()
    if normalized_target == "name":
        candidates = [
            w for w in words if _looks_like_name_label(_normalized_label_token(w.text))
        ]
    else:
        candidates = [
            w for w in words if _normalized_label_token(w.text) == normalized_target
        ]
    if not candidates:
        return None
    candidates.sort(key=lambda w: (w.top, w.left))
    return candidates[0]


def _find_student_id_label(words: list[_OcrWord]) -> tuple[int, int, int, int] | None:
    """Find a likely student ID label bbox, including split 'student id' text."""

    line_map = _group_words_by_line(words)
    for line_words in line_map.values():
        texts = [_normalized_label_token(w.text) for w in line_words]
        for i, txt in enumerate(texts):
            if txt != "student" and not txt.startswith("student"):
                continue
            for j in range(i + 1, min(i + 4, len(line_words))):
                if _looks_like_sid_label(texts[j]):
                    left = min(line_words[i].left, line_words[j].left)
                    top = min(line_words[i].top, line_words[j].top)
                    right = max(line_words[i].right, line_words[j].right)
                    bottom = max(line_words[i].bottom, line_words[j].bottom)
                    return left, top, right, bottom

    for w in words:
        if _looks_like_sid_label(_normalized_label_token(w.text)):
            return w.left, w.top, w.right, w.bottom

    return None


def _value_bbox_to_field_slice_px(
    value_bbox_px: tuple[int, int, int, int],
    *,
    page_w: int,
    page_h: int,
) -> tuple[int, int, int, int]:
    """Expand a detected field value bbox into a review-friendly field crop."""

    vx0, vy0, vx1, vy1 = value_bbox_px
    value_w = max(1, vx1 - vx0)
    value_h = max(1, vy1 - vy0)

    pad_left = max(
        int(round(page_w * FIELD_SLICE_PAD_LEFT_NORM)), int(round(value_w * 0.20))
    )
    pad_right = max(
        int(round(page_w * FIELD_SLICE_PAD_RIGHT_NORM)), int(round(value_w * 0.42))
    )

    x0 = vx0 - pad_left
    x1 = vx1 + pad_right

    min_width_px = int(round(page_w * FIELD_SLICE_MIN_WIDTH_NORM))
    max_width_px = int(round(page_w * FIELD_SLICE_MAX_WIDTH_NORM))
    width = x1 - x0

    if width < min_width_px:
        grow = min_width_px - width
        x0 -= int(round(grow * 0.35))
        x1 += int(round(grow * 0.65))
    elif width > max_width_px:
        center_x = (vx0 + vx1) / 2.0
        half = max_width_px / 2.0
        x0 = int(round(center_x - half * 0.95))
        x1 = int(round(center_x + half * 1.05))

    top_pad = max(
        int(round(value_h * FIELD_SLICE_TOP_PAD_FACTOR)), int(round(page_h * 0.01))
    )
    bottom_pad = max(
        int(round(value_h * FIELD_SLICE_BOTTOM_PAD_FACTOR)),
        int(round(page_h * 0.008)),
    )
    y0 = vy0 - top_pad
    y1 = vy1 + bottom_pad

    min_height_px = int(round(page_h * FIELD_SLICE_MIN_HEIGHT_NORM))
    max_height_px = int(round(page_h * FIELD_SLICE_MAX_HEIGHT_NORM))
    height = y1 - y0

    if height < min_height_px:
        grow = min_height_px - height
        y0 -= int(round(grow * 0.58))
        y1 += int(round(grow * 0.42))
    elif height > max_height_px:
        center_y = (vy0 + vy1) / 2.0
        half_h = max_height_px / 2.0
        y0 = int(round(center_y - half_h * 1.08))
        y1 = int(round(center_y + half_h * 0.92))

    return clamp_bbox_px((x0, y0, x1, y1), page_w=page_w, page_h=page_h)


def _label_to_field_bbox_px(
    label_bbox_px: tuple[int, int, int, int],
    page_w: int,
    page_h: int,
    boxes_norm: list[list[float]],
) -> tuple[int, int, int, int]:
    """Pair a field label with a nearby structural value box when possible."""

    lx0, ly0, lx1, ly1 = label_bbox_px
    label_center_y = (ly0 + ly1) / 2.0

    candidates: list[tuple[float, tuple[int, int, int, int]]] = []
    for box in boxes_norm:
        x0, y0, x1, y1 = bbox_norm_to_px(box, page_w, page_h)
        if x0 < lx1 - int(page_w * 0.02):
            continue
        if abs(((y0 + y1) / 2.0) - label_center_y) > page_h * 0.09:
            continue
        if (x1 - x0) < page_w * 0.12:
            continue
        if (y1 - y0) < page_h * 0.012 or (y1 - y0) > page_h * 0.12:
            continue
        score = abs(((y0 + y1) / 2.0) - label_center_y) - (x1 - x0) * 0.001
        candidates.append((score, (x0, y0, x1, y1)))

    if candidates:
        candidates.sort(key=lambda item: item[0])
        _, value_bbox = candidates[0]
        ux0 = min(lx0, value_bbox[0])
        uy0 = min(ly0, value_bbox[1])
        ux1 = max(lx1, value_bbox[2])
        uy1 = max(ly1, value_bbox[3])
        return _value_bbox_to_field_slice_px(
            (ux0, uy0, ux1, uy1),
            page_w=page_w,
            page_h=page_h,
        )

    # Heuristic fallback: use the label line and return a wide horizontal slice.
    return _value_bbox_to_field_slice_px(label_bbox_px, page_w=page_w, page_h=page_h)


def _detect_header_underlines(
    page_image: Image.Image,
) -> list[tuple[int, int, int, int]]:
    """Detect horizontal header underlines that can represent metadata blanks."""

    gray = np.array(page_image.convert("L"))
    page_h, page_w = gray.shape[:2]
    if page_h <= 0 or page_w <= 0:
        return []

    # Limit underline detection to the header; answer boxes later on the page
    # contain similar horizontal rules.
    scan_end = max(1, int(round(page_h * HEADER_UNDERLINE_SCAN_END_NORM)))
    header = gray[:scan_end, :]

    _, inv = cv2.threshold(header, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    kernel_width = max(25, int(round(page_w * 0.08)))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, 1))
    hlines = cv2.morphologyEx(inv, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(hlines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    raw_lines: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        width_norm = w / page_w
        height_norm = h / page_h
        y_norm = y / page_h
        if width_norm < HEADER_UNDERLINE_MIN_WIDTH_NORM:
            continue
        if width_norm > HEADER_UNDERLINE_MAX_WIDTH_NORM:
            continue
        if height_norm > HEADER_UNDERLINE_MAX_HEIGHT_NORM:
            continue
        if y_norm < HEADER_UNDERLINE_MIN_Y_NORM:
            continue
        raw_lines.append((x, y, x + w, y + h))

    if not raw_lines:
        return []

    raw_lines.sort(key=lambda bbox: (bbox[1], bbox[0]))
    merged: list[tuple[int, int, int, int]] = []
    for bbox in raw_lines:
        if not merged:
            merged.append(bbox)
            continue

        px0, py0, px1, py1 = merged[-1]
        x0, y0, x1, y1 = bbox
        same_row = abs(((py0 + py1) // 2) - ((y0 + y1) // 2)) <= max(
            10, int(page_h * 0.01)
        )
        overlaps_horizontally = x0 <= px1 + max(20, int(page_w * 0.02))
        if same_row and overlaps_horizontally:
            merged[-1] = (min(px0, x0), min(py0, y0), max(px1, x1), max(py1, y1))
            continue

        merged.append(bbox)

    return merged


def _find_matching_underline(
    underlines: list[tuple[int, int, int, int]],
    label_bbox_px: tuple[int, int, int, int],
    *,
    page_w: int,
    page_h: int,
    used_indices: set[int] | None = None,
) -> int | None:
    """Find the nearest unused underline to the right of a field label."""

    lx0, ly0, lx1, ly1 = label_bbox_px
    label_center_y = (ly0 + ly1) / 2.0
    best_idx: int | None = None
    best_score: float | None = None

    for idx, underline_bbox in enumerate(underlines):
        if used_indices is not None and idx in used_indices:
            continue

        ux0, uy0, ux1, uy1 = underline_bbox
        if ux1 <= lx1 + int(page_w * 0.04):
            continue

        underline_center_y = (uy0 + uy1) / 2.0
        y_distance = abs(underline_center_y - label_center_y)
        if y_distance > page_h * 0.09:
            continue

        score = y_distance + max(0.0, (ux0 - lx1) * 0.2)
        if best_score is None or score < best_score:
            best_score = score
            best_idx = idx

    return best_idx


def _underline_to_field_slice_px(
    underline_bbox_px: tuple[int, int, int, int],
    *,
    page_w: int,
    page_h: int,
) -> tuple[int, int, int, int]:
    """Expand a header underline into a field crop with label context."""

    ux0, uy0, ux1, uy1 = underline_bbox_px
    x0 = ux0 - int(round(page_w * HEADER_CONTEXT_LEFT_PAD_NORM))
    x1 = ux1 + int(round(page_w * HEADER_CONTEXT_RIGHT_PAD_NORM))
    y0 = uy0 - int(round(page_h * HEADER_CONTEXT_TOP_PAD_NORM))
    y1 = uy1 + int(round(page_h * HEADER_CONTEXT_BOTTOM_PAD_NORM))

    return clamp_bbox_px((x0, y0, x1, y1), page_w=page_w, page_h=page_h)


def _set_field_slices_from_underline(
    out: dict[str, dict[str, tuple[int, int, int, int]]],
    *,
    field_type: str,
    underline_bbox_px: tuple[int, int, int, int],
    page_w: int,
    page_h: int,
) -> None:
    """Store the primary field slice derived from a header underline."""

    out[field_type] = {
        "primary": _underline_to_field_slice_px(
            underline_bbox_px,
            page_w=page_w,
            page_h=page_h,
        ),
    }


def _detect_dynamic_field_slices(
    page_words: list[_OcrWord],
    page_w: int,
    page_h: int,
    boxes_norm: list[list[float]],
    *,
    page_image: Image.Image | None = None,
) -> dict[str, dict[str, tuple[int, int, int, int]]]:
    """
    Infer first-page metadata field crops from OCR and header geometry.

    The result maps field types such as ``name`` and ``sid`` to pixel crop
    bboxes. It is a best-effort fallback path for templates where fields are not
    supplied by the semantic layout scanner.
    """

    out: dict[str, dict[str, tuple[int, int, int, int]]] = {}
    # Field slices are resolved from strongest to weakest evidence: label plus
    # underline, parsed value, unused underline, then generic structural boxes.
    underlines = _detect_header_underlines(page_image) if page_image is not None else []
    used_underline_indices: set[int] = set()

    name_word = _find_label_word(page_words, "name")
    if name_word is not None:
        label_bbox = (name_word.left, name_word.top, name_word.right, name_word.bottom)
        underline_idx = _find_matching_underline(
            underlines,
            label_bbox,
            page_w=page_w,
            page_h=page_h,
            used_indices=used_underline_indices,
        )
        if underline_idx is not None:
            _set_field_slices_from_underline(
                out,
                field_type="name",
                underline_bbox_px=underlines[underline_idx],
                page_w=page_w,
                page_h=page_h,
            )
            used_underline_indices.add(underline_idx)
        else:
            out["name"] = {
                "primary": _label_to_field_bbox_px(
                    label_bbox, page_w, page_h, boxes_norm
                )
            }

    sid_label = _find_student_id_label(page_words)
    if sid_label is not None:
        underline_idx = _find_matching_underline(
            underlines,
            sid_label,
            page_w=page_w,
            page_h=page_h,
            used_indices=used_underline_indices,
        )
        if underline_idx is not None:
            _set_field_slices_from_underline(
                out,
                field_type="sid",
                underline_bbox_px=underlines[underline_idx],
                page_w=page_w,
                page_h=page_h,
            )
            used_underline_indices.add(underline_idx)
        else:
            out["sid"] = {
                "primary": _label_to_field_bbox_px(
                    sid_label, page_w, page_h, boxes_norm
                )
            }

    parsed_fields = _extract_header_field_values(page_words)
    for field_type in ("name", "sid"):
        if field_type in out:
            continue
        parsed = parsed_fields.get(field_type)
        if parsed is None or parsed.bbox_px == (0, 0, 0, 0):
            continue
        out[field_type] = {
            "primary": _value_bbox_to_field_slice_px(
                parsed.bbox_px,
                page_w=page_w,
                page_h=page_h,
            )
        }

    if underlines:
        ordered_underlines = sorted(
            [
                (idx, bbox)
                for idx, bbox in enumerate(underlines)
                if idx not in used_underline_indices
            ],
            key=lambda item: (item[1][1], item[1][0]),
        )

        if "name" not in out:
            name_candidate: tuple[int, tuple[int, int, int, int]] | None = None
            if "sid" in out:
                sid_primary = out["sid"]["primary"]
                sid_center_y = (sid_primary[1] + sid_primary[3]) / 2.0
                for idx, bbox in ordered_underlines:
                    underline_center_y = (bbox[1] + bbox[3]) / 2.0
                    if underline_center_y < sid_center_y - page_h * 0.015:
                        name_candidate = (idx, bbox)
            if name_candidate is None and ordered_underlines:
                name_candidate = ordered_underlines[0]
            if name_candidate is not None:
                idx, bbox = name_candidate
                _set_field_slices_from_underline(
                    out,
                    field_type="name",
                    underline_bbox_px=bbox,
                    page_w=page_w,
                    page_h=page_h,
                )
                used_underline_indices.add(idx)
                ordered_underlines = [
                    item for item in ordered_underlines if item[0] != idx
                ]

        if "sid" not in out:
            sid_candidate: tuple[int, tuple[int, int, int, int]] | None = None
            if "name" in out:
                name_primary = out["name"]["primary"]
                name_center_y = (name_primary[1] + name_primary[3]) / 2.0
                for idx, bbox in ordered_underlines:
                    underline_center_y = (bbox[1] + bbox[3]) / 2.0
                    if underline_center_y > name_center_y + page_h * 0.015:
                        sid_candidate = (idx, bbox)
                        break
            if sid_candidate is None and ordered_underlines:
                sid_candidate = ordered_underlines[-1]
            if sid_candidate is not None:
                idx, bbox = sid_candidate
                _set_field_slices_from_underline(
                    out,
                    field_type="sid",
                    underline_bbox_px=bbox,
                    page_w=page_w,
                    page_h=page_h,
                )

    candidate_boxes: list[list[float]] = []
    for box in boxes_norm:
        x, y, w, h = box
        if w < 0.18 or w > 0.88:
            continue
        if h < 0.015 or h > 0.16:
            continue
        if x > 0.65:
            continue
        candidate_boxes.append(box)

    candidate_boxes.sort(key=lambda box: (box[1], box[0]))
    if len(candidate_boxes) >= 2:
        name_box = candidate_boxes[0]
        sid_box = candidate_boxes[1]
        if "name" not in out:
            out["name"] = {
                "primary": _value_bbox_to_field_slice_px(
                    bbox_norm_to_px(name_box, page_w, page_h),
                    page_w=page_w,
                    page_h=page_h,
                )
            }
        if "sid" not in out:
            out["sid"] = {
                "primary": _value_bbox_to_field_slice_px(
                    bbox_norm_to_px(sid_box, page_w, page_h),
                    page_w=page_w,
                    page_h=page_h,
                )
            }

    return out


def _detect_dynamic_field_regions(
    page_words: list[_OcrWord],
    page_w: int,
    page_h: int,
    boxes_norm: list[list[float]],
    *,
    page_image: Image.Image | None = None,
) -> dict[str, tuple[int, int, int, int]]:
    """Compatibility wrapper returning each detected field's primary crop."""

    slices = _detect_dynamic_field_slices(
        page_words,
        page_w,
        page_h,
        boxes_norm,
        page_image=page_image,
    )
    return {
        field_type: field_slices["primary"]
        for field_type, field_slices in slices.items()
        if "primary" in field_slices
    }


def _save_field_crop(
    *,
    page_image: Image.Image,
    bbox_px: tuple[int, int, int, int],
    field_type: str,
    field_crop_dir: Path,
    page_number: int,
    suffix: str = "",
) -> str | None:
    """Save a field crop into this submission's artifact folder."""

    crop = crop_region(page_image, bbox_px)
    if crop.width <= 0 or crop.height <= 0:
        return None

    filename = f"{field_type}_p{page_number}{suffix}.png"

    field_crop_dir.mkdir(parents=True, exist_ok=True)
    out_path = field_crop_dir / filename
    crop.save(out_path)

    return str(out_path)


def _default_auto_fields() -> list[dict]:
    """Return metadata fields that are always attempted during extraction."""

    return [
        {
            "id": "student_name",
            "type": "name",
            "required": False,
        },
        {
            "id": "student_id",
            "type": "sid",
            "required": False,
        },
    ]


def make_review_reason(result: SubmissionResult, config: OcrConfig) -> None:
    """Populate review flags from validation failures on a submission result."""

    _ = config
    result.needs_review = False
    result.review_reasons = []

    if not result.version_validated:
        result.needs_review = True
        result.review_reasons.append(result.version_error or "version_invalid")

    for fr in result.fields:
        if fr.validated:
            continue
        result.needs_review = True
        result.review_reasons.append(f"{fr.field_id}: {fr.error or 'unknown'}")


def _final_question_debug_bboxes_by_page(
    layout: SubmissionLayout,
    rendered_pages: dict[int, tuple[Image.Image, int, int]],
) -> dict[int, list[tuple[str, tuple[int, int, int, int]]]]:
    """Compute final question crop boxes for layout debug overlays."""

    bboxes_by_page: dict[int, list[tuple[str, tuple[int, int, int, int]]]] = {}
    for question in layout.questions:
        segments = [
            (question.page_number, question.crop_bbox_norm),
            *question.continuation_segments,
        ]
        for page_number, bbox_norm in segments:
            if page_number not in rendered_pages:
                continue
            page_image, page_w, page_h = rendered_pages[page_number]
            bboxes_by_page.setdefault(page_number, []).append(
                (
                    question.question_id,
                    _question_final_crop_bbox_px(
                        page_image,
                        bbox_norm,
                        page_w,
                        page_h,
                    ),
                )
            )
    return bboxes_by_page


def process_submission_pdf(
    pdf_path: str | Path,
    config: OcrConfig,
    output_dir: str | Path,
    response_format: Literal["png", "pdf"] = "png",
    dpi: int = 300,
    split_subquestions: bool = True,
    save_layout_debug: bool = False,
) -> SubmissionResult:
    """
    Extract field and question crop artifacts from a submission PDF.

    Args:
        pdf_path: Path to the submission PDF.
        config: OCR configuration used by layout scanning.
        output_dir: Directory where this submission's artifacts are written.
        response_format: Image or PDF format for question response artifacts.
        dpi: Render resolution for OCR and saved crops.
        save_layout_debug: Whether to write JSON and overlay debug artifacts.

    Returns:
        A submission result containing artifact paths, detected question types,
        and any review reasons raised during layout or extraction.
    """

    path = Path(pdf_path)
    result = SubmissionResult(filename=path.name)

    doc = fitz.open(str(path))

    rendered_pages: dict[int, tuple[Image.Image, int, int]] = {}

    try:
        for page_idx in range(len(doc)):
            page_number = page_idx + 1
            page_image, page_w, page_h = render_page_to_image(doc, page_idx, dpi)
            rendered_pages[page_number] = (page_image, page_w, page_h)
    finally:
        doc.close()

    if not rendered_pages:
        result.version_validated = True
        return result

    # Layout detection owns crop discovery; this layer writes artifacts and
    # carries review metadata forward without re-deciding geometry.
    layout = scan_submission_layout(rendered_pages, config, split_subquestions=split_subquestions)

    result.version_validated = True
    result.version_error = None
    result.review_reasons.extend(layout.review_reasons)

    all_field_results: list[FieldExtractionResult] = []
    submission_dir = Path(output_dir)
    response_dir = submission_dir / "responses"
    field_crop_dir = submission_dir / "identifiers"
    response_dir.mkdir(parents=True, exist_ok=True)
    if (
        layout.title_region is not None
        and layout.title_region.page_number in rendered_pages
    ):
        page_image, page_w, page_h = rendered_pages[layout.title_region.page_number]
        title_crop = crop_region(
            page_image,
            bbox_norm_to_px(layout.title_region.crop_bbox_norm, page_w, page_h),
        )
        field_crop_dir.mkdir(parents=True, exist_ok=True)
        title_path = field_crop_dir / f"title_p{layout.title_region.page_number}.png"
        title_crop.save(title_path)
        result.title_crop_path = str(title_path)

    field_by_id = {field.field_id: field for field in layout.fields}
    for field_def in _default_auto_fields():
        field_id = field_def["id"]
        detected = field_by_id.get(field_id)
        if detected is None or detected.page_number not in rendered_pages:
            all_field_results.append(
                FieldExtractionResult(
                    field_id=field_id,
                    ocr_result=None,
                    validated=True,
                    crop_path=None,
                )
            )
            continue

        page_image, page_w, page_h = rendered_pages[detected.page_number]
        artifact_field_type = "id" if field_id == "student_id" else detected.field_kind
        crop_path = _save_field_crop(
            page_image=page_image,
            bbox_px=bbox_norm_to_px(detected.crop_bbox_norm, page_w, page_h),
            field_type=artifact_field_type,
            field_crop_dir=field_crop_dir,
            page_number=detected.page_number,
        )
        all_field_results.append(
            FieldExtractionResult(
                field_id=field_id,
                ocr_result=OcrResult(
                    text="",
                    confidence=detected.confidence,
                    bbox_px=(0, 0, 0, 0),
                ),
                validated=True,
                crop_path=crop_path,
            )
        )

    all_question_results: list[QuestionExtractionResult] = []
    sub_dir = response_dir

    detected_questions_sorted = sorted(
        layout.questions,
        key=lambda r: (r.question_number, r.page_number, r.crop_bbox_norm[1]),
    )

    used_ids: set[str] = set()
    final_types_by_region_key: dict[tuple, QuestionType] = {}
    for i, region in enumerate(detected_questions_sorted, start=1):
        page_number = region.page_number
        page_image, page_w, page_h = rendered_pages[page_number]
        qid = region.question_id
        if qid in used_ids:
            # Duplicate markers are kept as separate artifacts instead of
            # overwriting an earlier crop with the same question id.
            qid = f"q{region.question_number}_{i}"
        used_ids.add(qid)

        if region.continuation_segments:
            question_result = _extract_question_crop_segments(
                rendered_pages=rendered_pages,
                question_id=qid,
                segments=[
                    (page_number, region.crop_bbox_norm),
                    *region.continuation_segments,
                ],
                response_format=response_format,
                sub_dir=sub_dir,
                detected_question_type=region.question_type,
            )
            all_question_results.append(question_result)
            final_types_by_region_key[_question_region_key(region)] = (
                question_result.question_type
            )
            continue

        question_result = _extract_question_crop_norm(
            page_image=page_image,
            question_id=qid,
            bbox_norm=region.crop_bbox_norm,
            page_w=page_w,
            page_h=page_h,
            page_num=page_number,
            response_format=response_format,
            sub_dir=sub_dir,
            detected_question_type=region.question_type,
        )
        all_question_results.append(question_result)
        final_types_by_region_key[_question_region_key(region)] = (
            question_result.question_type
        )

    result.fields = all_field_results
    result.questions = all_question_results

    if save_layout_debug:
        debug_layout = _layout_with_final_question_types(
            layout,
            final_types_by_region_key,
        )
        write_layout_debug(
            debug_layout,
            rendered_pages,
            submission_dir / "layout_debug",
            question_crop_bboxes_by_page=_final_question_debug_bboxes_by_page(
                debug_layout,
                rendered_pages,
            ),
        )

    make_review_reason(result, config)
    if layout.review_reasons:
        result.needs_review = True
        result.review_reasons = list(
            dict.fromkeys([*result.review_reasons, *layout.review_reasons])
        )
    return result


def results_to_dict(result: SubmissionResult) -> dict:
    """Serialize a submission result into plain Python containers."""

    return {
        "filename": result.filename,
        "version_id": result.version_id,
        "version_confidence": result.version_confidence,
        "version_validated": result.version_validated,
        "version_error": result.version_error,
        "title_crop_path": result.title_crop_path,
        "needs_review": result.needs_review,
        "review_reasons": result.review_reasons,
        "fields": [
            {
                "field_id": r.field_id,
                "text": r.ocr_result.text if r.ocr_result else "",
                "confidence": r.ocr_result.confidence if r.ocr_result else 0.0,
                "validated": r.validated,
                "error": r.error,
                "crop_path": r.crop_path,
            }
            for r in result.fields
        ],
        "questions": [
            {
                "question_id": q.question_id,
                "page_number": q.page_number,
                "question_type": q.question_type,
                "output_path": q.output_path,
                "skipped": q.skipped,
                "error": q.error,
                "source_segments": [
                    {
                        "page_number": segment.page_number,
                        "bbox_norm": list(segment.bbox_norm),
                    }
                    for segment in q.source_segments
                ],
            }
            for q in result.questions
        ],
    }
