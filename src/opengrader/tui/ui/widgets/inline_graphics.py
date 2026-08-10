"""Rendering for the two *inline* graphics protocols: Sixel and iTerm2.

The Kitty protocol fits Textual's rendering model: images are uploaded once,
referenced by id, and placed with ordinary printable unicode cells, so
``textual-image`` can hand Rich a renderable and be done. Sixel and iTerm2
cannot work that way -- both are raw byte blobs painted at the cursor, which
Rich would mangle if it saw them as text.

The proven workaround (which ``textual-image`` uses for Sixel) is to bypass
Rich: a child widget overrides ``render_lines`` and injects the escape data
directly into the strip Textual writes to the terminal, bracketed by explicit
cursor moves. This module generalises that trick over both protocols so
Opengrader gets one code path, one cache, and one place to fix bugs -- and so
the image pipeline in :class:`~opengrader.tui.ui.widgets.sharp_image.SharpImage`
(LANCZOS downscaling, zoom, cell-exact sizing) feeds all three protocols
instead of only Kitty.

Known limitation: inside a terminal multiplexer the escape sequences below are
filtered unless passthrough is configured, which is why
:mod:`opengrader.tui.terminal_graphics` degrades a tmux session to half-blocks
rather than emitting bytes that would be swallowed.
"""

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

#: A no-op control code. Rich rewrites/escapes plain text segments; marking a
#: segment as a control sequence makes it pass through byte-for-byte.
_NULL_CONTROL = ((ControlType.CURSOR_FORWARD, 0),)
_NULL_STYLE = Style()

_OSC = "\x1b]"
_BEL = "\x07"


def encode_iterm2(
    image: PILImage.Image, cell_width: int, cell_height: int
) -> str:
    """Encode ``image`` as an iTerm2 inline-image escape sequence.

    The display size is declared in *character cells* rather than pixels, so
    the terminal scales the payload into exactly the box Textual reserved --
    which keeps the layout correct even when the pixel data we send is a
    different resolution (a HiDPI crop, or one clamped for memory).

    ``doNotMoveCursor`` keeps well-behaved terminals from scrolling the screen
    after painting; the caller still emits explicit cursor moves for the
    terminals that ignore it.
    """
    buffer = io.BytesIO()
    # PNG: lossless (these are text scans -- JPEG ringing eats thin pen
    # strokes) and understood by every implementation of the protocol.
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
    """Encode ``image`` as Sixel data.

    Sixel carries no size metadata: the pixels *are* the size, so the caller
    must scale before encoding.
    """
    return image_to_sixels(image)


class _CacheKey(NamedTuple):
    token: object
    crop: Region
    content_size: Size
    cell_size: CellSize


class InlineGraphicsPane(Widget, can_focus=False, inherit_css=False):
    """Child widget that injects Sixel/iTerm2 data into Textual's output.

    It renders nothing of its own and assumes it fills its parent's content
    region exactly (no border, no padding of its own), so ``render_lines``'s
    crop region maps straight onto image pixels.

    Args:
        protocol: Which encoder to use.
        prepare: ``(pixel_width, pixel_height) -> PIL image | None`` -- the
            owner's scaling pipeline. Called with the full (uncropped) target
            size; the pane crops the result itself.
        token: Cheap identity of the current source image, used to invalidate
            the encoded-data cache when the image changes.
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
        """Drop the encoded-data cache and repaint."""
        self._cache_key = None
        self._cache_data = ""
        self.refresh()

    def render_lines(self, crop: Region) -> list[Strip]:
        try:
            screen = self.screen
        except NoScreen:
            return []
        # An inactive screen (a modal is on top) must not paint pixels: unlike
        # text, they are not covered by whatever Textual draws over us.
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
        """Scale, crop, and encode the visible slice of the image."""
        pixel_width = max(1, content_size.width * cell_size.width)
        pixel_height = max(1, content_size.height * cell_size.height)
        # Bounded by the terminal's own cell grid, so this can never blow up
        # the way an arbitrary zoom factor could.
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
        """Park the cursor, emit the blob, then park it again.

        The escape data is absolutely positioned, so it must be written with
        the cursor at the widget's on-screen origin -- and the cursor must be
        left somewhere sane afterwards, because terminals disagree about
        whether painting an image moves it.
        """
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
