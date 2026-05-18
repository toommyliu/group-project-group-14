from __future__ import annotations

from math import floor
from pathlib import Path
from typing import IO

from PIL import Image as PILImage
from PIL import ImageOps
from textual.app import RenderResult
from textual.geometry import Size
from textual.message import Message
from textual.reactive import reactive
from textual_image._geometry import ImageSize
from textual_image._pixeldata import ensure_image
from textual_image._terminal import get_cell_size
from textual_image._utils import StrOrBytesPath
from textual_image.renderable import Image as AutoRenderable
from textual_image.widget import Image as TextualImage


class SharpImage(TextualImage, Renderable=AutoRenderable):
    """Image widget tuned for document scans in a kitty-capable terminal.

    ``textual-image`` renders terminal graphics on a cell-aligned pixel grid.
    This wrapper keeps the display size at or below the source image's native
    pixel size by default and performs any necessary resize with a high-quality
    filter before the image is handed to the terminal renderable.
    """

    class ScalingError(Message):
        def __init__(self, at_limit: bool) -> None:
            self.at_limit = at_limit
            super().__init__()

    fit_width_fraction = reactive(0.6, layout=True)
    allow_upscale = reactive(False)

    MAX_RENDER_PIXELS = 8_000_000  # 8 MP safety limit for terminal rendering
    MAX_DIMENSION = 4096  # Max dimension for Kitty protocol/compatibility

    def __init__(
        self,
        image: StrOrBytesPath | IO[bytes] | PILImage.Image | None = None,
        *,
        fit_width_fraction: float = 1.0,
        allow_upscale: bool = False,
        max_cell_height: int | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        self._prepared_cache_key: tuple[object, int, int] | None = None
        self._prepared_cache_image: PILImage.Image | None = None
        self._at_scaling_limit = False
        super().__init__(
            image,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )
        self.fit_width_fraction = fit_width_fraction
        self.allow_upscale = allow_upscale
        self.max_cell_height = max_cell_height

    @TextualImage.image.setter
    def image(
        self, value: StrOrBytesPath | IO[bytes] | PILImage.Image | None
    ) -> None:
        self._prepared_cache_key = None
        self._prepared_cache_image = None
        TextualImage.image.fset(self, value)

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
        _, height = ImageSize(
            self._image_width,
            self._image_height,
            width,
            "auto",
        ).get_cell_size(width, max_height, terminal_sizes)
        return height

    def render(self) -> RenderResult:
        if not self._image:
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
        _, height = ImageSize(
            self._image_width,
            self._image_height,
            width,
            "auto",
        ).get_cell_size(width, max_height, terminal_sizes)
        return max(1, width), max(1, height)

    def _target_cell_width(self, container_width: int) -> int:
        if not self._image or self._image_width <= 0:
            return 0

        terminal_sizes = get_cell_size()
        intrinsic_width = max(1, round(self._image_width / terminal_sizes.width))

        # Use intrinsic_width as a fallback for container_width if it's 0,
        # to ensure a non-zero target width for initial layout.
        effective_container_width = max(container_width, intrinsic_width)

        max_width = max(1, floor(effective_container_width * self.fit_width_fraction))

        if self.max_cell_height is not None and self._image_height > 0:
            max_height_px = self.max_cell_height * terminal_sizes.height
            height_limited_px = max_height_px * (self._image_width / self._image_height)
            height_limited_width = max(
                1,
                floor(height_limited_px / terminal_sizes.width),
            )
            max_width = min(max_width, height_limited_width)
        if self.allow_upscale:
            return max_width
        return min(intrinsic_width, max_width)

    def _prepare_image(self, width: int, height: int) -> PILImage.Image:
        cache_key = (self._image_cache_token(), width, height)
        if (
            self._prepared_cache_key == cache_key
            and self._prepared_cache_image is not None
        ):
            return self._prepared_cache_image

        with ensure_image(self._image) as opened_image:  # type: ignore
            source = ImageOps.exif_transpose(opened_image).copy()

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
