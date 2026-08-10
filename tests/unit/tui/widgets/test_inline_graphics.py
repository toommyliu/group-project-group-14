import base64
import re

from PIL import Image as PILImage

from opengrader.tui.ui.widgets.inline_graphics import encode_iterm2, encode_sixel

_HEADER = re.compile(r"^\x1b]1337;File=(?P<args>[^:]*):(?P<payload>.*)\x07$", re.S)


def _arguments(sequence: str) -> dict[str, str]:
    match = _HEADER.match(sequence)
    assert match, "not a well-formed iTerm2 inline-image sequence"
    return dict(
        part.split("=", 1) for part in match.group("args").split(";") if "=" in part
    )


def test_iterm2_sequence_is_well_formed() -> None:
    image = PILImage.new("RGB", (12, 8), color=(200, 30, 30))

    sequence = encode_iterm2(image, cell_width=6, cell_height=3)

    arguments = _arguments(sequence)
    assert arguments["inline"] == "1"
    # Size is declared in *cells* so the terminal scales the payload into
    # exactly the box Textual reserved, whatever resolution we sent.
    assert arguments["width"] == "6"
    assert arguments["height"] == "3"
    assert arguments["preserveAspectRatio"] == "0"
    assert arguments["doNotMoveCursor"] == "1"


def test_iterm2_payload_is_the_declared_number_of_png_bytes() -> None:
    image = PILImage.new("RGB", (10, 10), color=(0, 0, 255))

    sequence = encode_iterm2(image, cell_width=5, cell_height=5)

    match = _HEADER.match(sequence)
    assert match
    payload = base64.b64decode(match.group("payload"))
    # `size` must match, or iTerm2 truncates or waits for more data.
    assert len(payload) == int(_arguments(sequence)["size"])
    # PNG magic: lossless, so thin pen strokes survive.
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"


def test_iterm2_roundtrips_the_pixels() -> None:
    image = PILImage.new("RGB", (4, 4), color=(7, 200, 90))

    match = _HEADER.match(encode_iterm2(image, 2, 2))
    assert match
    import io

    decoded = PILImage.open(io.BytesIO(base64.b64decode(match.group("payload"))))
    assert decoded.size == (4, 4)
    assert decoded.convert("RGB").getpixel((0, 0)) == (7, 200, 90)


def test_sixel_output_uses_the_sixel_introducer() -> None:
    image = PILImage.new("RGB", (8, 8), color=(255, 255, 255))

    data = encode_sixel(image)

    assert data.startswith("\x1bP")  # DCS
    assert data.endswith("\x1b\\")  # ST
