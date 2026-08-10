"""Opengrader QR payload format + QR image generation.

A machine-readable code printed on a template page so the pipeline can recover a
sheet's **identity, version, page number, and page count** without OCR'ing
handwriting. One QR is stamped on every page; reading it (``core/ocr/qr_reader``)
gives three downstream stages a perfect-information fast path:

* **identity** (``id``)          — short-circuits roster name matching / duplicate
  resolution (``core/submissions/processor``)
* **boundary detection** (``page == 1``) — a new submission starts at every page 1
  (``core/submissions/boundary``)
* **page ordering** (``page``/``total``) — sort by ``page`` with no cascade
  (``core/submissions/reorder``)

The payload is deliberately tiny, human-inspectable, and versioned so the reader
can reject foreign QR codes and the format can evolve:

    OG1;id=100042;v=A;p=1;n=6;f=K7M2QX

``f`` is the **form key** of a scrambled sheet: the short, stable token the
question order was derived from (``core/bubble_sheet.form_key_for``). Printing
it here is what makes a scrambled exam readable months later — the sheet
carries the means to reconstruct its own question order, so there is no
sidecar map to lose or to fall out of sync with a re-generated PDF. It is
absent on an unscrambled sheet.

``id`` is omitted in *generic* mode (a stack of identical sheets — no student is
pre-assigned, so only version/page/count are encoded). This module is pure: it
depends only on ``segno`` (a zero-dependency, offline-first QR generator) and the
standard library. QR *reading* (OpenCV) lives in ``core/ocr/qr_reader`` so this
leaf stays importable without the heavy vision stack.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import segno

# Magic + format-version prefix. Bump the trailing digit if the field grammar
# changes incompatibly; the reader keys off this exact token.
PAYLOAD_PREFIX = "OG1"

# Field values are single tokens: strip the delimiters so a stray character in a
# student id / version label can never corrupt the payload grammar.
_UNSAFE_VALUE = re.compile(r"[;=\s]")


@dataclass(frozen=True)
class OpengraderCode:
    """Decoded contents of one Opengrader QR code.

    Every field is optional so the same type covers *generic* codes (no
    ``student_id``) and *personalized* codes (all fields present).
    """

    student_id: str | None = None
    version: str | None = None
    page: int | None = None
    total_pages: int | None = None
    # Scrambled-form key; ``None`` on a sheet whose questions are in answer-key
    # order. Deliberately separate from ``student_id``: a batch printed by copy
    # count has forms but no students, and stuffing a form key into ``id``
    # would make the extractor assign every sheet a bogus student.
    form: str | None = None


def _clean_value(value: str) -> str:
    return _UNSAFE_VALUE.sub("", value.strip())


def encode_payload(code: OpengraderCode) -> str:
    """Render an :class:`OpengraderCode` to its compact QR text payload."""
    parts = [PAYLOAD_PREFIX]
    if code.student_id:
        cleaned = _clean_value(code.student_id)
        if cleaned:
            parts.append(f"id={cleaned}")
    if code.version:
        cleaned = _clean_value(code.version)
        if cleaned:
            parts.append(f"v={cleaned}")
    if code.page is not None:
        parts.append(f"p={int(code.page)}")
    if code.total_pages is not None:
        parts.append(f"n={int(code.total_pages)}")
    if code.form:
        cleaned = _clean_value(code.form)
        if cleaned:
            parts.append(f"f={cleaned}")
    return ";".join(parts)


def decode_payload(text: str | None) -> OpengraderCode | None:
    """Parse a QR text payload, or return ``None`` if it is not ours.

    Tolerant of surrounding whitespace and unknown ``key=value`` fields (so a
    future writer can add fields without breaking an older reader). Returns
    ``None`` for empty text or any payload lacking the :data:`PAYLOAD_PREFIX`
    marker — that is how a foreign QR code is rejected.
    """
    if not text:
        return None
    tokens = [tok for tok in text.strip().split(";") if tok]
    if not tokens or tokens[0] != PAYLOAD_PREFIX:
        return None

    student_id: str | None = None
    version: str | None = None
    page: int | None = None
    total_pages: int | None = None
    form: str | None = None
    for token in tokens[1:]:
        key, sep, value = token.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if key == "id":
            student_id = value or None
        elif key == "v":
            version = value or None
        elif key == "p":
            page = _parse_int(value)
        elif key == "n":
            total_pages = _parse_int(value)
        elif key == "f":
            form = value or None
    return OpengraderCode(
        student_id=student_id,
        version=version,
        page=page,
        total_pages=total_pages,
        form=form,
    )


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def render_qr_png(code: OpengraderCode, *, scale: int = 6, border: int = 2) -> bytes:
    """Render a code to PNG bytes ready for ``page.insert_image(stream=...)``.

    ``error='m'`` (~15% recovery) keeps a folded/smudged scan readable while
    staying compact enough for a header-corner stamp. Returns raw PNG bytes so
    no temp file ever touches disk.
    """
    payload = encode_payload(code)
    qr = segno.make(payload, error="m")
    buffer = io.BytesIO()
    qr.save(buffer, kind="png", scale=scale, border=border)
    return buffer.getvalue()
