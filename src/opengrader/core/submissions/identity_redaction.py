from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from opengrader.core.pdf_utils import SCANNED_INK_LEVEL, ink_fraction

# Normalized (x, y, w, h).
Band = tuple[float, float, float, float]

DEFAULT_TOP_FRACTION = 0.45

PAD_ABOVE = 0.7
PAD_BELOW = 1.2

MIN_READABLE_ALNUM = 6

INK_PIXEL_FRACTION = 0.001

# Long labels can be prompts such as 'Name three properties'.
MAX_FIELD_LINE_WORDS = 6

IDENTITY_LINE_RE = re.compile(
    r"\b(?:"
    r"(?:full|first|last|printed|given|family|student)\s+name"
    r"|name"
    r"|student\s*(?:id|number|no\.?|#)"
    r"|id\s*(?:number|no\.?|#)?"
    r"|sid"
    r"|signature"
    r"|e-?mail"
    r")\b",
    re.IGNORECASE,
)

_FIELD_MARKER_RE = re.compile(r"[:_]|\.{3,}")

_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]")


@dataclass(frozen=True)
class TextLine:
    text: str
    top: int
    bottom: int


@dataclass(frozen=True)
class RedactionPlan:
    bands: tuple[Band, ...] = ()
    available: bool = True
    reason: str = ""


def line_is_identity_field(text: str) -> bool:
    stripped = text.strip()
    if not stripped or not IDENTITY_LINE_RE.search(stripped):
        return False
    if _FIELD_MARKER_RE.search(stripped):
        return True
    return len(stripped.split()) <= MAX_FIELD_LINE_WORDS


def bands_from_lines(
    lines: Iterable[TextLine],
    page_height: int,
    *,
    pad_above: float = PAD_ABOVE,
    pad_below: float = PAD_BELOW,
) -> tuple[Band, ...]:
    if page_height <= 0:
        return ()
    spans: list[tuple[float, float]] = []
    for line in lines:
        if not line_is_identity_field(line.text):
            continue
        height = max(1, line.bottom - line.top)
        spans.append(
            (
                (line.top - height * pad_above) / page_height,
                (line.bottom + height * pad_below) / page_height,
            )
        )
    return _bands_from_spans(spans)


def clip_bands(bands: Iterable[Band], protect: Iterable[Band]) -> tuple[Band, ...]:
    protected = [(y, y + h) for _x, y, _w, h in protect if h > 0]
    kept: list[Band] = []
    for x, y, width, height in bands:
        top, bottom = y, y + height
        begins_inside = False
        for protected_top, protected_bottom in protected:
            if protected_top <= top < protected_bottom:
                begins_inside = True
                break
            if top < protected_top < bottom:
                bottom = protected_top
        if begins_inside or bottom - top <= 0:
            continue
        kept.append((x, top, width, bottom - top))
    return tuple(kept)


def plan_identity_redaction(
    image,
    *,
    protect: Iterable[Band] = (),
    top_fraction: float = DEFAULT_TOP_FRACTION,
) -> RedactionPlan:
    width, height = getattr(image, "size", (0, 0))
    if width <= 0 or height <= 0:
        return RedactionPlan(
            available=False, reason="the page could not be measured"
        )

    strip_height = max(1, min(height, int(height * top_fraction)))
    strip = image.crop((0, 0, width, strip_height))
    try:
        lines, readable = _ocr_lines(strip)
    except Exception as exc:
        return RedactionPlan(
            available=False,
            reason=(
                "the identity header could not be located "
                f"({exc.__class__.__name__})"
            ),
        )

    if not readable:
        if not _has_ink(strip):
            return RedactionPlan()
        whole_strip: tuple[Band, ...] = ((0.0, 0.0, 1.0, strip_height / height),)
        return RedactionPlan(
            bands=clip_bands(whole_strip, protect),
            reason="header too faint to read, so the whole header area is hidden",
        )

    return RedactionPlan(bands=clip_bands(bands_from_lines(lines, height), protect))


def _bands_from_spans(spans: Sequence[tuple[float, float]]) -> tuple[Band, ...]:
    clamped = sorted(
        (max(0.0, top), min(1.0, bottom))
        for top, bottom in spans
        if min(1.0, bottom) > max(0.0, top)
    )
    merged: list[list[float]] = []
    for top, bottom in clamped:
        if merged and top <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], bottom)
        else:
            merged.append([top, bottom])
    return tuple((0.0, top, 1.0, bottom - top) for top, bottom in merged)


def _has_ink(image) -> bool:
    fraction = ink_fraction(image, level=SCANNED_INK_LEVEL)
    if fraction is None:
        return True
    return fraction >= INK_PIXEL_FRACTION


def _ocr_lines(image) -> tuple[tuple[TextLine, ...], bool]:
    import pytesseract
    from pytesseract import Output

    data = pytesseract.image_to_data(
        image, config="--psm 6", output_type=Output.DICT
    )
    grouped: dict[tuple[int, int, int], tuple[list[str], int, int]] = {}
    for index, raw_text in enumerate(data["text"]):
        text = (raw_text or "").strip()
        if not text:
            continue
        key = (
            int(data["block_num"][index]),
            int(data["par_num"][index]),
            int(data["line_num"][index]),
        )
        top = int(data["top"][index])
        bottom = top + int(data["height"][index])
        words, line_top, line_bottom = grouped.get(key, ([], top, bottom))
        words.append(text)
        grouped[key] = (words, min(line_top, top), max(line_bottom, bottom))

    lines = tuple(
        TextLine(text=" ".join(words), top=top, bottom=bottom)
        for words, top, bottom in grouped.values()
    )
    alnum = sum(len(_NON_ALNUM_RE.sub("", line.text)) for line in lines)
    return lines, alnum >= MIN_READABLE_ALNUM
