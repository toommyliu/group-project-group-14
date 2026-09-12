from __future__ import annotations

import io
import re
from dataclasses import dataclass

import segno

PAYLOAD_PREFIX = "OG1"

_UNSAFE_VALUE = re.compile(r"[;=\s]")


@dataclass(frozen=True)
class OpengraderCode:
    student_id: str | None = None
    version: str | None = None
    page: int | None = None
    total_pages: int | None = None
    form: str | None = None


def _clean_value(value: str) -> str:
    return _UNSAFE_VALUE.sub("", value.strip())


def encode_payload(code: OpengraderCode) -> str:
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
    payload = encode_payload(code)
    qr = segno.make(payload, error="m")
    buffer = io.BytesIO()
    qr.save(buffer, kind="png", scale=scale, border=border)
    return buffer.getvalue()
