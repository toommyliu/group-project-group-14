from __future__ import annotations

import re

import pytesseract
from PIL import Image

from opengrader.core.question_types import (
    FREE_RESPONSE,
    MULTIPLE_CHOICE,
    QuestionType,
)

CHOICE_LABEL_MIN_Y_NORM = 0.12
CHOICE_LABEL_MAX_X_NORM = 0.35

_CHOICE_LABEL_RE = re.compile(r"^([A-Za-z])[\).:]?$")
_CHOICE_PUNCTUATION = {")", ".", ":"}


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
    data = _safe_image_to_data(image, lang=lang, config=config)
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


def classify_question_image(
    image: Image.Image,
    *,
    lang: str = "eng",
    config: str = "--psm 6",
) -> QuestionType:
    """Classify a question crop conservatively from visible answer-choice labels."""
    candidates = _iter_choice_label_candidates(image, lang=lang, config=config)
    if not candidates:
        return FREE_RESPONSE

    first_position_by_label: dict[str, tuple[int, int]] = {}
    for label, top, left in candidates:
        first_position_by_label.setdefault(label, (top, left))

    expected = "A"
    if expected not in first_position_by_label:
        return FREE_RESPONSE

    contiguous_count = 0
    previous_position: tuple[int, int] | None = None
    for offset in range(26):
        label = chr(ord("A") + offset)
        position = first_position_by_label.get(label)
        if position is None:
            break
        if previous_position is not None and position < previous_position:
            break
        contiguous_count += 1
        previous_position = position

    if contiguous_count >= 2:
        return MULTIPLE_CHOICE
    return FREE_RESPONSE
