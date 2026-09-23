from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pytesseract
from PIL import Image, ImageDraw

from opengrader.core.question_types import FREE_RESPONSE, QuestionType

from .constants import SUBSCRIPT_TRANSLATION
from .question_classifier import classify_question_image
from .utils import apply_grayscale

if TYPE_CHECKING:
    from .extractor import OcrConfig


@dataclass(frozen=True)
class PageTextWord:
    text: str
    confidence: float
    bbox_px: tuple[int, int, int, int]
    line_id: tuple[int, int, int]


@dataclass(frozen=True)
class PageTextLine:
    text: str
    confidence: float
    bbox_px: tuple[int, int, int, int]
    words: tuple[PageTextWord, ...]


@dataclass(frozen=True)
class LayoutPrimitive:
    kind: Literal["hline", "vline", "box", "component"]
    bbox_norm: list[float]
    confidence: float


@dataclass(frozen=True)
class DetectedFieldRegion:
    field_id: Literal["student_name", "student_id"]
    field_kind: Literal["name", "sid"]
    page_number: int
    crop_bbox_norm: list[float]
    anchor_bbox_norm: list[float] | None
    confidence: float


@dataclass(frozen=True)
class DetectedTitleRegion:
    page_number: int
    crop_bbox_norm: list[float]
    confidence: float


@dataclass(frozen=True)
class DetectedQuestionRegion:
    question_id: str
    question_number: int
    page_number: int
    crop_bbox_norm: list[float]
    continuation_segments: list[tuple[int, list[float]]]
    confidence: float
    question_type: QuestionType = FREE_RESPONSE


@dataclass(frozen=True)
class SubmissionLayout:
    title_region: DetectedTitleRegion | None
    fields: list[DetectedFieldRegion]
    questions: list[DetectedQuestionRegion]
    review_reasons: list[str] = field(default_factory=list)
    primitives_by_page: dict[int, list[LayoutPrimitive]] = field(default_factory=dict)


@dataclass(frozen=True)
class _QuestionAnchor:
    page_number: int
    question_number: int
    bbox_px: tuple[int, int, int, int]
    line_bbox_px: tuple[int, int, int, int]
    confidence: float
    suffix: str = ""

    @property
    def question_id(self) -> str:
        return f"q{self.question_number}{self.suffix}"


_QUESTION_TOKEN_RE = re.compile(
    r"^(\d{1,3})[\.)]?$"
)
_QUESTION_Q_RE = re.compile(
    r"^q(?:uestion)?\s*(\d{1,3})[\.)]?$", re.IGNORECASE
)
_TITLE_COURSE_RE = re.compile(r"\b[A-Z]{2,}\s*\d{1,4}[A-Z]?\b", re.IGNORECASE)
_TITLE_ASSIGNMENT_RE = re.compile(
    r"\b(?:quiz|midterm|exam|final|assignment|homework|hw)\b",
    re.IGNORECASE,
)
_TITLE_COMPACT_VERSION_RE = re.compile(
    r"\b(?:quiz|midterm|exam|final|assignment|homework|hw)\s*"
    r"\d+\s*[_-]\s*[A-Z0-9]+\b",
    re.IGNORECASE,
)


_HEADER_MARK_MAX_Y_NORM = (
    0.22
)
_HEADER_MARK_MIN_WIDTH_NORM = 0.06
_HEADER_MARK_MAX_WIDTH_NORM = (
    0.45
)
_TITLE_SEARCH_MAX_Y_NORM = 0.34
_QUESTION_CONTINUATION_TRIGGER_Y_NORM = (
    0.70
)
_QUESTION_CONTINUATION_TOP_NORM = 0.04
_QUESTION_CONTINUATION_MIN_HEIGHT_NORM = 0.06
_QUESTION_CONTINUATION_MAX_BOX_TOP_NORM = (
    0.38
)
_QUESTION_CONTINUATION_X_OVERLAP_MIN_RATIO = (
    0.45
)
_QUESTION_CONTINUATION_PAGE_END_NORM = (
    0.965
)


def _clamp_bbox_norm(bbox: list[float]) -> list[float]:
    x, y, w, h = bbox
    x = max(0.0, min(1.0, x))
    y = max(0.0, min(1.0, y))
    w = max(0.0, min(1.0 - x, w))
    h = max(0.0, min(1.0 - y, h))
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


def bbox_px_to_norm(
    bbox_px: tuple[int, int, int, int],
    page_w: int,
    page_h: int,
) -> list[float]:
    x0, y0, x1, y1 = bbox_px
    if page_w <= 0 or page_h <= 0:
        return [0.0, 0.0, 0.0, 0.0]
    return _clamp_bbox_norm(
        [
            x0 / page_w,
            y0 / page_h,
            max(1, x1 - x0) / page_w,
            max(1, y1 - y0) / page_h,
        ]
    )


def bbox_norm_to_px(
    bbox_norm: list[float],
    page_w: int,
    page_h: int,
) -> tuple[int, int, int, int]:
    x, y, w, h = _clamp_bbox_norm(bbox_norm)
    x0 = max(0, min(page_w - 1, int(round(x * page_w))))
    y0 = max(0, min(page_h - 1, int(round(y * page_h))))
    x1 = max(1, min(page_w, int(round((x + w) * page_w))))
    y1 = max(1, min(page_h, int(round((y + h) * page_h))))
    if x1 <= x0:
        x1 = min(page_w, x0 + 1)
    if y1 <= y0:
        y1 = min(page_h, y0 + 1)
    return x0, y0, x1, y1


def _expand_px(
    bbox: tuple[int, int, int, int],
    *,
    page_w: int,
    page_h: int,
    pad_x: int,
    pad_y: int,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = bbox
    return (
        max(0, x0 - pad_x),
        max(0, y0 - pad_y),
        min(page_w, x1 + pad_x),
        min(page_h, y1 + pad_y),
    )


def _union_px(boxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _normalize_token(text: str) -> str:
    text = text.translate(SUBSCRIPT_TRANSLATION)
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _looks_like_name_label(token_norm: str) -> bool:
    return token_norm in {"name", "nane", "naine", "narne"} or token_norm.startswith(
        ("name", "narn")
    )


def _looks_like_sid_label(token_norm: str) -> bool:
    if token_norm in {
        "studentid",
        "student1d",
        "studentld",
        "sid",
        "id",
        "1d",
        "ld",
        "i0",
        "l0",
    }:
        return True
    return token_norm.startswith("student") and (
        "id" in token_norm or "1d" in token_norm or "ld" in token_norm
    )


def _safe_image_to_data(
    image: Image.Image, *, lang: str, config: str
) -> dict[str, list[Any]]:
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


def _page_words(image: Image.Image, config: "OcrConfig") -> list[PageTextWord]:
    data = _safe_image_to_data(image, lang=config.lang, config="--psm 6")
    words: list[PageTextWord] = []
    n = len(data.get("text", []))
    for i in range(n):
        text = str(data["text"][i]).strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf <= 0:
            continue
        left = int(data["left"][i])
        top = int(data["top"][i])
        width = int(data["width"][i])
        height = int(data["height"][i])
        try:
            line_id = (
                int(data.get("block_num", [0] * n)[i]),
                int(data.get("par_num", [0] * n)[i]),
                int(data.get("line_num", [0] * n)[i]),
            )
        except (TypeError, ValueError):
            line_id = (0, 0, i)
        words.append(
            PageTextWord(
                text=text,
                confidence=conf,
                bbox_px=(left, top, left + width, top + height),
                line_id=line_id,
            )
        )
    return words


def _group_lines(words: list[PageTextWord]) -> list[PageTextLine]:
    grouped: dict[tuple[int, int, int], list[PageTextWord]] = {}
    for word in words:
        grouped.setdefault(word.line_id, []).append(word)

    lines: list[PageTextLine] = []
    for line_words in grouped.values():
        ordered = tuple(sorted(line_words, key=lambda word: word.bbox_px[0]))
        bbox = _union_px([word.bbox_px for word in ordered])
        confidence = sum(word.confidence for word in ordered) / len(ordered)
        text = " ".join(word.text for word in ordered)
        lines.append(
            PageTextLine(
                text=re.sub(r"\s+", " ", text).strip(),
                confidence=confidence,
                bbox_px=bbox,
                words=ordered,
            )
        )
    lines.sort(key=lambda line: (line.bbox_px[1], line.bbox_px[0]))
    return lines


def _merge_boxes_px(
    boxes: list[tuple[int, int, int, int]],
    *,
    max_gap_px: int,
    same_axis_px: int,
) -> list[tuple[int, int, int, int]]:
    if not boxes:
        return []
    boxes = sorted(boxes, key=lambda b: (b[1], b[0]))
    merged: list[tuple[int, int, int, int]] = []
    for box in boxes:
        if not merged:
            merged.append(box)
            continue
        px0, py0, px1, py1 = merged[-1]
        x0, y0, x1, y1 = box
        same_row = abs(((py0 + py1) // 2) - ((y0 + y1) // 2)) <= same_axis_px
        close = px0 <= x0 <= px1 + max_gap_px
        if same_row and close:
            merged[-1] = (min(px0, x0), min(py0, y0), max(px1, x1), max(py1, y1))
        else:
            merged.append(box)
    return merged


def _detect_layout_primitives(image: Image.Image) -> list[LayoutPrimitive]:
    import cv2

    gray = apply_grayscale(np.array(image))
    page_h, page_w = gray.shape[:2]
    if page_w <= 0 or page_h <= 0:
        return []

    _, inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    adaptive_inv = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY_INV,
        41,
        15,
    )
    primitives: list[LayoutPrimitive] = []

    h_px: list[tuple[int, int, int, int]] = []
    # Use adaptive thresholding too; Otsu can erase faint header blanks.
    hline_passes = [
        (inv, max(32, page_w // 12), 0.10, 1.0),
        (
            adaptive_inv,
            max(36, page_w // 50),
            _HEADER_MARK_MIN_WIDTH_NORM,
            _HEADER_MARK_MAX_Y_NORM,
        ),
    ]
    for source, kernel_width, min_width_norm, max_y_norm in hline_passes:
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, 1))
        hlines = cv2.morphologyEx(source, cv2.MORPH_OPEN, h_kernel)
        h_contours, _ = cv2.findContours(
            hlines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        for contour in h_contours:
            x, y, w, h = cv2.boundingRect(contour)
            if y / page_h > max_y_norm:
                continue
            if w / page_w < min_width_norm or h / page_h > 0.025:
                continue
            h_px.append((x, y, x + w, y + h))
    for x0, y0, x1, y1 in _merge_boxes_px(
        h_px,
        max_gap_px=max(12, page_w // 80),
        same_axis_px=max(6, page_h // 400),
    ):
        if (x1 - x0) / page_w > 0.96 and (y0 < page_h * 0.04 or y1 > page_h * 0.96):
            continue
        primitives.append(
            LayoutPrimitive(
                "hline",
                bbox_px_to_norm((x0, y0, x1, y1), page_w, page_h),
                88.0,
            )
        )

    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(32, page_h // 14)))
    vlines = cv2.morphologyEx(inv, cv2.MORPH_OPEN, v_kernel)
    v_contours, _ = cv2.findContours(vlines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in v_contours:
        x, y, w, h = cv2.boundingRect(contour)
        if h / page_h < 0.06 or w / page_w > 0.025:
            continue
        if h / page_h > 0.92 and (x < page_w * 0.04 or x + w > page_w * 0.96):
            continue
        primitives.append(
            LayoutPrimitive(
                "vline",
                bbox_px_to_norm((x, y, x + w, y + h), page_w, page_h),
                84.0,
            )
        )

    contours, _ = cv2.findContours(inv, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < page_w * page_h * 0.0015:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if w <= 0 or h <= 0:
            continue
        width_norm = w / page_w
        height_norm = h / page_h
        if width_norm < 0.10 or height_norm < 0.012:
            continue
        # Enclosing page borders would skew the question-column median.
        if width_norm > 0.96 and height_norm > 0.90:
            continue
        rectangularity = area / max(1, w * h)
        if rectangularity < 0.18:
            continue
        primitives.append(
            LayoutPrimitive(
                "box",
                bbox_px_to_norm((x, y, x + w, y + h), page_w, page_h),
                min(96.0, 58.0 + rectangularity * 55.0),
            )
        )

    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(inv, 8)
    for label in range(1, min(num_labels, 1600)):
        x, y, w, h, area = stats[label]
        if area < 30:
            continue
        if w / page_w > 0.90 or h / page_h > 0.90:
            continue
        if w / page_w < 0.006 and h / page_h < 0.006:
            continue
        primitives.append(
            LayoutPrimitive(
                "component",
                bbox_px_to_norm(
                    (int(x), int(y), int(x + w), int(y + h)), page_w, page_h
                ),
                50.0,
            )
        )
    return primitives


def _find_question_anchors(
    page_number: int,
    lines: list[PageTextLine],
    page_w: int,
    page_h: int,
) -> list[_QuestionAnchor]:
    anchors: list[_QuestionAnchor] = []
    for line in lines:
        if not line.words:
            continue
        line_x_norm = line.bbox_px[0] / page_w
        line_y_norm = line.bbox_px[1] / page_h
        if line_x_norm > 0.45 or line_y_norm < 0.045 or line_y_norm > 0.975:
            continue

        words = list(line.words)
        first_text = words[0].text.strip()
        number: int | None = None
        marker_bbox = words[0].bbox_px

        m = _QUESTION_TOKEN_RE.fullmatch(first_text)
        if m:
            has_delimiter = first_text.endswith((".", ")"))
            has_points = bool(re.match(r"^\d+\s+\(?\d+\s*(?:points?|pts?)\b", line.text, re.I))
            if not has_delimiter and not has_points:
                continue
            number = int(m.group(1))
        else:
            q_match = _QUESTION_Q_RE.fullmatch(first_text)
            if q_match:
                number = int(q_match.group(1))
            elif (
                _normalize_token(first_text) in {"question", "problem"}
                and len(words) >= 2
            ):
                m2 = _QUESTION_TOKEN_RE.fullmatch(words[1].text.strip())
                if m2:
                    number = int(m2.group(1))
                    marker_bbox = _union_px([words[0].bbox_px, words[1].bbox_px])
            elif re.fullmatch(r"[A-Z][\.)]?", first_text):
                continue

        if number is None or number <= 0:
            continue

        line_lower = line.text.lower()
        if line_lower.strip() in {f"{number} points", f"{number} pts"}:
            # Point labels can start with digits without being question anchors.
            continue
        anchors.append(
            _QuestionAnchor(
                page_number=page_number,
                question_number=number,
                bbox_px=marker_bbox,
                line_bbox_px=line.bbox_px,
                confidence=line.confidence,
            )
        )

    best_by_key: dict[tuple[int, int], _QuestionAnchor] = {}
    for anchor in anchors:
        key = (anchor.page_number, anchor.question_number)
        previous = best_by_key.get(key)
        if previous is None or anchor.confidence > previous.confidence:
            best_by_key[key] = anchor
    return sorted(
        best_by_key.values(),
        key=lambda a: (a.page_number, a.bbox_px[1], a.bbox_px[0]),
    )


_SUBQUESTION_RE = re.compile(r"^([a-z])[.)]\s*\(?\d+(?:\.\d+)?\s*(?:points?|pts?)\b", re.I)


def _split_question_anchors(
    anchors: list[_QuestionAnchor],
    lines_by_page: dict[int, list[PageTextLine]],
) -> list[_QuestionAnchor]:
    ordered = sorted(anchors, key=lambda a: (a.page_number, a.line_bbox_px[1]))
    result: list[_QuestionAnchor] = []
    for index, parent in enumerate(ordered):
        start = (parent.page_number, parent.line_bbox_px[1])
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        end = (following.page_number, following.line_bbox_px[1]) if following else (float("inf"), 0)
        children: list[_QuestionAnchor] = []
        for page, lines in sorted(lines_by_page.items()):
            for line in lines:
                if not start < (page, line.bbox_px[1]) < end:
                    continue
                match = _SUBQUESTION_RE.match(line.text)
                if match is None:
                    continue
                suffix = match.group(1).lower()
                if suffix != chr(ord("a") + len(children)):
                    continue
                children.append(replace(
                    parent, page_number=page, suffix=suffix,
                    bbox_px=line.words[0].bbox_px, line_bbox_px=line.bbox_px,
                    confidence=line.confidence,
                ))
        if children and children[0].page_number == parent.page_number:
            children[0] = replace(children[0], bbox_px=parent.bbox_px, line_bbox_px=parent.line_bbox_px)
        result.extend(children if children else [parent])
    return result


def _field_label_bbox(
    lines: list[PageTextLine],
    *,
    kind: Literal["name", "sid"],
    page_w: int,
    max_y_px: int | None,
) -> tuple[tuple[int, int, int, int], float] | None:
    candidates: list[tuple[float, tuple[int, int, int, int]]] = []
    for line in lines:
        if max_y_px is not None and line.bbox_px[1] > max_y_px:
            continue
        max_label_x = 0.75 if kind == "name" else 0.92
        if line.bbox_px[0] / page_w > max_label_x:
            continue
        norms = [_normalize_token(word.text) for word in line.words]
        for idx, norm in enumerate(norms):
            matched = False
            end_idx = idx
            if kind == "name":
                matched = _looks_like_name_label(norm)
            else:
                matched = _looks_like_sid_label(norm)
                if (
                    norm.startswith("student")
                    and idx + 1 < len(norms)
                    and _looks_like_sid_label(norms[idx + 1])
                ):
                    matched = True
                    end_idx = idx + 1
            if not matched:
                continue
            label_words = list(line.words[idx : end_idx + 1])
            bbox = _union_px([word.bbox_px for word in label_words])
            y_bonus = max(0.0, 30.0 - (bbox[1] / 35.0))
            score = line.confidence + y_bonus - (bbox[0] / max(1, page_w)) * 10.0
            candidates.append((score, bbox))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1], candidates[0][0]


def _candidate_underlines(
    primitives: list[LayoutPrimitive],
    page_w: int,
    page_h: int,
) -> list[tuple[int, int, int, int]]:
    out: list[tuple[int, int, int, int]] = []
    for primitive in primitives:
        if primitive.kind not in {"hline", "box"}:
            continue
        x0, y0, x1, y1 = bbox_norm_to_px(primitive.bbox_norm, page_w, page_h)
        width_norm = (x1 - x0) / page_w
        height_norm = (y1 - y0) / page_h
        if primitive.kind == "hline" and width_norm >= 0.12 and height_norm <= 0.025:
            out.append((x0, y0, x1, y1))
        elif primitive.kind == "box" and width_norm >= 0.12 and height_norm <= 0.12:
            out.append((x0, y0, x1, y1))
    return sorted(out, key=lambda b: (b[1], b[0]))


def _region_for_label(
    label_bbox: tuple[int, int, int, int],
    primitives: list[LayoutPrimitive],
    *,
    page_w: int,
    page_h: int,
) -> tuple[list[float], float]:
    lx0, ly0, lx1, ly1 = label_bbox
    label_cy = (ly0 + ly1) / 2
    underlines = _candidate_underlines(primitives, page_w, page_h)

    best: tuple[float, tuple[int, int, int, int]] | None = None
    for ux0, uy0, ux1, uy1 in underlines:
        if ux1 <= lx1 + page_w * 0.025:
            continue
        y_distance = abs(((uy0 + uy1) / 2) - label_cy)
        if y_distance > page_h * 0.075:
            continue
        x_gap = max(0, ux0 - lx1)
        score = y_distance + x_gap * 0.18 - (ux1 - ux0) * 0.015
        if best is None or score < best[0]:
            best = (score, (ux0, uy0, ux1, uy1))

    if best is not None:
        _, value = best
        vx0, vy0, vx1, vy1 = value
        context = (
            min(lx0, vx0) - int(page_w * 0.018),
            min(ly0, vy0) - int(page_h * 0.025),
            max(lx1, vx1) + int(page_w * 0.025),
            max(ly1, vy1) + int(page_h * 0.025),
        )
        context = _expand_px(context, page_w=page_w, page_h=page_h, pad_x=0, pad_y=0)
        return (
            bbox_px_to_norm(context, page_w, page_h),
            92.0,
        )

    y_pad = max(20, int(page_h * 0.018))
    x0 = max(0, lx0 - int(page_w * 0.025))
    x1 = min(page_w, max(lx1 + int(page_w * 0.45), int(page_w * 0.62)))
    y0 = max(0, ly0 - y_pad)
    y1 = min(page_h, ly1 + y_pad)
    return (
        bbox_px_to_norm((x0, y0, x1, y1), page_w, page_h),
        68.0,
    )


def _region_for_orphan_value_mark(
    value_bbox: tuple[int, int, int, int],
    *,
    page_w: int,
    page_h: int,
) -> list[float]:
    vx0, vy0, vx1, vy1 = value_bbox
    context = (
        vx0 - int(page_w * 0.14),
        vy0 - int(page_h * 0.025),
        vx1 + int(page_w * 0.025),
        vy1 + int(page_h * 0.025),
    )
    context = _expand_px(context, page_w=page_w, page_h=page_h, pad_x=0, pad_y=0)
    return bbox_px_to_norm(context, page_w, page_h)


def _add_sid_from_unused_header_mark(
    fields: list[DetectedFieldRegion],
    primitives: list[LayoutPrimitive],
    *,
    page_w: int,
    page_h: int,
    first_question_y_px: int | None,
) -> None:
    if any(field.field_kind == "sid" for field in fields):
        return

    name_fields = [field for field in fields if field.field_kind == "name"]
    if not name_fields:
        return

    name_bbox = bbox_norm_to_px(name_fields[0].crop_bbox_norm, page_w, page_h)
    header_bottom = (
        first_question_y_px if first_question_y_px is not None else int(page_h * 0.25)
    )
    candidates: list[tuple[float, tuple[int, int, int, int]]] = []
    for mark in _candidate_underlines(primitives, page_w, page_h):
        x0, y0, x1, y1 = mark
        if y0 > header_bottom:
            continue
        if x1 <= name_bbox[0]:
            continue
        if y0 > name_bbox[3] + page_h * 0.12:
            continue
        if not (
            x0 >= name_bbox[2] - page_w * 0.02 or y0 > name_bbox[3] + page_h * 0.01
        ):
            continue
        width_norm = (x1 - x0) / page_w
        if width_norm < 0.10 or width_norm > 0.45:
            continue
        y_score = y0 / max(1, page_h) * 30.0
        right_bonus = 18.0 if x0 > page_w * 0.45 else 0.0
        below_name_bonus = 8.0 if y0 > name_bbox[3] else 0.0
        score = right_bonus + below_name_bonus + width_norm * 40.0 - y_score
        candidates.append((score, mark))

    if not candidates:
        return

    candidates.sort(key=lambda item: item[0], reverse=True)
    crop = _region_for_orphan_value_mark(candidates[0][1], page_w=page_w, page_h=page_h)
    fields.append(
        DetectedFieldRegion(
            field_id="student_id",
            field_kind="sid",
            page_number=1,
            crop_bbox_norm=crop,
            anchor_bbox_norm=None,
            confidence=72.0,
        )
    )


def _add_fields_from_header_marks(
    fields: list[DetectedFieldRegion],
    primitives: list[LayoutPrimitive],
    *,
    page_w: int,
    page_h: int,
    first_question_y_px: int | None,
) -> None:
    if any(field.field_kind == "name" for field in fields) and any(
        field.field_kind == "sid" for field in fields
    ):
        return

    header_bottom = min(
        int(page_h * _HEADER_MARK_MAX_Y_NORM),
        first_question_y_px if first_question_y_px is not None else page_h,
    )
    marks: list[tuple[int, int, int, int]] = []
    for mark in _candidate_underlines(primitives, page_w, page_h):
        x0, y0, x1, y1 = mark
        width_norm = (x1 - x0) / page_w
        height_norm = (y1 - y0) / page_h
        if y0 > header_bottom or y0 < page_h * 0.025:
            continue
        if not (
            _HEADER_MARK_MIN_WIDTH_NORM <= width_norm <= _HEADER_MARK_MAX_WIDTH_NORM
        ):
            continue
        if height_norm > 0.025:
            continue
        marks.append(mark)

    if not marks:
        return

    existing_boxes = [
        bbox_norm_to_px(field.crop_bbox_norm, page_w, page_h) for field in fields
    ]

    def overlaps_existing(mark: tuple[int, int, int, int]) -> bool:
        mx0, my0, mx1, my1 = mark
        mcx = (mx0 + mx1) / 2
        mcy = (my0 + my1) / 2
        for x0, y0, x1, y1 in existing_boxes:
            if x0 <= mcx <= x1 and y0 <= mcy <= y1:
                return True
        return False

    unused = [mark for mark in marks if not overlaps_existing(mark)]
    if not unused:
        return

    def add_field(
        field_kind: Literal["name", "sid"],
        field_id: Literal["student_name", "student_id"],
        mark: tuple[int, int, int, int],
    ) -> None:
        crop = _region_for_orphan_value_mark(mark, page_w=page_w, page_h=page_h)
        fields.append(
            DetectedFieldRegion(
                field_id=field_id,
                field_kind=field_kind,
                page_number=1,
                crop_bbox_norm=crop,
                anchor_bbox_norm=None,
                confidence=70.0,
            )
        )
        existing_boxes.append(bbox_norm_to_px(crop, page_w, page_h))

    if not any(field.field_kind == "name" for field in fields):
        left_marks = [
            mark for mark in unused if ((mark[0] + mark[2]) / 2) < page_w * 0.58
        ]
        if left_marks:
            left_marks.sort(key=lambda b: (b[1], b[0]))
            add_field("name", "student_name", left_marks[0])
            unused = [mark for mark in unused if not overlaps_existing(mark)]

    if not any(field.field_kind == "sid" for field in fields):
        right_marks = [
            mark for mark in unused if ((mark[0] + mark[2]) / 2) > page_w * 0.42
        ]
        if right_marks:
            right_marks.sort(key=lambda b: (b[1], -b[2]))
            add_field("sid", "student_id", right_marks[0])


def _detect_fields(
    first_page_lines: list[PageTextLine],
    first_page_primitives: list[LayoutPrimitive],
    page_w: int,
    page_h: int,
    first_question_y_px: int | None,
) -> list[DetectedFieldRegion]:
    out: list[DetectedFieldRegion] = []
    for kind, field_id in [("name", "student_name"), ("sid", "student_id")]:
        label = _field_label_bbox(
            first_page_lines,
            kind=kind,  # type: ignore[arg-type]
            page_w=page_w,
            max_y_px=first_question_y_px,
        )
        if label is None:
            continue
        label_bbox, label_score = label
        crop, region_score = _region_for_label(
            label_bbox,
            first_page_primitives,
            page_w=page_w,
            page_h=page_h,
        )
        out.append(
            DetectedFieldRegion(
                field_id=field_id,  # type: ignore[arg-type]
                field_kind=kind,  # type: ignore[arg-type]
                page_number=1,
                crop_bbox_norm=crop,
                anchor_bbox_norm=bbox_px_to_norm(label_bbox, page_w, page_h),
                confidence=round(
                    min(98.0, (label_score * 0.55) + (region_score * 0.45)),
                    1,
                ),
            )
        )
    _add_sid_from_unused_header_mark(
        out,
        first_page_primitives,
        page_w=page_w,
        page_h=page_h,
        first_question_y_px=first_question_y_px,
    )
    _add_fields_from_header_marks(
        out,
        first_page_primitives,
        page_w=page_w,
        page_h=page_h,
        first_question_y_px=first_question_y_px,
    )
    return out


def _detect_title_region(
    first_page_lines: list[PageTextLine],
    *,
    page_w: int,
    page_h: int,
    first_question_y_px: int | None,
    fields: list[DetectedFieldRegion],
    review_reasons: list[str] | None = None,
) -> DetectedTitleRegion | None:
    search_bottom = (
        first_question_y_px
        if first_question_y_px is not None
        else int(page_h * _TITLE_SEARCH_MAX_Y_NORM)
    )
    search_bottom = min(search_bottom, int(page_h * _TITLE_SEARCH_MAX_Y_NORM))
    field_boxes = [
        bbox_norm_to_px(field.crop_bbox_norm, page_w, page_h) for field in fields
    ]

    def fallback_region() -> DetectedTitleRegion:
        if review_reasons is not None:
            review_reasons.append("title_region_fallback_header")
        bottom = max(
            int(page_h * 0.08),
            min(search_bottom, int(page_h * 0.18)),
        )
        bbox = _expand_px(
            (int(page_w * 0.04), int(page_h * 0.02), int(page_w * 0.96), bottom),
            page_w=page_w,
            page_h=page_h,
            pad_x=0,
            pad_y=max(4, int(page_h * 0.004)),
        )
        return DetectedTitleRegion(
            page_number=1,
            crop_bbox_norm=bbox_px_to_norm(bbox, page_w, page_h),
            confidence=35.0,
        )

    def line_center_is_in_field(line: PageTextLine) -> bool:
        x0, y0, x1, y1 = line.bbox_px
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        for fx0, fy0, fx1, fy1 in field_boxes:
            if fx0 <= cx <= fx1 and fy0 <= cy <= fy1:
                return True
        return False

    def word_center_is_in_field(word: PageTextWord) -> bool:
        x0, y0, x1, y1 = word.bbox_px
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        for fx0, fy0, fx1, fy1 in field_boxes:
            if fx0 <= cx <= fx1 and fy0 <= cy <= fy1:
                return True
        return False

    def title_segment_from_line(line: PageTextLine) -> PageTextLine | None:
        words = list(line.words)
        if not words:
            return None

        end_idx = len(words)
        for idx, word in enumerate(words):
            norm = _normalize_token(word.text)
            if _looks_like_name_label(norm) or _looks_like_sid_label(norm):
                end_idx = idx
                break

        segment_words = [
            word for word in words[:end_idx] if not word_center_is_in_field(word)
        ]
        if not segment_words:
            return None

        bbox = _union_px([word.bbox_px for word in segment_words])
        confidence = sum(word.confidence for word in segment_words) / len(segment_words)
        text = re.sub(
            r"\s+",
            " ",
            " ".join(word.text for word in segment_words),
        ).strip()
        return PageTextLine(
            text=text,
            confidence=confidence,
            bbox_px=bbox,
            words=tuple(segment_words),
        )

    def distance_to_nearest_field(line: PageTextLine) -> float:
        if not field_boxes:
            return page_h * 0.20
        x0, y0, x1, y1 = line.bbox_px
        distances: list[float] = []
        for fx0, fy0, fx1, fy1 in field_boxes:
            dx = max(fx0 - x1, x0 - fx1, 0)
            dy = max(fy0 - y1, y0 - fy1, 0)
            distances.append((dx * dx + dy * dy) ** 0.5)
        return min(distances)

    def title_cue_score(text: str) -> float:
        score = 0.0
        if _TITLE_COURSE_RE.search(text):
            score += 34.0
        if _TITLE_ASSIGNMENT_RE.search(text):
            score += 30.0
        if _TITLE_COMPACT_VERSION_RE.search(text):
            score += 18.0
        return score

    candidate_lines: list[PageTextLine] = []
    for raw_line in first_page_lines:
        line = title_segment_from_line(raw_line)
        if line is None:
            continue
        x0, y0, x1, y1 = line.bbox_px
        if y0 > search_bottom:
            continue
        if y1 < page_h * 0.015:
            continue
        if line_center_is_in_field(line):
            continue
        norms = [_normalize_token(word.text) for word in line.words]
        if any(_looks_like_name_label(n) or _looks_like_sid_label(n) for n in norms):
            continue
        if re.match(r"^(?:q(?:uestion)?|problem)?\s*\d{1,3}[\.)]?\b", line.text, re.I):
            continue
        if len(re.findall(r"[A-Za-z0-9]", line.text)) < 3:
            continue
        candidate_lines.append(line)

    if not candidate_lines:
        return fallback_region()

    def score(line: PageTextLine) -> float:
        x0, y0, x1, y1 = line.bbox_px
        width_norm = (x1 - x0) / max(1, page_w)
        height_norm = (y1 - y0) / max(1, page_h)
        token_count = len(re.findall(r"[A-Za-z0-9]+", line.text))
        y_norm = y0 / max(1, page_h)
        search_progress = y0 / max(1, search_bottom)
        long_line_penalty = max(0.0, width_norm - 0.62) * 100.0
        verbose_penalty = max(0, token_count - 9) * 5.0
        short_line_penalty = max(0.0, 0.05 - width_norm) * 60.0
        instruction_penalty = (
            24.0
            if re.search(
                r"\b(?:instructions?|show your work|answer all)\b",
                line.text,
                re.I,
            )
            else 0.0
        )
        field_distance_bonus = (
            min(distance_to_nearest_field(line) / max(1, page_h), 0.08) * 95.0
        )
        above_field_bonus = 0.0
        if field_boxes and any(y1 <= fy0 for _fx0, fy0, _fx1, _fy1 in field_boxes):
            above_field_bonus = 12.0
        return (
            line.confidence * 0.45
            + title_cue_score(line.text)
            + min(width_norm, 0.55) * 18.0
            + height_norm * 140.0
            + max(0.0, 1.0 - search_progress) * 16.0
            + field_distance_bonus
            + above_field_bonus
            - long_line_penalty
            - verbose_penalty
            - short_line_penalty
            - instruction_penalty
            - y_norm * 10.0
        )

    best = max(candidate_lines, key=score)
    best_score = score(best)
    if title_cue_score(best.text) <= 0.0 and best_score < 45.0:
        return fallback_region()

    selected = [best]
    max_title_line_gap = max(14, int(page_h * 0.010))
    for line in candidate_lines:
        if line is best:
            continue
        vertical_gap = min(
            abs(line.bbox_px[1] - best.bbox_px[3]),
            abs(best.bbox_px[1] - line.bbox_px[3]),
        )
        center_delta = abs(
            ((line.bbox_px[0] + line.bbox_px[2]) / 2)
            - ((best.bbox_px[0] + best.bbox_px[2]) / 2)
        ) / max(1, page_w)
        left_delta = abs(line.bbox_px[0] - best.bbox_px[0]) / max(1, page_w)
        if (
            vertical_gap <= max_title_line_gap
            and (center_delta <= 0.18 or left_delta <= 0.08)
            and score(line) >= best_score - 20.0
        ):
            selected.append(line)

    bbox = _expand_px(
        _union_px([line.bbox_px for line in selected]),
        page_w=page_w,
        page_h=page_h,
        pad_x=max(14, int(page_w * 0.025)),
        pad_y=max(8, int(page_h * 0.006)),
    )
    return DetectedTitleRegion(
        page_number=1,
        crop_bbox_norm=bbox_px_to_norm(bbox, page_w, page_h),
        confidence=round(min(96.0, score(best)), 1),
    )


def _matched_question_box(
    anchor: _QuestionAnchor,
    primitives: list[LayoutPrimitive],
    page_w: int,
    page_h: int,
) -> list[float] | None:
    ax0, ay0, ax1, ay1 = anchor.line_bbox_px
    anchor_y = ay0 / page_h
    candidates: list[tuple[float, list[float]]] = []
    for primitive in primitives:
        if primitive.kind != "box":
            continue
        x, y, w, h = primitive.bbox_norm
        if w < 0.28 or h < 0.035 or y < 0.04:
            continue
        if not (x - 0.04 <= ax0 / page_w <= x + w + 0.04):
            continue
        rel_y = (anchor_y - y) / max(h, 1e-6)
        if rel_y < -0.14 or rel_y > 0.42:
            continue
        candidates.append((abs(anchor_y - y) + h * 0.3 - w * 0.06, primitive.bbox_norm))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item[0])[0][1]


def _next_rendered_page(page_number: int, page_numbers: list[int]) -> int | None:
    for candidate in page_numbers:
        if candidate > page_number:
            return candidate
    return None


def _next_page_top_boundary(
    page_number: int,
    anchors_by_page: dict[int, list[_QuestionAnchor]],
    dims: dict[int, tuple[int, int]],
) -> float:
    page_dims = dims.get(page_number)
    if page_dims is None:
        return _QUESTION_CONTINUATION_TOP_NORM

    _, page_h = page_dims
    anchors = anchors_by_page.get(page_number, [])
    if not anchors:
        return _QUESTION_CONTINUATION_PAGE_END_NORM

    first_anchor = min(anchors, key=lambda a: (a.line_bbox_px[1], a.line_bbox_px[0]))
    anchor_y = first_anchor.line_bbox_px[1] / max(1, page_h)
    return min(
        _QUESTION_CONTINUATION_PAGE_END_NORM,
        max(_QUESTION_CONTINUATION_TOP_NORM, anchor_y - 0.02),
    )


def _x_overlap_ratio(
    *,
    ax0: float,
    ax1: float,
    bx0: float,
    bx1: float,
) -> float:
    overlap = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    smaller_width = max(1e-6, min(ax1 - ax0, bx1 - bx0))
    return overlap / smaller_width


def _top_continuation_box(
    *,
    primitives: list[LayoutPrimitive],
    column_x0: float,
    column_x1: float,
    boundary_y: float,
    content_top: float = _QUESTION_CONTINUATION_TOP_NORM,
) -> list[float] | None:
    candidates: list[list[float]] = []
    for primitive in primitives:
        if primitive.kind != "box":
            continue
        x, y, w, h = primitive.bbox_norm
        if w < 0.28 or h < _QUESTION_CONTINUATION_MIN_HEIGHT_NORM:
            continue
        if y > _QUESTION_CONTINUATION_MAX_BOX_TOP_NORM:
            continue
        if y <= 0.055 and h >= 0.72:
            continue
        box_bottom = y + h
        if box_bottom > boundary_y:
            continue
        visible_top = max(y, content_top)
        visible_height = box_bottom - visible_top
        if visible_height < _QUESTION_CONTINUATION_MIN_HEIGHT_NORM:
            continue
        if (
            _x_overlap_ratio(
                ax0=x,
                ax1=x + w,
                bx0=column_x0,
                bx1=column_x1,
            )
            < _QUESTION_CONTINUATION_X_OVERLAP_MIN_RATIO
        ):
            continue
        candidates.append([x, visible_top, w, visible_height])

    if not candidates:
        return None

    candidates.sort(key=lambda box: (box[1], box[0]))
    cluster = [candidates[0]]
    cluster_bottom = candidates[0][1] + candidates[0][3]
    for box in candidates[1:]:
        x, y, w, h = box
        if y - cluster_bottom > 0.025:
            break
        if (
            _x_overlap_ratio(
                ax0=x,
                ax1=x + w,
                bx0=column_x0,
                bx1=column_x1,
            )
            < _QUESTION_CONTINUATION_X_OVERLAP_MIN_RATIO
        ):
            break
        cluster.append(box)
        cluster_bottom = max(cluster_bottom, y + h)

    x0 = min(box[0] for box in cluster)
    y0 = min(box[1] for box in cluster)
    x1 = max(box[0] + box[2] for box in cluster)
    y1 = max(box[1] + box[3] for box in cluster)
    return _clamp_bbox_norm([x0, y0, x1 - x0, y1 - y0])


def _continuation_segment_for_next_page(
    *,
    current_anchor: _QuestionAnchor,
    next_page_number: int,
    anchors_by_page: dict[int, list[_QuestionAnchor]],
    primitives_by_page: dict[int, list[LayoutPrimitive]],
    dims: dict[int, tuple[int, int]],
    column_x0: float,
    column_x1: float,
    review_reasons: list[str] | None,
    content_top: float = _QUESTION_CONTINUATION_TOP_NORM,
) -> tuple[int, list[float]] | None:
    page_dims = dims.get(next_page_number)
    if page_dims is None:
        return None

    boundary_y = _next_page_top_boundary(next_page_number, anchors_by_page, dims)
    box = _top_continuation_box(
        primitives=primitives_by_page.get(next_page_number, []),
        column_x0=column_x0,
        column_x1=column_x1,
        boundary_y=boundary_y,
        content_top=content_top,
    )
    if box is not None:
        return next_page_number, box

    height = boundary_y - content_top
    minimum_height = .015 if content_top > _QUESTION_CONTINUATION_TOP_NORM else _QUESTION_CONTINUATION_MIN_HEIGHT_NORM
    if height < minimum_height:
        return None

    if review_reasons is not None:
        review_reasons.append(
            f"q{current_anchor.question_number}: continuation_fallback_band"
        )
    return (
        next_page_number,
        _clamp_bbox_norm(
            [
                column_x0,
                content_top,
                max(0.05, column_x1 - column_x0),
                height,
            ]
        ),
    )


def _detect_questions(
    anchors: list[_QuestionAnchor],
    rendered_pages: dict[int, tuple[Image.Image, int, int]],
    primitives_by_page: dict[int, list[LayoutPrimitive]],
    review_reasons: list[str] | None = None,
    content_top_by_page: dict[int, float] | None = None,
) -> list[DetectedQuestionRegion]:
    if not anchors:
        return []
    dims = {
        page: (page_w, page_h) for page, (_, page_w, page_h) in rendered_pages.items()
    }
    x0_default, x1_default = 0.02, 0.98
    sorted_anchors = sorted(
        anchors, key=lambda a: (a.page_number, a.bbox_px[1], a.bbox_px[0])
    )
    page_numbers = sorted(rendered_pages)
    anchors_by_page: dict[int, list[_QuestionAnchor]] = {}
    for anchor in sorted_anchors:
        anchors_by_page.setdefault(anchor.page_number, []).append(anchor)

    questions: list[DetectedQuestionRegion] = []
    for idx, anchor in enumerate(sorted_anchors):
        page_image, page_w, page_h = rendered_pages[anchor.page_number]
        next_anchor = sorted_anchors[idx + 1] if idx + 1 < len(sorted_anchors) else None
        y0 = max(0.0, (anchor.line_bbox_px[1] / page_h) - 0.008)
        y1 = 0.965
        continuation: list[tuple[int, list[float]]] = []

        if next_anchor is not None and next_anchor.page_number == anchor.page_number:
            y1 = max(y0 + 0.03, (next_anchor.line_bbox_px[1] / page_h) - 0.02)
        elif y0 >= _QUESTION_CONTINUATION_TRIGGER_Y_NORM:
            y1 = 0.988
            next_page_number = _next_rendered_page(anchor.page_number, page_numbers)
            if next_page_number is not None:
                segment = _continuation_segment_for_next_page(
                    current_anchor=anchor,
                    content_top=(content_top_by_page or {}).get(next_page_number, _QUESTION_CONTINUATION_TOP_NORM),
                    next_page_number=next_page_number,
                    anchors_by_page=anchors_by_page,
                    primitives_by_page=primitives_by_page,
                    dims=dims,
                    column_x0=x0_default,
                    column_x1=x1_default,
                    review_reasons=review_reasons,
                )
                if segment is not None:
                    continuation.append(segment)

        x0 = x0_default
        x1 = x1_default
        matched = _matched_question_box(
            anchor,
            primitives_by_page.get(anchor.page_number, []),
            page_w,
            page_h,
        )
        if matched is not None:
            mx, my, mw, mh = matched
            x0 = min(x0, mx)
            x1 = max(x1, mx + mw)
            y0 = min(y0, max(0.0, my - 0.006))
            if next_anchor is None or next_anchor.page_number != anchor.page_number:
                y1 = min(y1, my + mh + 0.014)
            else:
                y1 = min(y1, max(y0 + 0.03, my + mh + 0.014))

        crop_bbox = _clamp_bbox_norm([x0, y0, max(0.05, x1 - x0), max(0.03, y1 - y0)])
        crop_px = bbox_norm_to_px(crop_bbox, page_w, page_h)
        crop = page_image.crop(crop_px)
        questions.append(
            DetectedQuestionRegion(
                question_id=anchor.question_id,
                question_number=anchor.question_number,
                page_number=anchor.page_number,
                crop_bbox_norm=crop_bbox,
                continuation_segments=continuation,
                confidence=round(
                    min(97.0, anchor.confidence + (8.0 if matched else 0.0)),
                    1,
                ),
                question_type=classify_question_image(crop),
            )
        )
    return questions


def scan_submission_layout(
    rendered_pages: dict[int, tuple[Image.Image, int, int]],
    config: "OcrConfig",
    split_subquestions: bool = True,
) -> SubmissionLayout:
    review_reasons: list[str] = []
    if not rendered_pages:
        return SubmissionLayout(
            title_region=None,
            fields=[],
            questions=[],
            review_reasons=["empty_submission"],
        )

    lines_by_page: dict[int, list[PageTextLine]] = {}
    primitives_by_page: dict[int, list[LayoutPrimitive]] = {}
    anchors: list[_QuestionAnchor] = []

    for page_number in sorted(rendered_pages):
        image, page_w, page_h = rendered_pages[page_number]
        lines = _group_lines(_page_words(image, config))
        primitives = _detect_layout_primitives(image)
        lines_by_page[page_number] = lines
        primitives_by_page[page_number] = primitives
        anchors.extend(_find_question_anchors(page_number, lines, page_w, page_h))

    first_page = min(rendered_pages)
    _, first_w, first_h = rendered_pages[first_page]
    first_question_y_px = min(
        (a.line_bbox_px[1] for a in anchors if a.page_number == first_page),
        default=None,
    )

    fields = _detect_fields(
        lines_by_page.get(first_page, []),
        primitives_by_page.get(first_page, []),
        first_w,
        first_h,
        first_question_y_px,
    )
    if not any(field.field_kind == "name" for field in fields):
        review_reasons.append("student_name_region_missing")
    if not any(field.field_kind == "sid" for field in fields):
        review_reasons.append("student_id_region_missing")

    title_region = _detect_title_region(
        lines_by_page.get(first_page, []),
        page_w=first_w,
        page_h=first_h,
        first_question_y_px=first_question_y_px,
        fields=fields,
        review_reasons=review_reasons,
    )
    if title_region is None:
        review_reasons.append("title_region_missing")

    anchors = sorted(
        anchors, key=lambda a: (a.page_number, a.question_number, a.line_bbox_px[1])
    )
    if not anchors:
        review_reasons.append("question_markers_missing")
    else:
        previous = 0
        for anchor in sorted(
            anchors,
            key=lambda a: (a.page_number, a.line_bbox_px[1], a.line_bbox_px[0]),
        ):
            if anchor.question_number <= previous:
                review_reasons.append("question_numbers_non_monotonic")
                break
            if previous and anchor.question_number != previous + 1:
                review_reasons.append("question_numbers_gap")
                break
            previous = anchor.question_number

    if split_subquestions:
        anchors = _split_question_anchors(anchors, lines_by_page)

    content_top_by_page: dict[int, float] = {}
    for page, lines in lines_by_page.items():
        _, _, height = rendered_pages[page]
        first_question = min((a.line_bbox_px[1] for a in anchors if a.page_number == page), default=height)
        header_bottom = max((line.bbox_px[3] for line in lines
                             if line.bbox_px[1] < min(first_question, height * .25)
                             and re.match(r"^(?:name|student\s*id|id)\b", line.text, re.I)), default=0)
        content_top_by_page[page] = max(_QUESTION_CONTINUATION_TOP_NORM, header_bottom / height + .02)

    questions = _detect_questions(
        anchors,
        rendered_pages,
        primitives_by_page,
        review_reasons=review_reasons,
        content_top_by_page=content_top_by_page,
    )
    for question in questions:
        _, _, w, h = question.crop_bbox_norm
        if w * h < 0.004 or w * h > 0.90:
            review_reasons.append(f"{question.question_id}: implausible_question_crop")

    return SubmissionLayout(
        title_region=title_region,
        fields=fields,
        questions=questions,
        review_reasons=review_reasons,
        primitives_by_page=primitives_by_page,
    )


def save_layout_debug(
    layout: SubmissionLayout,
    rendered_pages: dict[int, tuple[Image.Image, int, int]],
    output_dir: str | Path,
    question_crop_bboxes_by_page: dict[int, list[tuple[str, tuple[int, int, int, int]]]]
    | None = None,
) -> None:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(layout)
    (out_dir / "layout.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    for page_number, (image, page_w, page_h) in rendered_pages.items():
        overlay = image.convert("RGB")
        draw = ImageDraw.Draw(overlay)

        for primitive in layout.primitives_by_page.get(page_number, []):
            x0, y0, x1, y1 = bbox_norm_to_px(primitive.bbox_norm, page_w, page_h)
            color = {
                "hline": (20, 100, 220),
                "vline": (20, 140, 140),
                "box": (220, 120, 20),
                "component": (180, 180, 180),
            }[primitive.kind]
            draw.rectangle((x0, y0, x1, y1), outline=color, width=2)

        for field in layout.fields:
            if field.page_number != page_number:
                continue
            x0, y0, x1, y1 = bbox_norm_to_px(field.crop_bbox_norm, page_w, page_h)
            draw.rectangle((x0, y0, x1, y1), outline=(60, 180, 75), width=5)

        if layout.title_region and layout.title_region.page_number == page_number:
            x0, y0, x1, y1 = bbox_norm_to_px(
                layout.title_region.crop_bbox_norm, page_w, page_h
            )
            draw.rectangle((x0, y0, x1, y1), outline=(180, 60, 180), width=5)

        if question_crop_bboxes_by_page is None:
            for question in layout.questions:
                if question.page_number != page_number:
                    continue
                x0, y0, x1, y1 = bbox_norm_to_px(
                    question.crop_bbox_norm, page_w, page_h
                )
                draw.rectangle((x0, y0, x1, y1), outline=(220, 50, 50), width=5)
        else:
            for _question_id, (x0, y0, x1, y1) in question_crop_bboxes_by_page.get(
                page_number, []
            ):
                draw.rectangle((x0, y0, x1, y1), outline=(220, 50, 50), width=5)
        overlay.save(out_dir / f"page-{page_number:04d}.layout.png")
