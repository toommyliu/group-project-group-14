from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Callable, Literal

import fitz
import numpy as np
import pytesseract
from PIL import Image

from .alignment import preprocess_for_ocr
from .scanner import scan_template

ConfidenceThreshold = float

DEFAULT_CONFIDENCE_THRESHOLD = 60.0
MIN_TEXT_LENGTH = 1
MAX_TEXT_LENGTH = 1000

QUESTION_START_MARGIN_NORM = 0.012
QUESTION_END_MARGIN_NORM = 0.006
QUESTION_DEFAULT_PAGE_END_NORM = 0.965
QUESTION_MAX_END_NORM = 0.988
QUESTION_DEFAULT_X0_NORM = 0.11
QUESTION_DEFAULT_X1_NORM = 0.89
QUESTION_SPAN_TRIGGER_Y_NORM = 0.84
QUESTION_SPAN_NEXT_PAGE_START_NORM = 0.04

FIELD_SLICE_LEFT_MARGIN_NORM = 0.045
FIELD_SLICE_RIGHT_MARGIN_NORM = 0.965
FIELD_SLICE_MIN_WIDTH_NORM = 0.68
FIELD_SLICE_VERTICAL_PAD_FACTOR = 1.35
FIELD_SLICE_MIN_HEIGHT_NORM = 0.026
FIELD_SLICE_MAX_HEIGHT_NORM = 0.12

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


def _trim_question_region(region: Image.Image, *, margin_px: int = 12) -> Image.Image:
    """Trim excess white border while preserving response content."""
    gray = np.array(region.convert("L"))
    if gray.size == 0:
        return region

    dark_mask = gray < 238
    coords = np.argwhere(dark_mask)
    if coords.size == 0:
        return region

    y0, x0 = coords.min(axis=0)
    y1, x1 = coords.max(axis=0)

    x0 = max(0, int(x0) - margin_px)
    y0 = max(0, int(y0) - margin_px)
    x1 = min(region.width, int(x1) + margin_px + 1)
    y1 = min(region.height, int(y1) + margin_px + 1)

    if x1 <= x0 or y1 <= y0:
        return region

    trimmed = region.crop((x0, y0, x1, y1))
    original_area = max(1, region.width * region.height)
    trimmed_area = trimmed.width * trimmed.height
    area_ratio = trimmed_area / original_area

    # Guardrails: avoid over-trimming on sparse/noisy scans.
    if area_ratio < 0.12 or area_ratio > 0.995:
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
    # scan_template does contour-based rectangle detection and works well for
    # noisy scans when max_area_ratio is relaxed.
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
        if not (x - 0.03 <= mx <= x + w + 0.03):
            continue
        if not (y - 0.03 <= my <= y + min(h * 0.35, 0.14)):
            continue
        score = abs(my - y) + (1.0 - min(1.0, w)) * 0.5
        candidates.append((score, box))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    box = candidates[0][1]

    bx, by, bw, bh = box
    expanded = _clamp_bbox_norm(
        [bx, max(0.0, by - 0.006), bw, min(1.0 - by, bh + 0.012)]
    )
    return expanded


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

                box_end = min(QUESTION_MAX_END_NORM, mby + mbh + 0.012)
                if not spans_next_page and end_y - box_end > 0.06:
                    end_y = max(start_y + 0.03, box_end)

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
    bbox_px = _bbox_norm_to_px(bbox_norm, page_w, page_h)
    region = crop_region(page_image, bbox_px)
    region = _trim_question_region(region)
    out_path = sub_dir / f"{question_id}.{response_format}"

    if response_format == "pdf":
        region.convert("RGB").save(out_path, format="PDF")
    else:
        region.save(out_path)

    return QuestionExtractionResult(
        question_id=question_id,
        page_number=page_num,
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
            skipped=True,
            error="missing_segments",
        )

    crops: list[Image.Image] = []
    first_page_number = segments[0][0]
    for page_number, bbox_norm in segments:
        if page_number not in rendered_pages:
            continue
        page_image, page_w, page_h = rendered_pages[page_number]
        bbox_px = _bbox_norm_to_px(bbox_norm, page_w, page_h)
        region = crop_region(page_image, bbox_px)
        region = _trim_question_region(region)
        if region.width <= 0 or region.height <= 0:
            continue
        crops.append(region.convert("RGB"))

    if not crops:
        return QuestionExtractionResult(
            question_id=question_id,
            page_number=first_page_number,
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
        output_path=str(out_path),
    )


def _normalized_label_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


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
) -> tuple[str, float, tuple[int, int, int, int]] | None:
    if not line_words:
        return None

    tokens = [w.text for w in line_words]
    norms = [_normalized_label_token(token) for token in tokens]

    label_idx: int | None = None
    for i, norm in enumerate(norms):
        if norm == "name" or norm.startswith("name"):
            label_idx = i
            break
    if label_idx is None:
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
        if norm in {"student", "studentid", "sid", "id"}:
            break
        value_words.append(line_words[i])

    if not value_words:
        return None

    candidate = _normalize_name_text(" ".join(w.text for w in value_words))
    letters = len(re.findall(r"[A-Za-z]", candidate))
    if letters < 2:
        return None

    avg_conf = sum(w.conf for w in value_words) / len(value_words)
    conf = max(avg_conf, 72.0 if letters >= 4 else avg_conf)
    return candidate, conf, _words_bbox(value_words)


def _extract_sid_from_line(
    line_words: list[_OcrWord],
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
            and norms[i + 1]
            in {
                "id",
                "studentid",
                "sid",
            }
        ):
            label_end_idx = i + 1
            break
        if norm in {"studentid", "sid", "id"}:
            label_end_idx = i
            break

    if label_end_idx is None:
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
        if norm == "name":
            break
        value_words.append(line_words[i])

    if not value_words:
        return None

    digits = _normalize_sid_text(" ".join(w.text for w in value_words))
    if len(digits) < 5:
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

    top_cutoff = max(w.bottom for w in words) * 0.42

    best_name: tuple[float, OcrResult] | None = None
    best_sid: tuple[float, OcrResult] | None = None

    for line_words in ordered_lines:
        line_top = min(w.top for w in line_words)
        if line_top > top_cutoff:
            continue
        name_candidate = _extract_name_from_line(line_words)
        if name_candidate is not None:
            text, conf, bbox = name_candidate
            score = conf + len(re.findall(r"[A-Za-z]", text)) * 2.0
            if " " in text:
                score += 6.0
            candidate = OcrResult(text=text, confidence=conf, bbox_px=bbox)
            if best_name is None or score > best_name[0]:
                best_name = (score, candidate)

        sid_candidate = _extract_sid_from_line(line_words)
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
            if txt != "student":
                continue
            for j in range(i + 1, min(i + 4, len(line_words))):
                if texts[j] in {"id", "studentid", "sid"}:
                    left = min(line_words[i].left, line_words[j].left)
                    top = min(line_words[i].top, line_words[j].top)
                    right = max(line_words[i].right, line_words[j].right)
                    bottom = max(line_words[i].bottom, line_words[j].bottom)
                    return left, top, right, bottom

    for w in words:
        if _normalized_label_token(w.text) in {"id", "studentid", "sid"}:
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
    value_h = max(1, vy1 - vy0)

    x0 = int(round(page_w * FIELD_SLICE_LEFT_MARGIN_NORM))
    x1 = int(round(page_w * FIELD_SLICE_RIGHT_MARGIN_NORM))
    min_width_px = int(round(page_w * FIELD_SLICE_MIN_WIDTH_NORM))
    if x1 - x0 < min_width_px:
        x0 = max(0, page_w - min_width_px)
        x1 = page_w

    center_y = (vy0 + vy1) / 2.0
    pad_y = int(round(value_h * FIELD_SLICE_VERTICAL_PAD_FACTOR))
    min_height_px = int(round(page_h * FIELD_SLICE_MIN_HEIGHT_NORM))
    max_height_px = int(round(page_h * FIELD_SLICE_MAX_HEIGHT_NORM))
    target_h = min(max(max(value_h + 2 * pad_y, min_height_px), value_h + 2), max_height_px)
    y0 = int(round(center_y - target_h / 2.0))
    y1 = y0 + target_h

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


def _detect_dynamic_field_regions(
    page_words: list[_OcrWord],
    page_w: int,
    page_h: int,
    boxes_norm: list[list[float]],
) -> dict[str, tuple[int, int, int, int]]:
    out: dict[str, tuple[int, int, int, int]] = {}

    name_word = _find_label_word(page_words, "name")
    if name_word is not None:
        label_bbox = (name_word.left, name_word.top, name_word.right, name_word.bottom)
        out["name"] = _label_to_field_bbox_px(label_bbox, page_w, page_h, boxes_norm)

    sid_label = _find_student_id_label(page_words)
    if sid_label is not None:
        out["sid"] = _label_to_field_bbox_px(sid_label, page_w, page_h, boxes_norm)

    # Fallback: infer header field boxes directly from detected rectangles,
    # useful when label OCR fails on noisy scans.
    candidate_boxes: list[list[float]] = []
    for box in boxes_norm:
        x, y, w, h = box
        if y > 0.45:
            continue
        if w < 0.20 or w > 0.75:
            continue
        if h < 0.02 or h > 0.10:
            continue
        candidate_boxes.append(box)

    candidate_boxes.sort(key=lambda box: (box[1], box[0]))
    if len(candidate_boxes) >= 2:
        name_box = candidate_boxes[0]
        sid_box = candidate_boxes[1]
        if "name" not in out:
            out["name"] = _value_bbox_to_field_slice_px(
                _bbox_norm_to_px(name_box, page_w, page_h),
                page_w=page_w,
                page_h=page_h,
            )
        if "sid" not in out:
            out["sid"] = _value_bbox_to_field_slice_px(
                _bbox_norm_to_px(sid_box, page_w, page_h),
                page_w=page_w,
                page_h=page_h,
            )

    return out


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
) -> str | None:
    crop = crop_region(page_image, bbox_px)
    if crop.width <= 0 or crop.height <= 0:
        return None

    field_crop_dir.mkdir(parents=True, exist_ok=True)
    out_path = field_crop_dir / f"{field_type}.png"
    crop.save(out_path)
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
    field_crop_dir = Path(output_dir) / "field_crops" / path.stem
    if 1 in rendered_pages:
        page_image, page_w, page_h = rendered_pages[1]
        header_field_candidates = _extract_header_field_values(page_words.get(1, []))
        dynamic_regions_by_type = _detect_dynamic_field_regions(
            page_words.get(1, []),
            page_w,
            page_h,
            page_boxes.get(1, []),
        )

        for field_def in _default_auto_fields():
            field_type = field_def.get("type", "text")
            field_config = _override_config(config, field_def)

            header_result = header_field_candidates.get(field_type)
            dynamic_bbox = dynamic_regions_by_type.get(field_type)
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
                )
                dynamic_ocr = _ocr_field_from_dynamic_region(
                    page_image,
                    dynamic_bbox,
                    config,
                    field_def,
                )
                dynamic_validated = validate_field_result(
                    dynamic_ocr,
                    field_def,
                    field_config,
                )
                dynamic_validated.crop_path = crop_path

            header_validated: FieldExtractionResult | None = None
            if header_result is not None:
                header_validated = validate_field_result(
                    header_result,
                    field_def,
                    field_config,
                )
                header_validated.crop_path = crop_path

            selected: FieldExtractionResult
            if dynamic_validated is not None and dynamic_validated.validated:
                selected = dynamic_validated
            elif header_validated is not None and header_validated.validated:
                selected = header_validated
            elif dynamic_validated is not None:
                selected = dynamic_validated
            elif header_validated is not None:
                selected = header_validated
            else:
                selected = FieldExtractionResult(
                    field_id=field_def["id"],
                    ocr_result=None,
                    validated=True,
                    crop_path=crop_path,
                )

            all_field_results.append(selected)

    all_question_results: list[QuestionExtractionResult] = []
    sub_dir = Path(output_dir) / "responses" / path.stem
    sub_dir.mkdir(parents=True, exist_ok=True)

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
                "output_path": q.output_path,
                "skipped": q.skipped,
                "error": q.error,
            }
            for q in result.questions
        ],
    }
