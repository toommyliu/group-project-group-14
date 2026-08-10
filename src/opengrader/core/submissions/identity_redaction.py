"""Identity redaction for the whole-page scan view.

Grading is anonymous by default (``tui/state/workflow.py``): every screen shows
a student *id* and only teacher mode reveals the name. The whole-page view
(``page_context.py``, the grade screen's ``w`` key) broke that promise the
moment it was added — an answer *crop* is an answer box, but a whole *page* of
the scan is the sheet the student wrote their name and id across the top of. A
TA paging back for "continued on back" work sees the name.

The fix keeps the feature and removes the leak: while anonymity is on, the
identity header is painted out of the rendered page before the grader ever sees
it. Hiding ``w`` behind teacher mode was the other option and is worse —
whole-page context exists to stop a grader marking a *fragment* of an answer,
which is a correctness problem, and gating it would withdraw the fix from
exactly the people who do most of the grading.

Locating the header is OCR work, not geometry. Opengrader's own template puts
the Name/Student ID boxes *below* a variable-height title block
(``core/pdf/templates/draw.py``), a scan is skewed and offset on top of that,
and a hand-made exam puts them wherever it likes — so no fixed rectangle is
right. We read the top strip with Tesseract, find the *lines* that read like
identity fields, and black out a full-width band around each.

Three rules keep the result trustworthy:

* **Over-redact rather than under-redact.** A band spans the full page width
  and is padded generously above and below, because a handwritten name
  overshoots its printed box far more often than a question prompt strays into
  the header.
* **Never cover the answer being graded.** Bands are clipped above the
  question's highlighted crop region (:func:`clip_bands`). Hiding the answer
  would defeat the reason the page view exists, and that region is the one
  rectangle on the sheet we know for certain is not a name.
* **Fail closed, then fail honestly.** A top strip too poor to read redacts the
  *whole* searched strip. If Tesseract cannot run at all we cannot redact at
  all, so :class:`RedactionPlan` reports ``available=False`` and the caller
  refuses the page rather than guessing — that case, and only that case,
  degrades to "teacher mode only".

This is data minimization in the same spirit as ``core/anonymize.py``, applied
to pixels instead of markdown, and — like the policy lock — it is a **bias
control for a semi-trusted grading team, not a security boundary**: the scan
itself is on disk and a grader can open it in any PDF reader. It removes the
accidental glance, which is what anonymous grading is actually about.

The banding geometry is pure so it can be tested without a Tesseract binary;
only :func:`plan_identity_redaction` touches OCR.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from opengrader.core.pdf_utils import SCANNED_INK_LEVEL, ink_fraction

# Normalized (x, y, w, h), the same convention as ``QuestionSourceSegment`` and
# ``core/pdf_utils.bbox_norm_to_px``.
Band = tuple[float, float, float, float]

# Fraction of the page height searched for identity fields. Generous on
# purpose: on an Opengrader template the Name/Student ID boxes sit under the
# title and subtitle lines, which land them around 20-35% down a US-Letter
# sheet, and a long title pushes them further.
DEFAULT_TOP_FRACTION = 0.45

# Padding around a matched line, as a multiple of that line's own height. The
# printed label is small; the handwriting in the box next to it is not, and it
# routinely spills above and below the rule it was meant to sit on.
PAD_ABOVE = 0.7
PAD_BELOW = 1.2

# Below this many alphanumeric characters the top strip is treated as
# unreadable and the whole strip is hidden (same threshold and reasoning as
# ``boundary.page_has_identity_header``).
MIN_READABLE_ALNUM = 6

# A strip carrying more than this fraction of ink has something on it. Used only
# to tell "OCR read nothing because the page is *blank*" (a scratch sheet, the
# back of a duplex print — nothing to hide) from "OCR read nothing because the
# scan is *poor*" (fail closed, hide the whole strip).
#
# Measured at ``SCANNED_INK_LEVEL``, and that part is load-bearing: at the
# strict born-digital cut a real scanned header strip reads 0.000005-0.00095 and
# a *pencil-written* name reads 0.000000 — under this threshold, so the
# fail-closed branch silently failed **open** and showed the name. At the scanned
# cut the same strips read 0.0057-0.0103 and the pencil name 0.0068, against
# 0.000000 for blank paper and 0.00068 for a deliberately dirty blank sheet. The
# threshold itself was never wrong; only the grey level it was measured at.
INK_PIXEL_FRACTION = 0.001

# A header field is a short line ("Student ID") or one carrying a fill-in rule
# ("Name: ______"). The length ceiling is what keeps a prompt such as "Name
# three properties of a binary heap" out of the redaction set.
MAX_FIELD_LINE_WORDS = 6

# Lines that read like a student-identity field. A superset of
# ``boundary._IDENTITY_HEADER_RE``: that one only has to decide "is this a
# first page", this one has to catch every wording a name can hide behind.
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

# A colon or a fill-in rule ("____", "……") marks a line as a form field
# regardless of its length.
_FIELD_MARKER_RE = re.compile(r"[:_]|\.{3,}")

_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]")


@dataclass(frozen=True)
class TextLine:
    """One OCR'd line of the top strip: its words joined, extent in pixels."""

    text: str
    top: int
    bottom: int


@dataclass(frozen=True)
class RedactionPlan:
    """What to paint out of a page, and whether we could work it out at all.

    ``available=False`` means the identity header could *not* be located (no
    working Tesseract, an unmeasurable page). The caller must not show the page
    during anonymous grading — ``reason`` is written for a status line.
    """

    bands: tuple[Band, ...] = ()
    available: bool = True
    reason: str = ""


def line_is_identity_field(text: str) -> bool:
    """Whether an OCR'd line reads like a Name/Student-ID form field."""
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
    """Full-width bands covering every identity line, merged and ascending.

    Bands run the full page width because the value written next to a label is
    unbounded to the right — a long name, a second field on the same row — and
    nothing else lives on a header line worth preserving.
    """
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
    """Trim bands so they never cover a protected region.

    ``protect`` is the question's highlighted crop region(s). A redaction that
    hides the answer under review is worse than the leak it prevents, and the
    crop region is by construction an answer box, not a name field. Since bands
    are full-width, only the vertical overlap matters: a band is cut off at the
    top of the first protected region it runs into, and dropped outright if it
    *starts* inside one.
    """
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
    """Work out what to paint out of one rendered scan page.

    ``image`` is the rendered page (PIL); ``protect`` the normalized crop
    regions this question is being graded on. See the module docstring for the
    fail-closed / fail-honest split between an unreadable strip and OCR that
    cannot run.
    """
    width, height = getattr(image, "size", (0, 0))
    if width <= 0 or height <= 0:
        return RedactionPlan(
            available=False, reason="the page could not be measured"
        )

    strip_height = max(1, min(height, int(height * top_fraction)))
    strip = image.crop((0, 0, width, strip_height))
    try:
        lines, readable = _ocr_lines(strip)
    except Exception as exc:  # missing Tesseract binary, unreadable image, …
        return RedactionPlan(
            available=False,
            reason=(
                "the identity header could not be located "
                f"({exc.__class__.__name__})"
            ),
        )

    if not readable:
        # Nothing legible up here. A *blank* strip is the ordinary case — a
        # scratch sheet, the back of a duplex print — and has nothing to hide;
        # an inked strip we could not read is a poor scan, and that is the one
        # that has to fail closed over the whole searched area.
        if not _has_ink(strip):
            return RedactionPlan()
        whole_strip: tuple[Band, ...] = ((0.0, 0.0, 1.0, strip_height / height),)
        return RedactionPlan(
            bands=clip_bands(whole_strip, protect),
            reason="header too faint to read, so the whole header area is hidden",
        )

    return RedactionPlan(bands=clip_bands(bands_from_lines(lines, height), protect))


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _bands_from_spans(spans: Sequence[tuple[float, float]]) -> tuple[Band, ...]:
    """Clamp, merge overlapping vertical spans, and widen them to the page."""
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
    """Whether an image carries enough ink to be more than blank paper."""
    fraction = ink_fraction(image, level=SCANNED_INK_LEVEL)
    if fraction is None:
        # Can't tell — assume there is something to hide.
        return True
    return fraction >= INK_PIXEL_FRACTION


def _ocr_lines(image) -> tuple[tuple[TextLine, ...], bool]:
    """OCR one image into text lines. Returns ``(lines, readable)``.

    Word boxes are grouped back into their Tesseract line so the *phrase* can
    be matched — "Student" and "ID" are meaningless apart, and matching single
    words would redact any prompt containing "name".
    """
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
