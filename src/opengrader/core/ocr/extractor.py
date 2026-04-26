from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Callable, Literal

import cv2
import fitz
import numpy as np
import pytesseract
from PIL import Image

from opengrader.core.question_types import FREE_RESPONSE, QuestionType

from .alignment import preprocess_for_ocr
from .question_classifier import classify_question_image
from .scanner import scan_template

ConfidenceThreshold = float

DEFAULT_CONFIDENCE_THRESHOLD = 60.0
MIN_TEXT_LENGTH = 1
MAX_TEXT_LENGTH = 1000

QUESTION_START_MARGIN_NORM = 0.008
QUESTION_END_MARGIN_NORM = 0.025
QUESTION_DEFAULT_PAGE_END_NORM = 0.965
QUESTION_MAX_END_NORM = 0.988
QUESTION_DEFAULT_X0_NORM = 0.06
QUESTION_DEFAULT_X1_NORM = 0.92
QUESTION_SPAN_TRIGGER_Y_NORM = 0.84
QUESTION_SPAN_NEXT_PAGE_START_NORM = 0.04
QUESTION_CROP_PAD_X_NORM = 0.015
QUESTION_CROP_PAD_TOP_NORM = 0.004
QUESTION_CROP_PAD_BOTTOM_NORM = 0.008
QUESTION_BOX_MIN_WIDTH_NORM = 0.36
QUESTION_BOX_MIN_HEIGHT_NORM = 0.05
QUESTION_BOX_MAX_HEIGHT_NORM = 0.72
QUESTION_BOX_PAGE_BORDER_Y_NORM = 0.055
QUESTION_BOX_MARKER_REL_Y_MAX = 0.34
QUESTION_NEXT_STRUCTURAL_MIN_GAP_NORM = 0.05
QUESTION_NEXT_STRUCTURAL_X_MARGIN_NORM = 0.06
QUESTION_STRUCTURAL_USE_MATCHED_BOX_MAX_HEIGHT_NORM = 0.55
QUESTION_STRUCTURAL_MATCHED_BOX_FRACTION = 0.75
QUESTION_TRIM_DARK_THRESHOLD = 232
QUESTION_TRIM_MIN_COMPONENT_AREA = 18
QUESTION_TRIM_MIN_COMPONENT_WIDTH = 3
QUESTION_TRIM_MIN_COMPONENT_HEIGHT = 3
QUESTION_TRIM_LINE_COMPONENT_MIN_SPAN_NORM = 0.65
QUESTION_TRIM_LINE_COMPONENT_MAX_THICKNESS_NORM = 0.04
QUESTION_TRIM_MIN_AREA_RATIO = 0.01

FIELD_SLICE_MIN_WIDTH_NORM = 0.42
FIELD_SLICE_MAX_WIDTH_NORM = 0.82
FIELD_SLICE_PAD_LEFT_NORM = 0.02
FIELD_SLICE_PAD_RIGHT_NORM = 0.15
FIELD_SLICE_TOP_PAD_FACTOR = 1.9
FIELD_SLICE_BOTTOM_PAD_FACTOR = 1.45
FIELD_SLICE_MIN_HEIGHT_NORM = 0.03
FIELD_SLICE_MAX_HEIGHT_NORM = 0.145
HEADER_UNDERLINE_SCAN_END_NORM = 0.42
HEADER_UNDERLINE_MIN_WIDTH_NORM = 0.18
HEADER_UNDERLINE_MAX_WIDTH_NORM = 0.88
HEADER_UNDERLINE_MAX_HEIGHT_NORM = 0.012
HEADER_UNDERLINE_MIN_Y_NORM = 0.06
HEADER_CONTEXT_LEFT_PAD_NORM = 0.14
HEADER_CONTEXT_RIGHT_PAD_NORM = 0.02
HEADER_CONTEXT_TOP_PAD_NORM = 0.034
HEADER_CONTEXT_BOTTOM_PAD_NORM = 0.012
HEADER_FOCUS_LEFT_PAD_NORM = 0.002
HEADER_FOCUS_RIGHT_PAD_NORM = 0.002
HEADER_FOCUS_TOP_PAD_NORM = 0.002
HEADER_FOCUS_BOTTOM_PAD_NORM = 0.002

_QUESTION_MARKER_RE = re.compile(r"^(\d{1,3})[\.)]$")
_QUESTION_NUM_ONLY_RE = re.compile(r"^\d{1,3}$")


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


@dataclass
class SubmissionResult:
    filename: str
    version_id: str | None = None
    version_confidence: float | None = None
    version_validated: bool = False
    version_error: str | None = None
    fields: list[FieldExtractionResult] = field(default_factory=list)
    questions: list[QuestionExtractionResult] = field(default_factory=list)
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)


@dataclass
class OcrConfig:
    lang: str = "eng"
    psm: int = 6
    whitelist: str | None = None
    deskew: bool = True
    confidence_threshold: ConfidenceThreshold = DEFAULT_CONFIDENCE_THRESHOLD
    text_cleanup_fn: Callable[[str], str] | None = None


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


@dataclass(frozen=True)
class _DetectedQuestionRegion:
    page_number: int
    question_number: int
    bbox_norm: list[float]
    continuation: tuple[int, list[float]] | None = None


def _override_config(base: OcrConfig, field: dict) -> OcrConfig:
    return OcrConfig(
        lang=field.get("lang", base.lang),
        psm=field.get("psm", base.psm),
        whitelist=field.get("whitelist", base.whitelist),
        deskew=field.get("deskew", base.deskew),
        confidence_threshold=field.get(
            "confidence_threshold", base.confidence_threshold
        ),
        text_cleanup_fn=base.text_cleanup_fn,
    )


def _normalize_text(raw: str) -> str:
    return re.sub(r"\s+", " ", raw).strip()


def _normalize_name_text(raw: str) -> str:
    text = raw.replace("|", " ").replace("_", " ")
    text = text.replace("“", " ").replace("”", " ").replace("`", " ")
    text = re.sub(r"[^A-Za-z\s\-'\.]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(?i:name)\s+", "", text)
    return text


def _normalize_sid_text(raw: str) -> str:
    return "".join(re.findall(r"\d", raw))


def _pytesseract_string(image: Image.Image, *, lang: str, config: str) -> str:
    try:
        return pytesseract.image_to_string(image, lang=lang, config=config)
    except Exception:
        return ""


def _ocr_sid_robust(region: Image.Image, config: OcrConfig) -> OcrResult:
    resample = (
        Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
    )
    variants = [region]
    if region.width < 1600 and region.height < 500:
        variants.append(region.resize((region.width * 2, region.height * 2), resample))

    psm_values = [7, 8, 6, 11]
    candidates: list[tuple[float, str, float]] = []

    for image_variant in variants:
        for psm in psm_values:
            local_config = OcrConfig(
                lang=config.lang,
                psm=psm,
                whitelist="0123456789",
                deskew=False,
                confidence_threshold=0,
                text_cleanup_fn=config.text_cleanup_fn,
            )
            ocr = ocr_region(image_variant, local_config)
            processed_digits = "".join(re.findall(r"\d", ocr.text))

            raw = _pytesseract_string(
                image_variant,
                lang=config.lang,
                config=f"--psm {psm} -c tessedit_char_whitelist=0123456789",
            )
            raw_digits = "".join(re.findall(r"\d", raw))

            for digits, conf in [
                (processed_digits, ocr.confidence),
                (raw_digits, max(42.0, ocr.confidence * 0.65)),
            ]:
                if not digits:
                    continue
                exact = re.search(r"\d{9}", digits)
                normalized = exact.group(0) if exact else digits[:9]
                score = conf + (55.0 if exact else 0.0) - abs(9 - len(normalized)) * 7.0
                candidates.append((score, normalized, conf))

    if not candidates:
        return OcrResult(text="", confidence=0.0, bbox_px=(0, 0, 0, 0))

    best = max(candidates, key=lambda item: item[0])
    if len(best[1]) < 5:
        return OcrResult(text="", confidence=max(0.0, best[2]), bbox_px=(0, 0, 0, 0))

    final_conf = max(0.0, best[2])
    if len(best[1]) == 9:
        final_conf = max(final_conf, 82.0)
    return OcrResult(text=best[1], confidence=final_conf, bbox_px=(0, 0, 0, 0))


def _ocr_name_robust(region: Image.Image, config: OcrConfig) -> OcrResult:
    resample = (
        Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
    )
    variants = [region]
    if region.width < 2000 and region.height < 650:
        variants.append(region.resize((region.width * 2, region.height * 2), resample))

    candidates: list[tuple[float, str, float]] = []

    for image_variant in variants:
        for psm in [7, 6, 11]:
            local_config = OcrConfig(
                lang=config.lang,
                psm=psm,
                whitelist="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz -'.",
                deskew=False,
                confidence_threshold=0,
                text_cleanup_fn=config.text_cleanup_fn,
            )
            ocr = ocr_region(image_variant, local_config)
            cleaned = _normalize_name_text(ocr.text)
            if cleaned:
                letters = len(re.findall(r"[A-Za-z]", cleaned))
                score = (
                    ocr.confidence + letters * 3.0 + (8.0 if " " in cleaned else 0.0)
                )
                candidates.append((score, cleaned, ocr.confidence))

        raw = _pytesseract_string(image_variant, lang=config.lang, config="--psm 7")
        cleaned_raw = _normalize_name_text(raw)
        if cleaned_raw:
            letters = len(re.findall(r"[A-Za-z]", cleaned_raw))
            score = 32.0 + letters * 2.5 + (8.0 if " " in cleaned_raw else 0.0)
            candidates.append((score, cleaned_raw, 40.0))

    if not candidates:
        base = ocr_region(region, config)
        return OcrResult(
            text=_normalize_name_text(base.text),
            confidence=base.confidence,
            bbox_px=base.bbox_px,
        )

    best = max(candidates, key=lambda item: item[0])
    final_conf = max(0.0, best[2])
    letter_count = len(re.findall(r"[A-Za-z]", best[1]))
    if letter_count < 2:
        return OcrResult(text="", confidence=final_conf, bbox_px=(0, 0, 0, 0))
    if letter_count >= 4:
        final_conf = max(final_conf, 72.0)
    return OcrResult(text=best[1], confidence=final_conf, bbox_px=(0, 0, 0, 0))


def _ocr_field_with_heuristics(
    region: Image.Image,
    field: dict,
    config: OcrConfig,
) -> OcrResult:
    ftype = field.get("type", "text")
    if ftype == "sid":
        return _ocr_sid_robust(region, config)
    if ftype == "name":
        return _ocr_name_robust(region, config)
    return ocr_region(region, config)


def _short_text_from_result(text: str, field: dict) -> str:
    ftype = field.get("type", "text")
    if ftype == "name":
        return _normalize_name_text(text)
    if ftype == "sid":
        return _normalize_sid_text(text)


def _is_empty(text: str) -> bool:
    return len(text.strip()) < MIN_TEXT_LENGTH


def _clamp_bbox_norm(bbox: list[float]) -> list[float]:
    x, y, w, h = bbox
    x = max(0.0, min(1.0, x))
    y = max(0.0, min(1.0, y))
    w = max(0.0, min(1.0 - x, w))
    h = max(0.0, min(1.0 - y, h))
    return [x, y, w, h]


def _bbox_norm_to_px(
    bbox_norm: list[float], page_w: int, page_h: int
) -> tuple[int, int, int, int]:
    x, y, w, h = _clamp_bbox_norm(bbox_norm)
    x0 = int(round(x * page_w))
    y0 = int(round(y * page_h))
    x1 = int(round((x + w) * page_w))
    y1 = int(round((y + h) * page_h))
    x0 = max(0, min(page_w, x0))
    x1 = max(0, min(page_w, x1))
    y0 = max(0, min(page_h, y0))
    y1 = max(0, min(page_h, y1))
    if x1 <= x0:
        x1 = min(page_w, x0 + 1)
    if y1 <= y0:
        y1 = min(page_h, y0 + 1)
    return x0, y0, x1, y1


def _bbox_area_norm(bbox: list[float]) -> float:
    return max(0.0, bbox[2]) * max(0.0, bbox[3])


def render_page_to_image(
    doc: fitz.Document, page_idx: int, dpi: int = 300
) -> tuple[Image.Image, int, int]:
    page = doc[page_idx]
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    buf = pix.tobytes("png")
    img = Image.open(io.BytesIO(buf))
    w, h = img.size
    return img, w, h


def crop_region(image: Image.Image, bbox_px: tuple[int, int, int, int]) -> Image.Image:
    x0, y0, x1, y1 = bbox_px
    return image.crop((x0, y0, x1, y1))


def _expand_question_bbox_px(
    bbox_px: tuple[int, int, int, int],
    *,
    page_w: int,
    page_h: int,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = bbox_px
    x_pad = max(4, int(round(page_w * QUESTION_CROP_PAD_X_NORM)))
    top_pad = max(2, int(round(page_h * QUESTION_CROP_PAD_TOP_NORM)))
    bottom_pad = max(4, int(round(page_h * QUESTION_CROP_PAD_BOTTOM_NORM)))
    return _clamp_bbox_px(
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
    if active.size <= 1:
        return (int(active[0]), int(active[-1])) if active.size else (0, 0)

    start = int(active[0])
    end = int(active[-1])
    min_gap = max(20, int(dimension_size * min_gap_norm))

    diffs = np.diff(active)

    for i in range(len(diffs)):
        if diffs[i] > min_gap:
            before_count = i + 1
            after_count = len(active) - before_count
            before_span = int(active[i]) - start
            after_span = end - int(active[i + 1])
            if (
                before_count <= max(3, int(after_count * 0.10))
                and before_span < max(20, after_span * 0.15)
            ):
                start = int(active[i + 1])
            break

    for i in range(len(diffs) - 1, -1, -1):
        if diffs[i] > min_gap:
            before_idx = i
            after_count = len(active) - (before_idx + 1)
            before_count = before_idx + 1
            after_span = end - int(active[i + 1])
            before_span = int(active[i]) - start
            if (
                after_count <= max(3, int(before_count * 0.10))
                and after_span < max(20, before_span * 0.15)
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


def _trim_question_region(region: Image.Image, *, margin_px: int = 8) -> Image.Image:
    gray = np.array(region.convert("L"))
    if gray.size == 0:
        return region

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
    for col_idx in range(region.width - 1, max(0, region.width - _GUTTER_SCAN_WIDTH) - 1, -1):
        col_fill = np.count_nonzero(content_mask[:, col_idx]) / max(1, region.height)
        if col_fill >= _GUTTER_FILL_THRESHOLD:
            gutter_left_r = col_idx
        else:
            if gutter_left_r < region.width and (region.width - 1 - col_idx) - (region.width - gutter_left_r) <= 3:
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
        content_mask[:wipe_to, :] = 0

    _MIN_COMPONENT_AREA = max(QUESTION_TRIM_MIN_COMPONENT_AREA, 40)

    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(content_mask, 8)
    component_mask = np.zeros_like(content_mask)
    components: list[tuple[int, int, int, int, int]] = []
    for label in range(1, num_labels):
        x, y, w, h, area = stats[label]
        if area < _MIN_COMPONENT_AREA:
            continue
        if (
            w >= int(region.width * QUESTION_TRIM_LINE_COMPONENT_MIN_SPAN_NORM)
            and h <= max(12, int(region.height * QUESTION_TRIM_LINE_COMPONENT_MAX_THICKNESS_NORM))
        ):
            continue
        if (
            h >= int(region.height * QUESTION_TRIM_LINE_COMPONENT_MIN_SPAN_NORM)
            and w <= max(12, int(region.width * QUESTION_TRIM_LINE_COMPONENT_MAX_THICKNESS_NORM))
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
        if w < QUESTION_TRIM_MIN_COMPONENT_WIDTH and h < QUESTION_TRIM_MIN_COMPONENT_HEIGHT:
            continue
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
        return region

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
        return region

    trimmed = region.crop((x0, y0, x1, y1))
    original_area = max(1, region.width * region.height)
    trimmed_area = trimmed.width * trimmed.height
    area_ratio = trimmed_area / original_area

    # Guardrails: avoid over-trimming on sparse/noisy scans.
    if area_ratio < QUESTION_TRIM_MIN_AREA_RATIO or area_ratio > 0.995:
        return region

    return trimmed


def _safe_image_to_data(
    image: Image.Image,
    *,
    lang: str,
    config: str,
) -> dict:
    try:
        return pytesseract.image_to_data(
            image,
            lang=lang,
            config=config,
            output_type=pytesseract.Output.DICT,
        )
    except Exception:
        return {
            "text": [],
            "conf": [],
            "left": [],
            "top": [],
            "width": [],
            "height": [],
            "block_num": [],
            "par_num": [],
            "line_num": [],
        }


def ocr_region(
    image: Image.Image,
    config: OcrConfig,
) -> OcrResult:
    processed = preprocess_for_ocr(image, deskew=config.deskew)
    lang = config.lang
    custom_config = f"--psm {config.psm}"

    if config.whitelist:
        custom_config += f" -c tessedit_char_whitelist={config.whitelist}"

    data = _safe_image_to_data(processed, lang=lang, config=custom_config)

    words: list[tuple[str, float, tuple[int, int, int, int]]] = []
    n = len(data.get("text", []))
    for i in range(n):
        text = str(data["text"][i]).strip()
        conf_raw = data["conf"][i]
        try:
            conf = float(conf_raw)
        except (TypeError, ValueError):
            conf = -1.0
        if text and conf > 0:
            words.append(
                (
                    text,
                    conf,
                    (
                        int(data["left"][i]),
                        int(data["top"][i]),
                        int(data["width"][i]),
                        int(data["height"][i]),
                    ),
                )
            )

    if not words:
        fallback_text = ""
        try:
            fallback_text = pytesseract.image_to_string(
                processed,
                lang=lang,
                config=custom_config,
            )
        except Exception:
            fallback_text = ""
        fallback_text = _normalize_text(fallback_text)
        if fallback_text:
            return OcrResult(text=fallback_text, confidence=50.0, bbox_px=(0, 0, 0, 0))
        return OcrResult(text="", confidence=0.0, bbox_px=(0, 0, 0, 0))

    full_text = " ".join(w[0] for w in words)
    avg_conf = sum(w[1] for w in words) / len(words)
    min_x = min(w[2][0] for w in words)
    min_y = min(w[2][1] for w in words)
    max_x = max(w[2][0] + w[2][2] for w in words)
    max_y = max(w[2][1] + w[2][3] for w in words)

    if config.text_cleanup_fn:
        full_text = config.text_cleanup_fn(full_text)
    if len(full_text) > MAX_TEXT_LENGTH:
        full_text = full_text[:MAX_TEXT_LENGTH]

    return OcrResult(
        text=_normalize_text(full_text),
        confidence=avg_conf,
        bbox_px=(min_x, min_y, max_x, max_y),
    )


def _ocr_page_words(page_image: Image.Image, config: OcrConfig) -> list[_OcrWord]:
    data = _safe_image_to_data(page_image, lang=config.lang, config="--psm 6")
    words: list[_OcrWord] = []
    n = len(data.get("text", []))

    for i in range(n):
        text = str(data["text"][i]).strip()
        if not text:
            continue
        conf_raw = data["conf"][i]
        try:
            conf = float(conf_raw)
        except (TypeError, ValueError):
            conf = -1.0
        if conf <= 0:
            continue

        try:
            line_id = (
                int(data.get("block_num", [0] * n)[i]),
                int(data.get("par_num", [0] * n)[i]),
                int(data.get("line_num", [0] * n)[i]),
            )
        except (TypeError, ValueError):
            line_id = (0, 0, i)

        words.append(
            _OcrWord(
                text=text,
                conf=conf,
                left=int(data["left"][i]),
                top=int(data["top"][i]),
                width=int(data["width"][i]),
                height=int(data["height"][i]),
                line_id=line_id,
            )
        )

    return words


def _group_words_by_line(
    words: list[_OcrWord],
) -> dict[tuple[int, int, int], list[_OcrWord]]:
    lines: dict[tuple[int, int, int], list[_OcrWord]] = {}
    for w in words:
        lines.setdefault(w.line_id, []).append(w)
    for line_words in lines.values():
        line_words.sort(key=lambda word: word.left)
    return lines


def _question_number_from_token(text: str) -> int | None:
    m = _QUESTION_MARKER_RE.fullmatch(text)
    if m:
        return int(m.group(1))
    return None


def _extract_question_markers_for_page(
    page_number: int,
    words: list[_OcrWord],
    page_w: int,
    page_h: int,
) -> list[_QuestionMarker]:
    if not words:
        return []

    line_map = _group_words_by_line(words)
    markers: list[_QuestionMarker] = []

    words_sorted = sorted(words, key=lambda w: (w.top, w.left))
    for idx, word in enumerate(words_sorted):
        qnum = _question_number_from_token(word.text)
        if qnum is None and _QUESTION_NUM_ONLY_RE.fullmatch(word.text):
            for j in range(idx + 1, min(idx + 4, len(words_sorted))):
                nxt = words_sorted[j]
                if nxt.line_id != word.line_id:
                    break
                if nxt.text in {".", ")"}:
                    qnum = int(word.text)
                    break

        if qnum is None:
            continue

        x_norm = word.left / page_w if page_w else 0.0
        y_norm = word.top / page_h if page_h else 0.0

        if x_norm > 0.45:
            continue
        if y_norm < 0.07 or y_norm > 0.985:
            continue

        line_words = line_map.get(word.line_id, [])
        line_text = " ".join(w.text.lower() for w in line_words)
        has_points_hint = ("pts" in line_text) or ("point" in line_text)

        markers.append(
            _QuestionMarker(
                page_number=page_number,
                question_number=qnum,
                x_px=word.left,
                y_px=word.top,
                conf=word.conf,
                has_points_hint=has_points_hint,
            )
        )

    if not markers:
        return []

    # Deduplicate by question number on a page.
    best_by_key: dict[tuple[int, int], _QuestionMarker] = {}
    for marker in markers:
        key = (marker.page_number, marker.question_number)
        prev = best_by_key.get(key)
        if prev is None:
            best_by_key[key] = marker
            continue
        prev_score = prev.conf + (15.0 if prev.has_points_hint else 0.0)
        marker_score = marker.conf + (15.0 if marker.has_points_hint else 0.0)
        if marker_score > prev_score:
            best_by_key[key] = marker

    return sorted(best_by_key.values(), key=lambda m: (m.page_number, m.y_px, m.x_px))


def _filter_markers_by_left_margin(
    markers: list[_QuestionMarker],
) -> list[_QuestionMarker]:
    if not markers:
        return []
    x_values = [m.x_px for m in markers]
    median_x = median(x_values)

    kept: list[_QuestionMarker] = []
    for marker in markers:
        if abs(marker.x_px - median_x) <= max(80.0, median_x * 0.25):
            kept.append(marker)
            continue
        if marker.has_points_hint:
            kept.append(marker)

    if kept:
        return kept
    return markers


def _detected_boxes_norm(page_image: Image.Image) -> list[list[float]]:
    regions = scan_template(
        page_image,
        min_area_ratio=0.0015,
        max_area_ratio=0.95,
    )
    boxes = [_clamp_bbox_norm(r.bbox) for r in regions]
    boxes.sort(key=lambda b: (b[1], b[0]))
    return boxes


def _match_box_for_marker(
    marker: _QuestionMarker,
    page_w: int,
    page_h: int,
    boxes_norm: list[list[float]],
) -> list[float] | None:
    if not boxes_norm or page_w <= 0 or page_h <= 0:
        return None

    mx = marker.x_px / page_w
    my = marker.y_px / page_h

    candidates: list[tuple[float, list[float]]] = []
    for box in boxes_norm:
        x, y, w, h = box
        if h <= 0 or w <= 0:
            continue
        if w < QUESTION_BOX_MIN_WIDTH_NORM or h < QUESTION_BOX_MIN_HEIGHT_NORM:
            continue
        if y <= QUESTION_BOX_PAGE_BORDER_Y_NORM and h >= QUESTION_BOX_MAX_HEIGHT_NORM:
            continue
        if not (x - 0.03 <= mx <= x + w + 0.03):
            continue

        marker_rel_y = (my - y) / max(h, 1e-6)
        if marker_rel_y < -0.05 or marker_rel_y > QUESTION_BOX_MARKER_REL_Y_MAX:
            continue

        top_distance = abs(my - y)
        height_penalty = min(1.0, h / QUESTION_BOX_MAX_HEIGHT_NORM)
        width_penalty = max(0.0, 0.6 - min(1.0, w))
        score = (top_distance * 1.8) + (height_penalty * 0.85) + (width_penalty * 0.2)
        candidates.append((score, box))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    box = candidates[0][1]

    bx, by, bw, bh = box
    expanded = _clamp_bbox_norm(
        [bx, max(0.0, by - 0.006), bw, min(1.0 - by, bh + 0.014)]
    )
    return expanded


def _next_structural_question_top_norm(
    *,
    min_top_norm: float,
    column_x0: float,
    column_x1: float,
    boxes_norm: list[list[float]],
) -> float | None:
    candidates: list[float] = []
    for box in boxes_norm:
        x, y, w, h = box
        if w < QUESTION_BOX_MIN_WIDTH_NORM or h < QUESTION_BOX_MIN_HEIGHT_NORM:
            continue
        if y <= min_top_norm:
            continue

        box_x1 = x + w
        if box_x1 < column_x0 - QUESTION_NEXT_STRUCTURAL_X_MARGIN_NORM:
            continue
        if x > column_x1 + QUESTION_NEXT_STRUCTURAL_X_MARGIN_NORM:
            continue

        candidates.append(y)

    if not candidates:
        return None

    return min(candidates)


def _estimate_question_column_bounds(
    page_boxes: dict[int, list[list[float]]],
) -> tuple[float, float]:
    x0_values: list[float] = []
    x1_values: list[float] = []

    for boxes in page_boxes.values():
        for box in boxes:
            x, y, w, h = box
            if w < 0.45 or h < 0.08:
                continue
            if y < 0.12:
                continue
            x0_values.append(x)
            x1_values.append(x + w)

    if not x0_values or not x1_values:
        return QUESTION_DEFAULT_X0_NORM, QUESTION_DEFAULT_X1_NORM

    x0 = max(0.02, float(median(x0_values)) - 0.004)
    x1 = min(0.98, float(median(x1_values)) + 0.004)
    if x1 - x0 < 0.30:
        return QUESTION_DEFAULT_X0_NORM, QUESTION_DEFAULT_X1_NORM
    return x0, x1


def _next_question_marker(
    markers: list[_QuestionMarker],
    current: _QuestionMarker,
) -> _QuestionMarker | None:
    wanted = current.question_number + 1
    candidates = [
        marker
        for marker in markers
        if marker.question_number == wanted
        and (
            marker.page_number > current.page_number
            or (
                marker.page_number == current.page_number and marker.y_px > current.y_px
            )
        )
    ]
    if not candidates:
        return None
    return min(
        candidates, key=lambda marker: (marker.page_number, marker.y_px, marker.x_px)
    )


def _detect_question_regions(
    page_words: dict[int, list[_OcrWord]],
    page_dims: dict[int, tuple[int, int]],
    page_boxes: dict[int, list[list[float]]],
) -> list[_DetectedQuestionRegion]:
    markers: list[_QuestionMarker] = []
    for page_number, words in page_words.items():
        page_w, page_h = page_dims[page_number]
        markers.extend(
            _extract_question_markers_for_page(page_number, words, page_w, page_h)
        )

    if not markers:
        return []

    markers = _filter_markers_by_left_margin(markers)
    markers.sort(key=lambda marker: (marker.page_number, marker.y_px, marker.x_px))

    by_page: dict[int, list[_QuestionMarker]] = {}
    for marker in markers:
        by_page.setdefault(marker.page_number, []).append(marker)

    default_x0, default_x1 = _estimate_question_column_bounds(page_boxes)

    regions: list[_DetectedQuestionRegion] = []
    for page_number in sorted(by_page):
        markers_on_page = by_page[page_number]
        page_w, page_h = page_dims[page_number]
        boxes_norm = page_boxes.get(page_number, [])

        for idx, marker in enumerate(markers_on_page):
            marker_y_norm = marker.y_px / page_h if page_h else 0.0
            start_y = max(0.0, marker_y_norm - QUESTION_START_MARGIN_NORM)

            next_same_page = (
                markers_on_page[idx + 1] if idx + 1 < len(markers_on_page) else None
            )
            next_question = _next_question_marker(markers, marker)
            spans_next_page = (
                next_same_page is None
                and next_question is not None
                and next_question.page_number > page_number
                and marker_y_norm >= QUESTION_SPAN_TRIGGER_Y_NORM
            )

            if next_same_page is not None:
                next_y = next_same_page.y_px / page_h
                end_y = max(start_y + 0.02, next_y - QUESTION_END_MARGIN_NORM)
            elif spans_next_page:
                end_y = QUESTION_MAX_END_NORM
            else:
                end_y = QUESTION_DEFAULT_PAGE_END_NORM

            x0 = default_x0
            x1 = default_x1

            matched_box = _match_box_for_marker(marker, page_w, page_h, boxes_norm)
            if matched_box is not None and _bbox_area_norm(matched_box) > 0.015:
                mbx, mby, mbw, mbh = matched_box
                box_x0 = max(0.02, mbx + 0.002)
                box_x1 = min(0.98, mbx + mbw - 0.002)
                x0 = min(x0, box_x0)
                x1 = max(x1, box_x1)
                start_y = min(start_y, max(0.0, mby - 0.005))

                if not spans_next_page:
                    # Keep a little room below the drawn question box for
                    # handwritten spillover while still preventing bleed into
                    # the next question.
                    box_end_soft = min(QUESTION_MAX_END_NORM, mby + mbh + 0.012)
                    end_y = min(end_y, max(start_y + 0.03, box_end_soft))

            if not spans_next_page:
                structural_min_top = marker_y_norm + QUESTION_NEXT_STRUCTURAL_MIN_GAP_NORM
                if matched_box is not None:
                    _, matched_y, _, matched_h = matched_box
                    if matched_h <= QUESTION_STRUCTURAL_USE_MATCHED_BOX_MAX_HEIGHT_NORM:
                        structural_min_top = max(
                            structural_min_top,
                            matched_y + (matched_h * QUESTION_STRUCTURAL_MATCHED_BOX_FRACTION),
                        )

                structural_next_top = _next_structural_question_top_norm(
                    min_top_norm=structural_min_top,
                    column_x0=x0,
                    column_x1=x1,
                    boxes_norm=boxes_norm,
                )
                if structural_next_top is not None:
                    structural_end = max(
                        start_y + 0.03,
                        structural_next_top - QUESTION_END_MARGIN_NORM,
                    )
                    end_y = min(end_y, structural_end)

            end_y = min(QUESTION_MAX_END_NORM, max(start_y + 0.03, end_y))
            bbox = _clamp_bbox_norm([x0, start_y, max(0.05, x1 - x0), end_y - start_y])

            continuation: tuple[int, list[float]] | None = None
            if spans_next_page and next_question is not None:
                next_page_num = next_question.page_number
                next_dims = page_dims.get(next_page_num)
                if next_dims is not None:
                    _, next_h = next_dims
                    next_end = (next_question.y_px / next_h) - QUESTION_END_MARGIN_NORM
                    next_start = QUESTION_SPAN_NEXT_PAGE_START_NORM
                    if next_end - next_start >= 0.03:
                        continuation_bbox = _clamp_bbox_norm(
                            [
                                x0,
                                next_start,
                                max(0.05, x1 - x0),
                                next_end - next_start,
                            ]
                        )
                        continuation = (next_page_num, continuation_bbox)

            regions.append(
                _DetectedQuestionRegion(
                    page_number=page_number,
                    question_number=marker.question_number,
                    bbox_norm=bbox,
                    continuation=continuation,
                )
            )

    return regions


def validate_field_result(
    result: OcrResult, field: dict, config: OcrConfig
) -> FieldExtractionResult:
    fid = field["id"]
    ftype = field.get("type", "text")

    if result.confidence < config.confidence_threshold:
        return FieldExtractionResult(
            field_id=fid,
            ocr_result=result,
            validated=False,
            error=f"low_ocr_confidence({result.confidence:.1f} < {config.confidence_threshold})",
        )

    text = result.text
    if _is_empty(text):
        if field.get("required", False):
            return FieldExtractionResult(
                field_id=fid,
                ocr_result=result,
                validated=False,
                error="empty_required_field",
            )
        return FieldExtractionResult(field_id=fid, ocr_result=result, validated=True)

    if ftype == "version_marker":
        expected = field.get("expected", "")
        normalized = text.upper().strip()
        if normalized != expected.upper():
            return FieldExtractionResult(
                field_id=fid,
                ocr_result=result,
                expected=expected,
                validated=False,
                error=f"version_mismatch(expected={expected!r}, got={text!r})",
            )

    if ftype == "name":
        if not re.match(r"^[A-Za-z\s\-'\.]+$", text):
            return FieldExtractionResult(
                field_id=fid,
                ocr_result=result,
                validated=False,
                error=f"invalid_name_characters(got={text!r})",
            )

    if ftype == "sid":
        digit_count = len(_normalize_sid_text(text))
        if digit_count < 5:
            return FieldExtractionResult(
                field_id=fid,
                ocr_result=result,
                validated=False,
                error=f"invalid_sid_format(got={text!r})",
            )

    return FieldExtractionResult(field_id=fid, ocr_result=result, validated=True)


def _extract_question_crop_norm(
    page_image: Image.Image,
    question_id: str,
    bbox_norm: list[float],
    page_w: int,
    page_h: int,
    page_num: int,
    response_format: Literal["png", "pdf"],
    sub_dir: Path,
) -> QuestionExtractionResult:
    bbox_px = _expand_question_bbox_px(
        _bbox_norm_to_px(bbox_norm, page_w, page_h),
        page_w=page_w,
        page_h=page_h,
    )
    region = crop_region(page_image, bbox_px)
    region = _trim_question_region(region)
    question_type = classify_question_image(region)
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
    )


def _extract_question_crop_segments(
    *,
    rendered_pages: dict[int, tuple[Image.Image, int, int]],
    question_id: str,
    segments: list[tuple[int, list[float]]],
    response_format: Literal["png", "pdf"],
    sub_dir: Path,
) -> QuestionExtractionResult:
    if not segments:
        return QuestionExtractionResult(
            question_id=question_id,
            page_number=1,
            question_type=FREE_RESPONSE,
            skipped=True,
            error="missing_segments",
        )

    crops: list[Image.Image] = []
    first_page_number = segments[0][0]
    for page_number, bbox_norm in segments:
        if page_number not in rendered_pages:
            continue
        page_image, page_w, page_h = rendered_pages[page_number]
        bbox_px = _expand_question_bbox_px(
            _bbox_norm_to_px(bbox_norm, page_w, page_h),
            page_w=page_w,
            page_h=page_h,
        )
        region = crop_region(page_image, bbox_px)
        region = _trim_question_region(region)
        if region.width <= 0 or region.height <= 0:
            continue
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
        question_type=classify_question_image(stitched),
        output_path=str(out_path),
    )


def _normalized_label_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _looks_like_name_label(token_norm: str) -> bool:
    if token_norm in {"name", "nane", "naine", "narne"}:
        return True
    return token_norm.startswith("name") or token_norm.startswith("narn")


def _looks_like_sid_label(token_norm: str) -> bool:
    if token_norm in {"studentid", "sid", "id", "studentld"}:
        return True
    if token_norm.startswith("student") and ("id" in token_norm or "ld" in token_norm):
        return True
    return False


def _words_bbox(words: list[_OcrWord]) -> tuple[int, int, int, int]:
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


def _clamp_bbox_px(
    bbox_px: tuple[int, int, int, int],
    *,
    page_w: int,
    page_h: int,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = bbox_px
    x0 = max(0, min(page_w - 1, x0))
    y0 = max(0, min(page_h - 1, y0))
    x1 = max(1, min(page_w, x1))
    y1 = max(1, min(page_h, y1))
    if x1 <= x0:
        x1 = min(page_w, x0 + 1)
    if y1 <= y0:
        y1 = min(page_h, y0 + 1)
    return x0, y0, x1, y1


def _value_bbox_to_field_slice_px(
    value_bbox_px: tuple[int, int, int, int],
    *,
    page_w: int,
    page_h: int,
) -> tuple[int, int, int, int]:
    vx0, vy0, vx1, vy1 = value_bbox_px
    value_w = max(1, vx1 - vx0)
    value_h = max(1, vy1 - vy0)

    pad_left = max(int(round(page_w * FIELD_SLICE_PAD_LEFT_NORM)), int(round(value_w * 0.20)))
    pad_right = max(int(round(page_w * FIELD_SLICE_PAD_RIGHT_NORM)), int(round(value_w * 0.42)))

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

    top_pad = max(int(round(value_h * FIELD_SLICE_TOP_PAD_FACTOR)), int(round(page_h * 0.01)))
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

    return _clamp_bbox_px((x0, y0, x1, y1), page_w=page_w, page_h=page_h)


def _label_to_field_bbox_px(
    label_bbox_px: tuple[int, int, int, int],
    page_w: int,
    page_h: int,
    boxes_norm: list[list[float]],
) -> tuple[int, int, int, int]:
    lx0, ly0, lx1, ly1 = label_bbox_px
    label_center_y = (ly0 + ly1) / 2.0

    candidates: list[tuple[float, tuple[int, int, int, int]]] = []
    for box in boxes_norm:
        x0, y0, x1, y1 = _bbox_norm_to_px(box, page_w, page_h)
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


def _detect_header_underlines(page_image: Image.Image) -> list[tuple[int, int, int, int]]:
    gray = np.array(page_image.convert("L"))
    page_h, page_w = gray.shape[:2]
    if page_h <= 0 or page_w <= 0:
        return []

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
        same_row = abs(((py0 + py1) // 2) - ((y0 + y1) // 2)) <= max(10, int(page_h * 0.01))
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
    focus: bool,
) -> tuple[int, int, int, int]:
    ux0, uy0, ux1, uy1 = underline_bbox_px
    if focus:
        x0 = ux0 - int(round(page_w * HEADER_FOCUS_LEFT_PAD_NORM))
        x1 = ux1 + int(round(page_w * HEADER_FOCUS_RIGHT_PAD_NORM))
        y0 = uy0 - int(round(page_h * HEADER_FOCUS_TOP_PAD_NORM))
        y1 = uy1 + int(round(page_h * HEADER_FOCUS_BOTTOM_PAD_NORM))
    else:
        x0 = ux0 - int(round(page_w * HEADER_CONTEXT_LEFT_PAD_NORM))
        x1 = ux1 + int(round(page_w * HEADER_CONTEXT_RIGHT_PAD_NORM))
        y0 = uy0 - int(round(page_h * HEADER_CONTEXT_TOP_PAD_NORM))
        y1 = uy1 + int(round(page_h * HEADER_CONTEXT_BOTTOM_PAD_NORM))

    return _clamp_bbox_px((x0, y0, x1, y1), page_w=page_w, page_h=page_h)


def _set_field_slices_from_underline(
    out: dict[str, dict[str, tuple[int, int, int, int]]],
    *,
    field_type: str,
    underline_bbox_px: tuple[int, int, int, int],
    page_w: int,
    page_h: int,
) -> None:
    out[field_type] = {
        "primary": _underline_to_field_slice_px(
            underline_bbox_px,
            page_w=page_w,
            page_h=page_h,
            focus=False,
        ),
        "focus": _underline_to_field_slice_px(
            underline_bbox_px,
            page_w=page_w,
            page_h=page_h,
            focus=True,
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
    out: dict[str, dict[str, tuple[int, int, int, int]]] = {}
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
                "primary": _label_to_field_bbox_px(label_bbox, page_w, page_h, boxes_norm)
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
                "primary": _label_to_field_bbox_px(sid_label, page_w, page_h, boxes_norm)
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
                ordered_underlines = [item for item in ordered_underlines if item[0] != idx]

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
                    _bbox_norm_to_px(name_box, page_w, page_h),
                    page_w=page_w,
                    page_h=page_h,
                )
            }
        if "sid" not in out:
            out["sid"] = {
                "primary": _value_bbox_to_field_slice_px(
                    _bbox_norm_to_px(sid_box, page_w, page_h),
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
    page_image: Image.Image | None = None,
) -> dict[str, tuple[int, int, int, int]]:
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


def _ocr_field_from_dynamic_region(
    page_image: Image.Image,
    bbox_px: tuple[int, int, int, int],
    base_config: OcrConfig,
    field: dict,
) -> OcrResult:
    region = crop_region(page_image, bbox_px)
    field_config = _override_config(base_config, field)
    result = _ocr_field_with_heuristics(region, field, field_config)
    result.text = _short_text_from_result(result.text, field)
    return result


def _save_field_crop(
    *,
    page_image: Image.Image,
    bbox_px: tuple[int, int, int, int],
    field_type: str,
    field_crop_dir: Path,
    page_number: int,
    suffix: str = "",
    mirror_dir: Path | None = None,
) -> str | None:
    crop = crop_region(page_image, bbox_px)
    if crop.width <= 0 or crop.height <= 0:
        return None

    filename = f"{field_type}_p{page_number}{suffix}.png"

    field_crop_dir.mkdir(parents=True, exist_ok=True)
    out_path = field_crop_dir / filename
    crop.save(out_path)

    if mirror_dir is not None:
        mirror_dir.mkdir(parents=True, exist_ok=True)
        mirror_path = mirror_dir / filename
        crop.save(mirror_path)

    return str(out_path)


def _default_auto_fields() -> list[dict]:
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


def _guess_version_from_text(first_page_words: list[_OcrWord]) -> str | None:
    if not first_page_words:
        return None

    words = sorted(first_page_words, key=lambda w: (w.top, w.left))
    top_words = [w.text for w in words if w.top <= max(1, words[0].top + 950)]
    text = " ".join(top_words)

    candidates: list[str] = []
    for pattern in [r"\(([A-Z0-9])\)", r"\[([A-Z0-9])\]", r"version\s*([A-Z0-9])"]:
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            token = m.group(1).upper()
            if token:
                candidates.append(token)

    if not candidates:
        return None

    return candidates[0]


def make_review_reason(result: SubmissionResult, config: OcrConfig) -> None:
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


def _field_result_score(field_result: FieldExtractionResult) -> float:
    if field_result.ocr_result is None:
        return -1.0

    text = field_result.ocr_result.text.strip()
    base = field_result.ocr_result.confidence
    if field_result.validated:
        base += 35.0
    if text:
        base += min(20.0, float(len(text)) * 1.1)
    if field_result.crop_path:
        base += 4.0
    return base


def process_submission_pdf(
    pdf_path: str | Path,
    config: OcrConfig,
    output_dir: str | Path,
    response_format: Literal["png", "pdf"] = "png",
    dpi: int = 300,
) -> SubmissionResult:
    path = Path(pdf_path)
    result = SubmissionResult(filename=path.name)

    doc = fitz.open(str(path))

    rendered_pages: dict[int, tuple[Image.Image, int, int]] = {}
    page_words: dict[int, list[_OcrWord]] = {}
    page_boxes: dict[int, list[list[float]]] = {}

    try:
        for page_idx in range(len(doc)):
            page_number = page_idx + 1
            page_image, page_w, page_h = render_page_to_image(doc, page_idx, dpi)
            rendered_pages[page_number] = (page_image, page_w, page_h)
            page_words[page_number] = _ocr_page_words(page_image, config)
            page_boxes[page_number] = _detected_boxes_norm(page_image)
    finally:
        doc.close()

    if not rendered_pages:
        result.version_validated = True
        return result

    page_dims = {
        page_number: (w, h) for page_number, (_, w, h) in rendered_pages.items()
    }
    dynamic_regions = _detect_question_regions(page_words, page_dims, page_boxes)

    text_version_id = _guess_version_from_text(page_words.get(1, []))
    result.version_id = text_version_id
    result.version_confidence = 80.0 if text_version_id else None
    result.version_validated = True
    result.version_error = None

    all_field_results: list[FieldExtractionResult] = []
    submission_dir = Path(output_dir) / "responses" / path.stem
    submission_dir.mkdir(parents=True, exist_ok=True)
    field_crop_dir = submission_dir / "field_crops"
    field_crop_hoist_dir = Path(output_dir) / "field_crops" / path.stem
    best_field_by_id: dict[str, tuple[float, FieldExtractionResult]] = {}

    for page_number in sorted(rendered_pages):
        if page_number != 1:
            continue
        page_image, page_w, page_h = rendered_pages[page_number]
        page_word_list = page_words.get(page_number, [])
        header_field_candidates = _extract_header_field_values(page_word_list)
        dynamic_slices_by_type = _detect_dynamic_field_slices(
            page_word_list,
            page_w,
            page_h,
            page_boxes.get(page_number, []),
            page_image=page_image,
        )

        for field_def in _default_auto_fields():
            field_type = field_def.get("type", "text")
            field_config = _override_config(config, field_def)

            header_result = header_field_candidates.get(field_type)
            dynamic_slice_set = dynamic_slices_by_type.get(field_type, {})
            dynamic_bbox = dynamic_slice_set.get("primary")
            if (
                dynamic_bbox is None
                and header_result is not None
                and header_result.bbox_px != (0, 0, 0, 0)
            ):
                dynamic_bbox = _value_bbox_to_field_slice_px(
                    header_result.bbox_px,
                    page_w=page_w,
                    page_h=page_h,
                )

            crop_path: str | None = None
            dynamic_validated: FieldExtractionResult | None = None
            if dynamic_bbox is not None:
                crop_path = _save_field_crop(
                    page_image=page_image,
                    bbox_px=dynamic_bbox,
                    field_type=field_type,
                    field_crop_dir=field_crop_dir,
                    page_number=page_number,
                    mirror_dir=field_crop_hoist_dir,
                )
                focus_bbox = dynamic_slice_set.get("focus")
                if focus_bbox is not None:
                    _save_field_crop(
                        page_image=page_image,
                        bbox_px=focus_bbox,
                        field_type=field_type,
                        field_crop_dir=field_crop_dir,
                        page_number=page_number,
                        suffix="_focus",
                        mirror_dir=field_crop_hoist_dir,
                    )

                dynamic_candidates: list[FieldExtractionResult] = []
                for candidate_bbox in [dynamic_bbox, focus_bbox]:
                    if candidate_bbox is None:
                        continue
                    dynamic_ocr = _ocr_field_from_dynamic_region(
                        page_image,
                        candidate_bbox,
                        config,
                        field_def,
                    )
                    candidate = validate_field_result(
                        dynamic_ocr,
                        field_def,
                        field_config,
                    )
                    candidate.crop_path = crop_path
                    dynamic_candidates.append(candidate)

                if dynamic_candidates:
                    dynamic_validated = max(
                        dynamic_candidates,
                        key=_field_result_score,
                    )

            header_validated: FieldExtractionResult | None = None
            if header_result is not None:
                header_validated = validate_field_result(
                    header_result,
                    field_def,
                    field_config,
                )
                header_validated.crop_path = crop_path

            selected: FieldExtractionResult | None = None
            if dynamic_validated is not None and dynamic_validated.validated:
                selected = dynamic_validated
            elif header_validated is not None and header_validated.validated:
                selected = header_validated
            elif dynamic_validated is not None:
                selected = dynamic_validated
            elif header_validated is not None:
                selected = header_validated

            if selected is None:
                continue

            field_id = field_def["id"]
            score = _field_result_score(selected)
            prev = best_field_by_id.get(field_id)
            if prev is None or score > prev[0]:
                best_field_by_id[field_id] = (score, selected)

    for field_def in _default_auto_fields():
        field_id = field_def["id"]
        chosen = best_field_by_id.get(field_id)
        if chosen is not None:
            all_field_results.append(chosen[1])
            continue

        all_field_results.append(
            FieldExtractionResult(
                field_id=field_id,
                ocr_result=None,
                validated=True,
                crop_path=None,
            )
        )

    all_question_results: list[QuestionExtractionResult] = []
    sub_dir = submission_dir

    dynamic_regions_sorted = sorted(
        dynamic_regions,
        key=lambda r: (r.question_number, r.page_number, r.bbox_norm[1]),
    )

    used_ids: set[str] = set()
    for i, region in enumerate(dynamic_regions_sorted, start=1):
        page_number = region.page_number
        page_image, page_w, page_h = rendered_pages[page_number]
        qid = f"q{region.question_number}"
        if qid in used_ids:
            qid = f"q{region.question_number}_{i}"
        used_ids.add(qid)

        if region.continuation is not None:
            all_question_results.append(
                _extract_question_crop_segments(
                    rendered_pages=rendered_pages,
                    question_id=qid,
                    segments=[(page_number, region.bbox_norm), region.continuation],
                    response_format=response_format,
                    sub_dir=sub_dir,
                )
            )
            continue

        all_question_results.append(
            _extract_question_crop_norm(
                page_image=page_image,
                question_id=qid,
                bbox_norm=region.bbox_norm,
                page_w=page_w,
                page_h=page_h,
                page_num=page_number,
                response_format=response_format,
                sub_dir=sub_dir,
            )
        )

    result.fields = all_field_results
    result.questions = all_question_results

    make_review_reason(result, config)
    return result


def results_to_dict(result: SubmissionResult) -> dict:
    return {
        "filename": result.filename,
        "version_id": result.version_id,
        "version_confidence": result.version_confidence,
        "version_validated": result.version_validated,
        "version_error": result.version_error,
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
            }
            for q in result.questions
        ],
    }
