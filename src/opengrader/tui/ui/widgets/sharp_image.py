from __future__ import annotations

from math import floor
from pathlib import Path
from typing import IO

from PIL import Image as PILImage
from PIL import ImageOps
from textual.app import ComposeResult, RenderResult
from textual.geometry import Size
from textual.message import Message
from textual.reactive import reactive
from textual_image._geometry import ImageSize
from textual_image._pixeldata import ensure_image
from textual_image._terminal import get_cell_size
from textual_image._utils import StrOrBytesPath
from textual_image.renderable import tgp as _tgp
from textual_image.renderable.halfcell import Image as HalfcellRenderable
from textual_image.renderable.tgp import Image as TGPImage
from textual_image.renderable.unicode import Image as UnicodeRenderable

# The widget base class without ``textual-image``'s own protocol auto-choice:
# Opengrader picks the protocol itself (see `tui/terminal_graphics.py`), so we
# take the plain base and supply the renderable ourselves.
from textual_image.widget._base import Image as BaseImageWidget

from opengrader.tui.terminal_graphics import GraphicsCapability, active_graphics
from opengrader.tui.ui.widgets.inline_graphics import InlineGraphicsPane


class _TGPImageWithValidDelete(TGPImage):
    """TGP renderable whose delete command actually removes the image.

    ``tgp.Image.cleanup`` sends ``a=d,I=<id>``, but the image was registered
    under an image *id* (``i=``), not an image *number* (``I=``), so terminals
    following the Kitty graphics spec ignore the delete. The previous image
    then stays alive in the terminal, and any of its unicode placeholder
    cells that survive a partial repaint keep showing slices of it. Send the
    spec-correct ``a=d,d=I,i=<id>`` (drop placements and free the data), with
    ``q=2`` so the terminal does not write a response into Textual's stdin.
    """

    def cleanup(self) -> None:
        if self.terminal_image_id:
            _tgp._send_tgp_message(a="d", d="I", i=self.terminal_image_id, q=2)
            self.terminal_image_id = None


#: Protocols rendered through a Rich renderable. Sixel and iTerm2 are absent
#: on purpose: they are raw escape blobs and go through `InlineGraphicsPane`.
_RENDERABLE_BY_PROTOCOL = {
    "kitty": _TGPImageWithValidDelete,
    "halfcell": HalfcellRenderable,
    "unicode": UnicodeRenderable,
}

_INLINE_PROTOCOLS = frozenset({"sixel", "iterm2"})

#: Clockwise display rotation → the PIL transpose that achieves it. Transposes
#: are used rather than ``Image.rotate`` because a quarter turn is then an exact
#: pixel remapping: no resampling, no interpolation blur on the thin pen strokes
#: this widget exists to keep legible. PIL's ``ROTATE_n`` names count
#: *counter*-clockwise, hence the apparent inversion.
_ROTATION_TRANSPOSE = {
    90: PILImage.Transpose.ROTATE_270,
    180: PILImage.Transpose.ROTATE_180,
    270: PILImage.Transpose.ROTATE_90,
}


class SharpImage(BaseImageWidget, Renderable=UnicodeRenderable):
    """Image widget tuned for document scans, on whatever the terminal supports.

    Sizing is protocol-independent: the display size stays at or below the
    source image's native pixel size by default, and any resize happens with a
    high-quality filter before the pixels reach the terminal.

    Rendering is protocol-dependent, and the split is invisible to callers:

    * Kitty / half-block / unicode go through a Rich renderable produced by
      :meth:`render`, chosen per instance from the detected capability.
    * Sixel and iTerm2 are raw escape blobs Rich cannot carry, so the widget
      composes an :class:`InlineGraphicsPane` child that writes them straight
      into Textual's output. :meth:`render` then yields nothing.

    Either way the same :meth:`_prepare_image` pipeline does the scaling, so a
    crop looks the same (modulo protocol fidelity) in every terminal.
    """

    class ScalingError(Message):
        def __init__(self, at_limit: bool) -> None:
            self.at_limit = at_limit
            super().__init__()

    fit_width_fraction = reactive(0.6, layout=True)
    allow_upscale = reactive(False)
    #: Clockwise display rotation in degrees (0/90/180/270). Purely a view
    #: transform — the file on disk is never touched.
    rotation = reactive(0, layout=True)

    MAX_RENDER_PIXELS = 8_000_000  # 8 MP safety limit for terminal rendering
    MAX_DIMENSION = 4096  # Max dimension for Kitty protocol/compatibility

    def __init__(
        self,
        image: StrOrBytesPath | IO[bytes] | PILImage.Image | None = None,
        *,
        fit_width_fraction: float = 1.0,
        allow_upscale: bool = False,
        max_cell_height: int | None = None,
        capability: GraphicsCapability | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        self._prepared_cache_key: tuple[object, int, int] | None = None
        self._prepared_cache_image: PILImage.Image | None = None
        self._at_scaling_limit = False
        # Snapshot the protocol per widget: detection is process-wide and
        # settled before the app starts, and an instance-level copy keeps the
        # widget testable against any protocol.
        self._capability = capability or active_graphics()
        self._inline_pane: InlineGraphicsPane | None = None
        super().__init__(
            image,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )
        self._Renderable = _RENDERABLE_BY_PROTOCOL.get(
            self._capability.protocol, UnicodeRenderable
        )
        self.fit_width_fraction = fit_width_fraction
        self.allow_upscale = allow_upscale
        self.max_cell_height = max_cell_height

    @property
    def capability(self) -> GraphicsCapability:
        """The graphics protocol this widget renders with."""
        return self._capability

    @property
    def uses_inline_graphics(self) -> bool:
        return self._capability.protocol in _INLINE_PROTOCOLS

    @property
    def _display_size(self) -> tuple[int, int]:
        """The image's size *as displayed*, i.e. after any rotation.

        A quarter turn swaps width and height, and every sizing decision below
        works from the displayed shape — without this a rotated portrait scan
        would be laid out into a portrait cell box and squashed.
        """
        if self.rotation in (90, 270):
            return self._image_height, self._image_width
        return self._image_width, self._image_height

    def watch_rotation(self, old_rotation: int, new_rotation: int) -> None:
        """Drop the prepared/encoded pixels so the new orientation is drawn."""
        if old_rotation == new_rotation:
            return
        self._prepared_cache_key = None
        self._prepared_cache_image = None
        # Same reasoning as the ``image`` setter: the inline pane keeps its own
        # encoded-escape cache and reads pixels back through our pipeline.
        pane = getattr(self, "_inline_pane", None)
        if pane is not None and pane.is_attached:
            pane.invalidate()

    def compose(self) -> ComposeResult:
        if self.uses_inline_graphics:
            self._inline_pane = InlineGraphicsPane(
                self._capability.protocol,  # type: ignore[arg-type]
                self._prepare_image_or_none,
                self._image_cache_token,
            )
            yield self._inline_pane

    @BaseImageWidget.image.setter
    def image(
        self, value: StrOrBytesPath | IO[bytes] | PILImage.Image | None
    ) -> None:
        self._prepared_cache_key = None
        self._prepared_cache_image = None
        BaseImageWidget.image.fset(self, value)
        # The inline pane reads the image lazily through our pipeline, so it
        # only needs its encoded-data cache dropped.
        pane = getattr(self, "_inline_pane", None)
        if pane is not None and pane.is_attached:
            pane.invalidate()

    def on_unmount(self) -> None:
        # The library widget never frees the terminal-side image when the
        # widget is removed, leaking it in the terminal's image storage.
        if self._renderable is not None:
            self._renderable.cleanup()
            self._renderable = None

    def get_content_width(self, container: Size, viewport: Size) -> int:
        _ = viewport
        return self._target_cell_width(container.width)

    def get_content_height(
        self, container: Size, viewport: Size, width: int
    ) -> int:
        _ = viewport
        if not self._image:
            return 0

        max_height = self.max_cell_height or container.height or 2**32
        terminal_sizes = get_cell_size()
        display_width, display_height = self._display_size
        _, height = ImageSize(
            display_width,
            display_height,
            width,
            "auto",
        ).get_cell_size(width, max_height, terminal_sizes)
        return height

    def render(self) -> RenderResult:
        if not self._image:
            return ""

        # Sixel/iTerm2 paint through the composed child; drawing anything here
        # would overwrite the pixels the pane just wrote.
        if self.uses_inline_graphics:
            return ""

        if self._renderable:
            self._renderable.cleanup()
            self._renderable = None

        try:
            width, height = self._target_cell_size()
            terminal_sizes = get_cell_size()
            pixel_width = width * terminal_sizes.width
            pixel_height = height * terminal_sizes.height

            # Proactively clamp resolution to safety limits to avoid memory/terminal errors.
            # If the requested zoom exceeds this, we downscale the pixel data while keeping
            # the cell layout size the same. This provides a "sharp as possible" view.
            total_pixels = pixel_width * pixel_height
            if total_pixels > self.MAX_RENDER_PIXELS:
                scale = (self.MAX_RENDER_PIXELS / total_pixels) ** 0.5
                pixel_width = int(pixel_width * scale)
                pixel_height = int(pixel_height * scale)
                at_limit = True
            else:
                at_limit = False

            if at_limit != self._at_scaling_limit:
                self._at_scaling_limit = at_limit
                self.post_message(self.ScalingError(at_limit))

            prepared_image = self._prepare_image(pixel_width, pixel_height)
            self._renderable = self._Renderable(prepared_image, width, height)
        except (ValueError, RuntimeError) as exc:
            # Catch unexpected downstream errors (e.g. from the terminal protocol)
            if any(k in str(exc).lower() for k in ("large", "too big", "size", "pixels")):
                if not self._at_scaling_limit:
                    self._at_scaling_limit = True
                    self.post_message(self.ScalingError(True))
                return "Image too large to render"
            raise

        return self._renderable

    def _target_cell_size(self) -> tuple[int, int]:
        target_width = self._target_cell_width(
            self.parent.size.width if self.parent is not None else 0
        )
        width = target_width
        if not self._image:
            return 0, 0

        terminal_sizes = get_cell_size()
        max_height = self.max_cell_height or 2**32
        display_width, display_height = self._display_size
        _, height = ImageSize(
            display_width,
            display_height,
            width,
            "auto",
        ).get_cell_size(width, max_height, terminal_sizes)
        return max(1, width), max(1, height)

    def _target_cell_width(self, container_width: int) -> int:
        display_width, display_height = self._display_size
        if not self._image or display_width <= 0:
            return 0

        terminal_sizes = get_cell_size()
        intrinsic_width = max(1, round(display_width / terminal_sizes.width))

        # Use intrinsic_width as a fallback for container_width if it's 0,
        # to ensure a non-zero target width for initial layout.
        effective_container_width = (
            container_width if container_width > 0 else intrinsic_width
        )

        max_width = max(1, floor(effective_container_width * self.fit_width_fraction))

        if self.max_cell_height is not None and display_height > 0:
            max_height_px = self.max_cell_height * terminal_sizes.height
            height_limited_px = max_height_px * (display_width / display_height)
            height_limited_width = max(
                1,
                floor(height_limited_px / terminal_sizes.width),
            )
            max_width = min(max_width, height_limited_width)
        if self.allow_upscale:
            return max_width
        return min(intrinsic_width, max_width)

    def _prepare_image_or_none(
        self, width: int, height: int
    ) -> PILImage.Image | None:
        """`_prepare_image` for the inline pane, tolerant of a missing image."""
        if not self._image:
            return None
        return self._prepare_image(width, height)

    def _prepare_image(self, width: int, height: int) -> PILImage.Image:
        cache_key = (self._image_cache_token(), width, height)
        if (
            self._prepared_cache_key == cache_key
            and self._prepared_cache_image is not None
        ):
            return self._prepared_cache_image

        with ensure_image(self._image) as opened_image:  # type: ignore
            source = ImageOps.exif_transpose(opened_image).copy()

        # Rotate before resizing: the target size is already expressed in the
        # displayed orientation, and turning first means the LANCZOS resize
        # below sees the shape it is actually scaling to.
        transpose = _ROTATION_TRANSPOSE.get(self.rotation % 360)
        if transpose is not None:
            source = source.transpose(transpose)

        if source.size == (width, height):
            prepared = source
        else:
            reducing_gap = 3.0 if width < source.width or height < source.height else None
            prepared = source.resize(
                (width, height),
                resample=PILImage.Resampling.LANCZOS,
                reducing_gap=reducing_gap,
            )

        self._prepared_cache_key = cache_key
        self._prepared_cache_image = prepared
        return prepared

    def _image_cache_token(self) -> object:
        # Rotation is part of the identity of the *pixels*, not just of the
        # layout, so it belongs in the token: it keys both this widget's
        # prepared-image cache and the inline pane's encoded-escape cache, and
        # without it a turned crop would keep serving the previous orientation.
        return (self._source_cache_token(), self.rotation % 360)

    def _source_cache_token(self) -> object:
        if isinstance(self._image, bytes):
            return (self._image, None)
        if isinstance(self._image, (str, Path)):
            path = Path(self._image)
            try:
                stat = path.stat()
            except OSError:
                return (path, None)
            return (path, stat.st_mtime_ns, stat.st_size)
        return id(self._image)
