from __future__ import annotations

import base64
import io
from collections.abc import Callable
from typing import Literal, NamedTuple

from PIL import Image as PILImage
from rich.control import Control
from rich.segment import ControlType, Segment
from rich.style import Style
from textual.dom import NoScreen
from textual.geometry import Region, Size
from textual.strip import Strip
from textual.widget import Widget
from textual_image._sixel import image_to_sixels
from textual_image._terminal import CellSize, get_cell_size

InlineProtocol = Literal["sixel", "iterm2"]

_NULL_CONTROL = ((ControlType.CURSOR_FORWARD, 0),)
_NULL_STYLE = Style()

_OSC = "\x1b]"
_BEL = "\x07"


def encode_iterm2(
    image: PILImage.Image, cell_width: int, cell_height: int
) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=1)
    payload = buffer.getvalue()
    encoded = base64.b64encode(payload).decode("ascii")
    arguments = ";".join(
        (
            "inline=1",
            f"size={len(payload)}",
            f"width={cell_width}",
            f"height={cell_height}",
            "preserveAspectRatio=0",
            "doNotMoveCursor=1",
        )
    )
    return f"{_OSC}1337;File={arguments}:{encoded}{_BEL}"


def encode_sixel(image: PILImage.Image) -> str:
    return image_to_sixels(image)


class _CacheKey(NamedTuple):
    token: object
    crop: Region
    content_size: Size
    cell_size: CellSize


class InlineGraphicsPane(Widget, can_focus=False, inherit_css=False):
    """prepare receives full pixel dimensions; cropping happens here. Cached escapes are
    keyed by token.
    """

    DEFAULT_CSS = """
    InlineGraphicsPane {
        width: 100%;
        height: 100%;
    }
    """

    def __init__(
        self,
        protocol: InlineProtocol,
        prepare: Callable[[int, int], PILImage.Image | None],
        token: Callable[[], object],
    ) -> None:
        super().__init__()
        self._protocol: InlineProtocol = protocol
        self._prepare = prepare
        self._token = token
        self._cache_key: _CacheKey | None = None
        self._cache_data: str = ""

    def invalidate(self) -> None:
        self._cache_key = None
        self._cache_data = ""
        self.refresh()

    def render_lines(self, crop: Region) -> list[Strip]:
        try:
            screen = self.screen
        except NoScreen:
            return []
        # Textual's modal text layers cannot cover terminal graphics.
        if not screen.is_active:
            return []
        if crop.width <= 0 or crop.height <= 0:
            return []

        cell_size = get_cell_size()
        content_size = self.content_size
        key = _CacheKey(self._token(), crop, content_size, cell_size)
        if self._cache_key == key and self._cache_data:
            data = self._cache_data
        else:
            data = self._encode(crop, content_size, cell_size)
            if not data:
                return []
            self._cache_key = key
            self._cache_data = data

        return [Strip([])] * (crop.height - 1) + [
            Strip(self._segments(data), cell_length=crop.width)
        ]

    def _encode(
        self, crop: Region, content_size: Size, cell_size: CellSize
    ) -> str:
        pixel_width = max(1, content_size.width * cell_size.width)
        pixel_height = max(1, content_size.height * cell_size.height)
        image = self._prepare(pixel_width, pixel_height)
        if image is None:
            return ""

        cropped = image.crop(
            (
                crop.x * cell_size.width,
                crop.y * cell_size.height,
                min(crop.right * cell_size.width, image.width),
                min(crop.bottom * cell_size.height, image.height),
            )
        )
        if cropped.width <= 0 or cropped.height <= 0:
            return ""

        if self._protocol == "iterm2":
            return encode_iterm2(cropped, crop.width, crop.height)
        return encode_sixel(cropped)

    def _segments(self, data: str) -> list[Segment]:
        visible = self.screen.find_widget(self).visible_region
        return [
            Segment(
                Control.move_to(visible.x, visible.y).segment.text,
                style=_NULL_STYLE,
            ),
            Segment(data, style=_NULL_STYLE, control=_NULL_CONTROL),
            Segment(
                Control.move_to(visible.right, visible.bottom).segment.text,
                style=_NULL_STYLE,
            ),
        ]
