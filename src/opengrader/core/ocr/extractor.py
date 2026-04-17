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

from .alignment import AnchorRegion, detect_anchor_presence, preprocess_for_ocr
from .scanner import scan_template
from .spec import lookup_version_by_anchors, version_anchor_map

ConfidenceThreshold = float

DEFAULT_CONFIDENCE_THRESHOLD = 60.0
MIN_TEXT_LENGTH = 1
MAX_TEXT_LENGTH = 1000
DEFAULT_SID_REGEX = r"^\d{9}$"

QUESTION_START_MARGIN_NORM = 0.012
QUESTION_END_MARGIN_NORM = 0.006
QUESTION_DEFAULT_PAGE_END_NORM = 0.965
QUESTION_MAX_END_NORM = 0.988
QUESTION_DEFAULT_X0_NORM = 0.11
QUESTION_DEFAULT_X1_NORM = 0.89
QUESTION_SPAN_TRIGGER_Y_NORM = 0.84
QUESTION_SPAN_NEXT_PAGE_START_NORM = 0.04
PROMPT_EXPAND_UP_NORM = 0.02
ANSWER_EXPAND_DOWN_NORM = 0.01

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


@dataclass
class QuestionExtractionResult:
    question_id: str
    page_number: int
    output_path: str | None = None
    crop_bbox_norm: list[float] | None = None
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


def _pytesseract_string(image: Image.Image, *, lang: str, config: str) -> str:
    try:
        return pytesseract.image_to_string(image, lang=lang, config=config)
    except Exception:
        return ""


def _ocr_sid_robust(region: Image.Image, config: OcrConfig) -> OcrResult:
    resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
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
    resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
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
                score = ocr.confidence + letters * 3.0 + (8.0 if " " in cleaned else 0.0)
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
        digits = re.findall(r"\d", text)
        return "".join(digits)
    if ftype == "version_marker":
        letters = re.findall(r"[A-Za-z]", text.upper())
        expected = str(field.get("expected", "")).upper()
        if expected and expected in text.upper():
            return expected
        if expected and letters:
            expected_char = expected[0]
            for ch in letters:
                if ch == expected_char:
                    return expected_char
            ambiguous_map = {
                "B": {"B", "8"},
                "A": {"A", "4"},
                "C": {"C", "G", "0"},
                "D": {"D", "0"},
                "E": {"E", "F"},
            }
            candidate_set = ambiguous_map.get(expected_char, {expected_char})
            for ch in text.upper():
                if ch in candidate_set:
                    return expected_char
        if letters:
            return letters[0]
    return text


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


def _bbox_px_to_norm(
    bbox_px: tuple[int, int, int, int], page_w: int, page_h: int
) -> list[float]:
    x0, y0, x1, y1 = bbox_px
    if page_w <= 0 or page_h <= 0:
        return [0.0, 0.0, 1.0, 1.0]
    x = x0 / page_w
    y = y0 / page_h
    w = (x1 - x0) / page_w
    h = (y1 - y0) / page_h
    return _clamp_bbox_norm([x, y, w, h])


def _bbox_area_norm(bbox: list[float]) -> float:
    return max(0.0, bbox[2]) * max(0.0, bbox[3])


def _relative_bbox(parent_bbox: list[float], child_bbox: list[float]) -> list[float] | None:
    if len(parent_bbox) != 4 or len(child_bbox) != 4:
        return None
    px, py, pw, ph = parent_bbox
    cx, cy, cw, ch = child_bbox
    if pw <= 0 or ph <= 0:
        return None
    rx = (cx - px) / pw
    ry = (cy - py) / ph
    rw = cw / pw
    rh = ch / ph
    if rw <= 0 or rh <= 0:
        return None
    return _clamp_bbox_norm([rx, ry, rw, rh])


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


def _group_words_by_line(words: list[_OcrWord]) -> dict[tuple[int, int, int], list[_OcrWord]]:
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


def _filter_markers_by_left_margin(markers: list[_QuestionMarker]) -> list[_QuestionMarker]:
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
                marker.page_number == current.page_number
                and marker.y_px > current.y_px
            )
        )
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda marker: (marker.page_number, marker.y_px, marker.x_px))


def _detect_question_regions(
    page_words: dict[int, list[_OcrWord]],
    page_dims: dict[int, tuple[int, int]],
    page_boxes: dict[int, list[list[float]]],
) -> list[_DetectedQuestionRegion]:
    markers: list[_QuestionMarker] = []
    for page_number, words in page_words.items():
        page_w, page_h = page_dims[page_number]
        markers.extend(_extract_question_markers_for_page(page_number, words, page_w, page_h))

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

            next_same_page = markers_on_page[idx + 1] if idx + 1 < len(markers_on_page) else None
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
        pattern = field.get("regex", DEFAULT_SID_REGEX)
        if not re.fullmatch(pattern, text):
            return FieldExtractionResult(
                field_id=fid,
                ocr_result=result,
                validated=False,
                error=f"invalid_sid_format(got={text!r}, pattern={pattern!r})",
            )

    return FieldExtractionResult(field_id=fid, ocr_result=result, validated=True)


def extract_fields_from_page(
    page_image: Image.Image,
    fields: list[dict],
    config: OcrConfig,
    page_w: int,
    page_h: int,
) -> list[FieldExtractionResult]:
    results = []
    for f in fields:
        if "bbox" not in f:
            validated = not bool(f.get("required", False))
            err = None if validated else "missing_bbox"
            results.append(
                FieldExtractionResult(
                    field_id=f["id"],
                    ocr_result=None,
                    validated=validated,
                    error=err,
                )
            )
            continue
        bbox_px = _bbox_norm_to_px(f["bbox"], page_w, page_h)
        region = crop_region(page_image, bbox_px)
        field_config = _override_config(config, f)
        ocr_result = _ocr_field_with_heuristics(region, f, field_config)
        ocr_result.text = _short_text_from_result(ocr_result.text, f)
        field_result = validate_field_result(ocr_result, f, field_config)
        results.append(field_result)
    return results


def extract_version_field(spec: dict) -> dict | None:
    for page_dict in spec.get("pages", []):
        for f in page_dict.get("fields", []):
            if f.get("type") == "version_marker":
                return f
    return None


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
        crop_bbox_norm=_bbox_px_to_norm(bbox_px, page_w, page_h),
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
    first_crop_bbox_norm: list[float] | None = None

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
        if first_crop_bbox_norm is None:
            first_crop_bbox_norm = _bbox_px_to_norm(bbox_px, page_w, page_h)

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
        crop_bbox_norm=first_crop_bbox_norm,
    )


def _compute_spec_question_crop_bbox(question: dict) -> list[float]:
    container_bbox = question.get("full_bbox") or question.get("bbox")
    if not isinstance(container_bbox, list) or len(container_bbox) != 4:
        return [0.0, 0.0, 1.0, 1.0]

    x, y, w, h = container_bbox
    y0 = y
    y1 = y + h

    prompt_bbox = question.get("prompt_bbox")
    if isinstance(prompt_bbox, list) and len(prompt_bbox) == 4:
        y0 = min(y0, prompt_bbox[1] - PROMPT_EXPAND_UP_NORM)

    answer_bbox = question.get("answer_bbox")
    if isinstance(answer_bbox, list) and len(answer_bbox) == 4:
        y1 = min(1.0, answer_bbox[1] + answer_bbox[3] + ANSWER_EXPAND_DOWN_NORM)

    return _clamp_bbox_norm([x, max(0.0, y0), w, max(0.01, y1 - max(0.0, y0))])


def _iter_top_level_questions(spec: dict) -> list[tuple[int, dict]]:
    ordered: list[tuple[int, dict]] = []
    for page in spec.get("pages", []):
        page_number = int(page.get("page_number", 0))
        for question in page.get("questions", []):
            ordered.append((page_number, question))
    return ordered


def _question_number_from_id(question_id: str) -> int | None:
    m = re.search(r"(\d+)", question_id)
    if not m:
        return None
    return int(m.group(1))


def _map_dynamic_regions_to_spec_questions(
    dynamic_regions: list[_DetectedQuestionRegion],
    spec: dict,
) -> dict[str, _DetectedQuestionRegion]:
    if not dynamic_regions:
        return {}

    by_number: dict[int, _DetectedQuestionRegion] = {}
    for region in dynamic_regions:
        by_number.setdefault(region.question_number, region)

    mapping: dict[str, _DetectedQuestionRegion] = {}
    ordered_questions = _iter_top_level_questions(spec)
    used_keys: set[tuple[int, int]] = set()

    # First pass: numeric match by question id.
    for _, question in ordered_questions:
        qid = str(question.get("id", ""))
        qnum = _question_number_from_id(qid)
        if qnum is None:
            continue
        region = by_number.get(qnum)
        if region is None:
            continue
        mapping[qid] = region
        used_keys.add((region.page_number, region.question_number))

    # Second pass: sequential fallback for unresolved ids.
    remaining_regions = [
        r
        for r in sorted(dynamic_regions, key=lambda v: (v.question_number, v.page_number))
        if (r.page_number, r.question_number) not in used_keys
    ]
    unresolved_qids = [
        str(q.get("id", ""))
        for _, q in ordered_questions
        if str(q.get("id", "")) not in mapping
    ]
    for qid, region in zip(unresolved_qids, remaining_regions):
        mapping[qid] = region

    return mapping


def _split_dynamic_bbox_for_subquestion(
    parent_dynamic_bbox: list[float],
    parent_spec_bbox: list[float],
    subquestion: dict,
) -> list[float]:
    sub_container = subquestion.get("full_bbox") or subquestion.get("bbox")
    if not isinstance(sub_container, list) or len(sub_container) != 4:
        return parent_dynamic_bbox

    rel = _relative_bbox(parent_spec_bbox, sub_container)
    if rel is None:
        return parent_dynamic_bbox

    px, py, pw, ph = parent_dynamic_bbox
    rx, ry, rw, rh = rel
    return _clamp_bbox_norm([px + pw * rx, py + ph * ry, pw * rw, ph * rh])


def _find_label_word(words: list[_OcrWord], target: str) -> _OcrWord | None:
    normalized_target = target.lower()
    candidates = [w for w in words if w.text.lower() == normalized_target]
    if not candidates:
        return None
    candidates.sort(key=lambda w: (w.top, w.left))
    return candidates[0]


def _find_student_id_label(words: list[_OcrWord]) -> tuple[int, int, int, int] | None:
    line_map = _group_words_by_line(words)
    for line_words in line_map.values():
        texts = [w.text.lower() for w in line_words]
        for i, txt in enumerate(texts):
            if txt != "student":
                continue
            for j in range(i + 1, min(i + 4, len(line_words))):
                if texts[j] in {"id", "id:"}:
                    left = min(line_words[i].left, line_words[j].left)
                    top = min(line_words[i].top, line_words[j].top)
                    right = max(line_words[i].right, line_words[j].right)
                    bottom = max(line_words[i].bottom, line_words[j].bottom)
                    return left, top, right, bottom

    for w in words:
        if w.text.lower() in {"id", "id:"}:
            return w.left, w.top, w.right, w.bottom

    return None


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
        if abs(((y0 + y1) / 2.0) - label_center_y) > page_h * 0.08:
            continue
        if (x1 - x0) < page_w * 0.12:
            continue
        if (y1 - y0) < page_h * 0.012 or (y1 - y0) > page_h * 0.09:
            continue
        score = abs(((y0 + y1) / 2.0) - label_center_y) - (x1 - x0) * 0.001
        candidates.append((score, (x0, y0, x1, y1)))

    if candidates:
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    # Heuristic fallback.
    label_h = max(1, ly1 - ly0)
    x0 = min(page_w - 1, lx1 + int(page_w * 0.012))
    x1 = min(page_w, x0 + int(page_w * 0.50))
    y0 = max(0, ly0 - int(label_h * 0.30))
    y1 = min(page_h, ly1 + int(label_h * 1.30))
    if x1 <= x0:
        x1 = min(page_w, x0 + 1)
    if y1 <= y0:
        y1 = min(page_h, y0 + 1)
    return (x0, y0, x1, y1)


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
            out["name"] = _bbox_norm_to_px(name_box, page_w, page_h)
        if "sid" not in out:
            out["sid"] = _bbox_norm_to_px(sid_box, page_w, page_h)

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


def _default_auto_fields() -> list[dict]:
    return [
        {
            "id": "student_name",
            "type": "name",
            "required": False,
            "psm": 7,
            "confidence_threshold": 0,
        },
        {
            "id": "student_id",
            "type": "sid",
            "required": False,
            "regex": DEFAULT_SID_REGEX,
            "whitelist": "0123456789",
            "psm": 7,
            "confidence_threshold": 0,
        },
    ]


def _guess_version_from_text(first_page_words: list[_OcrWord], spec: dict) -> str | None:
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

    versions = version_anchor_map(spec)
    if versions:
        for c in candidates:
            if c in versions:
                return c
        return None

    return candidates[0]


def detect_version_from_anchors(
    page_image: Image.Image,
    page_anchors: list[dict],
    spec: dict,
) -> str | None:
    if not version_anchor_map(spec):
        return None
    if not page_anchors:
        return None

    arr = np.array(page_image)
    page_h, page_w = arr.shape[:2]
    detected_ids: set[str] = set()

    for anchor_dict in page_anchors:
        anchor = AnchorRegion(
            id=anchor_dict["id"],
            x_norm=anchor_dict["bbox"][0],
            y_norm=anchor_dict["bbox"][1],
            w_norm=anchor_dict["bbox"][2],
            h_norm=anchor_dict["bbox"][3],
        )
        if detect_anchor_presence(arr, anchor, page_w, page_h):
            detected_ids.add(anchor.id)

    return lookup_version_by_anchors(detected_ids, spec)


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
    spec: dict | None,
    config: OcrConfig,
    output_dir: str | Path,
    response_format: Literal["png", "pdf"] = "png",
    dpi: int = 300,
) -> SubmissionResult:
    path = Path(pdf_path)
    result = SubmissionResult(filename=path.name)

    normalized_spec = spec or {}
    spec_pages = normalized_spec.get("pages", [])
    has_spec_fields = any(page.get("fields") for page in spec_pages)
    has_spec_questions = any(page.get("questions") for page in spec_pages)

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

    # Version detection (anchor-based when available, OCR fallback otherwise).
    anchor_version_id: str | None = None
    if version_anchor_map(normalized_spec):
        first_spec_page = None
        for page_dict in spec_pages:
            if page_dict.get("page_number") == 1:
                first_spec_page = page_dict
                break
        if first_spec_page is not None and 1 in rendered_pages:
            first_img, _, _ = rendered_pages[1]
            anchor_version_id = detect_version_from_anchors(
                first_img,
                first_spec_page.get("anchors", []),
                normalized_spec,
            )

    text_version_id = _guess_version_from_text(page_words.get(1, []), normalized_spec)

    versions = version_anchor_map(normalized_spec)
    if versions:
        result.version_id = anchor_version_id or text_version_id
        if result.version_id is not None:
            result.version_confidence = 100.0 if result.version_id == anchor_version_id else 80.0
            result.version_validated = True
            result.version_error = None
        else:
            result.version_confidence = 0.0
            result.version_validated = False
            result.version_error = "version_not_detected"
    else:
        result.version_id = text_version_id
        result.version_confidence = 80.0 if text_version_id else None
        result.version_validated = True
        result.version_error = None

    # Field extraction

    all_field_results: list[FieldExtractionResult] = []

    page_dict_by_number: dict[int, dict] = {
        int(page.get("page_number", 0)): page for page in spec_pages
    }

    for page_number in sorted(rendered_pages):
        page_image, page_w, page_h = rendered_pages[page_number]
        page_dict = page_dict_by_number.get(page_number, {})

        fields = page_dict.get("fields", [])
        if not fields and not has_spec_fields and page_number == 1:
            fields = _default_auto_fields()

        if not fields:
            continue

        dynamic_regions_by_type = _detect_dynamic_field_regions(
            page_words.get(page_number, []),
            page_w,
            page_h,
            page_boxes.get(page_number, []),
        )

        for field_def in fields:
            fid = field_def["id"]
            field_type = field_def.get("type", "text")
            field_config = _override_config(config, field_def)

            dynamic_bbox = dynamic_regions_by_type.get(field_type)
            if dynamic_bbox is not None and page_number == 1 and field_type in {"name", "sid"}:
                ocr_result = _ocr_field_from_dynamic_region(
                    page_image,
                    dynamic_bbox,
                    config,
                    field_def,
                )
                all_field_results.append(
                    validate_field_result(ocr_result, field_def, field_config)
                )
                continue

            if "bbox" in field_def:
                bbox_px = _bbox_norm_to_px(field_def["bbox"], page_w, page_h)
                region = crop_region(page_image, bbox_px)
                ocr_result = _ocr_field_with_heuristics(region, field_def, field_config)
                ocr_result.text = _short_text_from_result(ocr_result.text, field_def)
                all_field_results.append(
                    validate_field_result(ocr_result, field_def, field_config)
                )
                continue

            validated = not bool(field_def.get("required", False))
            err = None if validated else "field_region_not_detected"
            all_field_results.append(
                FieldExtractionResult(
                    field_id=fid,
                    ocr_result=None,
                    validated=validated,
                    error=err,
                )
            )

    # Question extraction

    all_question_results: list[QuestionExtractionResult] = []
    sub_dir = Path(output_dir) / "responses" / path.stem
    sub_dir.mkdir(parents=True, exist_ok=True)

    if has_spec_questions:
        dynamic_by_qid = _map_dynamic_regions_to_spec_questions(dynamic_regions, normalized_spec)

        for page_number, question in _iter_top_level_questions(normalized_spec):
            if page_number not in rendered_pages:
                continue
            page_image, page_w, page_h = rendered_pages[page_number]
            qid = str(question["id"])
            subquestions = question.get("subquestions", [])

            dynamic_region = dynamic_by_qid.get(qid)
            parent_crop_bbox = (
                dynamic_region.bbox_norm
                if dynamic_region is not None
                else _compute_spec_question_crop_bbox(question)
            )

            if subquestions:
                parent_spec_bbox = question.get("full_bbox") or question.get("bbox") or [
                    0.0,
                    0.0,
                    1.0,
                    1.0,
                ]
                for subquestion in subquestions:
                    sqid = str(subquestion["id"])
                    if dynamic_region is not None:
                        crop_bbox = _split_dynamic_bbox_for_subquestion(
                            parent_crop_bbox,
                            parent_spec_bbox,
                            subquestion,
                        )
                    else:
                        crop_bbox = _compute_spec_question_crop_bbox(subquestion)

                    all_question_results.append(
                        _extract_question_crop_norm(
                            page_image=page_image,
                            question_id=sqid,
                            bbox_norm=crop_bbox,
                            page_w=page_w,
                            page_h=page_h,
                            page_num=page_number,
                            response_format=response_format,
                            sub_dir=sub_dir,
                        )
                    )
                continue

            if dynamic_region is not None and dynamic_region.continuation is not None:
                segments: list[tuple[int, list[float]]] = [
                    (page_number, parent_crop_bbox),
                    dynamic_region.continuation,
                ]
                all_question_results.append(
                    _extract_question_crop_segments(
                        rendered_pages=rendered_pages,
                        question_id=qid,
                        segments=segments,
                        response_format=response_format,
                        sub_dir=sub_dir,
                    )
                )
            else:
                all_question_results.append(
                    _extract_question_crop_norm(
                        page_image=page_image,
                        question_id=qid,
                        bbox_norm=parent_crop_bbox,
                        page_w=page_w,
                        page_h=page_h,
                        page_num=page_number,
                        response_format=response_format,
                        sub_dir=sub_dir,
                    )
                )
    else:
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
            else:
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

    version_field = extract_version_field(normalized_spec) if normalized_spec else None
    result.fields = [
        fr for fr in all_field_results if fr.field_id != (version_field or {}).get("id")
    ]
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
            }
            for r in result.fields
        ],
        "questions": [
            {
                "question_id": q.question_id,
                "page_number": q.page_number,
                "output_path": q.output_path,
                "crop_bbox_norm": q.crop_bbox_norm,
                "skipped": q.skipped,
                "error": q.error,
            }
            for q in result.questions
        ],
    }
