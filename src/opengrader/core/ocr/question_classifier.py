from __future__ import annotations

import re
from PIL import Image

from opengrader.core.question_types import (
    FREE_RESPONSE,
    MULTIPLE_CHOICE,
    QuestionType,
)
from opengrader.core.pdf_utils import safe_image_to_data

CHOICE_LABEL_MIN_Y_NORM = 0.12
CHOICE_LABEL_MAX_X_NORM = 0.35

CHOICE_LABEL_COLUMN_TOLERANCE_NORM = 0.08
CHOICE_LABEL_MIN_ROW_GAP_NORM = 0.015

_CHOICE_LABEL_RE = re.compile(r"^([A-Za-z])[\).:]?$")
_CHOICE_PUNCTUATION = {")", ".", ":"}

def _choice_label_from_token(token: str) -> str | None:
    normalized = token.strip()
    if not normalized:
        return None

    match = _CHOICE_LABEL_RE.fullmatch(normalized)
    if match is None:
        compact = re.sub(r"[^A-Za-z).:]", "", normalized)
        match = _CHOICE_LABEL_RE.fullmatch(compact)
    if match is None:
        return None

    return match.group(1).upper()


def _same_ocr_line(data: dict, i: int, j: int) -> bool:
    for key in ("block_num", "par_num", "line_num"):
        values = data.get(key, [])
        if i >= len(values) or j >= len(values) or values[i] != values[j]:
            return False
    return True


def _token_confidence(data: dict, i: int) -> float:
    try:
        return float(data.get("conf", [])[i])
    except (TypeError, ValueError, IndexError):
        return -1.0


def _is_choice_label_candidate(
    *,
    data: dict,
    index: int,
    image_width: int,
    image_height: int,
) -> bool:
    if image_width <= 0 or image_height <= 0:
        return False

    try:
        left = int(data.get("left", [])[index])
        top = int(data.get("top", [])[index])
    except (TypeError, ValueError, IndexError):
        return False

    x_norm = left / image_width
    y_norm = top / image_height
    return x_norm <= CHOICE_LABEL_MAX_X_NORM and y_norm >= CHOICE_LABEL_MIN_Y_NORM


def _iter_choice_label_candidates(
    image: Image.Image,
    *,
    lang: str,
    config: str,
) -> list[tuple[str, int, int]]:
    data = safe_image_to_data(image, lang=lang, config=config)
    texts = data.get("text", [])
    count = len(texts)
    candidates: list[tuple[str, int, int]] = []

    for i in range(count):
        text = str(texts[i]).strip()
        if not text or _token_confidence(data, i) <= 0:
            continue
        if not _is_choice_label_candidate(
            data=data,
            index=i,
            image_width=image.width,
            image_height=image.height,
        ):
            continue

        label = _choice_label_from_token(text)
        if label is None and re.fullmatch(r"[A-Za-z]", text):
            next_index = i + 1
            if next_index < count and _same_ocr_line(data, i, next_index):
                next_text = str(texts[next_index]).strip()
                if next_text in _CHOICE_PUNCTUATION:
                    label = text.upper()

        if label is None:
            continue

        try:
            top = int(data.get("top", [])[i])
            left = int(data.get("left", [])[i])
        except (TypeError, ValueError, IndexError):
            top = 0
            left = 0
        candidates.append((label, top, left))

    candidates.sort(key=lambda item: (item[1], item[2]))
    return candidates


def _label_index(label: str) -> int:
    return ord(label) - ord("A")


def _labels_are_vertically_ordered(labels: list[tuple[str, int, int]]) -> bool:
    previous_idx = -1
    previous_top = -1
    for label, top, _left in labels:
        idx = _label_index(label)
        if idx <= previous_idx or top <= previous_top:
            return False
        previous_idx = idx
        previous_top = top
    return True


def _has_plausible_row_spacing(
    labels: list[tuple[str, int, int]], *, image_height: int
) -> bool:
    if len(labels) < 3:
        return True

    gaps = [
        labels[i + 1][1] - labels[i][1]
        for i in range(len(labels) - 1)
        if labels[i + 1][1] > labels[i][1]
    ]
    if len(gaps) != len(labels) - 1:
        return False

    min_gap = max(8, int(round(image_height * CHOICE_LABEL_MIN_ROW_GAP_NORM)))
    if any(gap < min_gap for gap in gaps):
        return False

    sorted_gaps = sorted(gaps)
    median_gap = sorted_gaps[len(sorted_gaps) // 2]
    return all(median_gap * 0.45 <= gap <= median_gap * 1.90 for gap in gaps)


def _dedupe_choice_labels_by_position(
    labels: list[tuple[str, int, int]],
) -> list[tuple[str, int, int]]:
    first_by_label: dict[str, tuple[str, int, int]] = {}
    for label, top, left in sorted(labels, key=lambda item: (item[1], item[2])):
        first_by_label.setdefault(label, (label, top, left))
    return sorted(first_by_label.values(), key=lambda item: (item[1], item[2]))


def _choice_sequence_is_strong(
    labels: list[tuple[str, int, int]], *, image_height: int
) -> bool:
    labels = _dedupe_choice_labels_by_position(labels)
    if len(labels) < 2:
        return False
    if not _labels_are_vertically_ordered(labels):
        return False
    if not _has_plausible_row_spacing(labels, image_height=image_height):
        return False

    indices = [_label_index(label) for label, _top, _left in labels]
    first = indices[0]
    last = indices[-1]
    visible_count = len(indices)
    missing_count = (last - first + 1) - visible_count

    if first == 0:
        if missing_count == 0 and visible_count >= 2:
            return True
        return missing_count == 1 and visible_count >= 3

    if first == 1:
        return missing_count == 0 and visible_count >= 3

    return False


def _has_strong_choice_sequence(
    candidates: list[tuple[str, int, int]],
    *,
    image_width: int,
    image_height: int,
) -> bool:
    if len(candidates) < 2 or image_width <= 0 or image_height <= 0:
        return False

    column_tolerance = max(
        18, int(round(image_width * CHOICE_LABEL_COLUMN_TOLERANCE_NORM))
    )
    for _seed_label, _seed_top, seed_left in candidates:
        column = [
            candidate
            for candidate in candidates
            if abs(candidate[2] - seed_left) <= column_tolerance
        ]
        if _choice_sequence_is_strong(column, image_height=image_height):
            return True
    return False


_TRUE_FALSE_WORD_RE = re.compile(r"^(true|false)$")


def _normalize_word_token(token: str) -> str | None:
    cleaned = re.sub(r"[^A-Za-z]", "", token).lower()
    match = _TRUE_FALSE_WORD_RE.fullmatch(cleaned)
    return match.group(1) if match is not None else None


def _has_true_false_pair(
    image: Image.Image,
    *,
    lang: str,
    config: str,
) -> bool:
    if image.width <= 0 or image.height <= 0:
        return False

    data = safe_image_to_data(image, lang=lang, config=config)
    texts = data.get("text", [])
    positions: dict[str, tuple[int, int]] = {}
    for i in range(len(texts)):
        word = _normalize_word_token(str(texts[i]).strip())
        if word is None or word in positions or _token_confidence(data, i) <= 0:
            continue
        if not _is_choice_label_candidate(
            data=data,
            index=i,
            image_width=image.width,
            image_height=image.height,
        ):
            continue
        try:
            top = int(data.get("top", [])[i])
            left = int(data.get("left", [])[i])
        except (TypeError, ValueError, IndexError):
            continue
        positions[word] = (top, left)

    if "true" not in positions or "false" not in positions:
        return False

    true_top, true_left = positions["true"]
    false_top, false_left = positions["false"]
    column_tolerance = max(
        18, int(round(image.width * CHOICE_LABEL_COLUMN_TOLERANCE_NORM))
    )
    return false_top > true_top and abs(false_left - true_left) <= column_tolerance


def classify_question_image(
    image: Image.Image,
    *,
    lang: str = "eng",
    config: str = "--psm 6",
) -> QuestionType:
    candidates = _iter_choice_label_candidates(image, lang=lang, config=config)
    if candidates and _has_strong_choice_sequence(
        candidates, image_width=image.width, image_height=image.height
    ):
        return MULTIPLE_CHOICE

    if _has_true_false_pair(image, lang=lang, config=config):
        return MULTIPLE_CHOICE

    return FREE_RESPONSE
