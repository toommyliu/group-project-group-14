"""Opengrader QR payload encode/decode + generate→read round-trip."""

from __future__ import annotations

import numpy as np
from PIL import Image

from opengrader.core.ocr.qr_reader import read_code_from_image
from opengrader.core.qr_codes import (
    OpengraderCode,
    decode_payload,
    encode_payload,
    render_qr_png,
)


def test_encode_personalized_payload():
    code = OpengraderCode(student_id="100042", version="A", page=3, total_pages=6)
    assert encode_payload(code) == "OG1;id=100042;v=A;p=3;n=6"


def test_encode_generic_payload_omits_id():
    code = OpengraderCode(version="B", page=1, total_pages=2)
    assert encode_payload(code) == "OG1;v=B;p=1;n=2"


def test_encode_strips_unsafe_delimiters():
    code = OpengraderCode(student_id="1;2=3 4", version="A")
    # ';', '=', and whitespace are stripped so they can't corrupt the grammar.
    assert encode_payload(code) == "OG1;id=1234;v=A"


def test_decode_round_trip():
    code = OpengraderCode(student_id="100042", version="A", page=3, total_pages=6)
    assert decode_payload(encode_payload(code)) == code


def test_decode_rejects_foreign_qr():
    assert decode_payload("https://example.com") is None
    assert decode_payload("") is None
    assert decode_payload(None) is None


def test_decode_tolerates_unknown_fields():
    code = decode_payload("OG1;id=7;v=A;p=1;n=1;future=xyz")
    assert code == OpengraderCode(student_id="7", version="A", page=1, total_pages=1)


def test_decode_ignores_bad_integers():
    code = decode_payload("OG1;p=notanumber;n=3")
    assert code.page is None
    assert code.total_pages == 3


def _render_pil(code: OpengraderCode) -> Image.Image:
    import io

    png = render_qr_png(code)
    return Image.open(io.BytesIO(png)).convert("RGB")


def test_generate_to_read_round_trip():
    code = OpengraderCode(student_id="803352", version="B", page=2, total_pages=4)
    image = _render_pil(code)
    assert read_code_from_image(image) == code


def test_reader_ignores_blank_page():
    blank = Image.fromarray(np.full((200, 200, 3), 255, dtype=np.uint8))
    assert read_code_from_image(blank) is None
