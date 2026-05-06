from __future__ import annotations

from math import floor
from pathlib import Path
from typing import IO

from PIL import Image as PILImage
from PIL import ImageOps
from textual.app import RenderResult
from textual.geometry import Size
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
        self.fit_width_fraction = fit_width_fraction
        self.allow_upscale = allow_upscale
        self.max_cell_height = max_cell_height
        self._prepared_cache_key: tuple[object, int, int] | None = None
        self._prepared_cache_image: PILImage.Image | None = None
        super().__init__(
            image,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )

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

        width, height = self._target_cell_size()
        terminal_sizes = get_cell_size()
        pixel_width = width * terminal_sizes.width
        pixel_height = height * terminal_sizes.height
        prepared_image = self._prepare_image(pixel_width, pixel_height)

        self._renderable = self._Renderable(prepared_image, width, height)
        return self._renderable

    def _target_cell_size(self) -> tuple[int, int]:
        target_width = self._target_cell_width(
            self.parent.size.width if self.parent is not None else 0
        )
        width = (
            min(self.content_size.width, target_width)
            if self.content_size.width
            else target_width
        )
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
        max_width = max(1, floor(container_width * self.fit_width_fraction))
        if container_width <= 0:
            max_width = intrinsic_width
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

        with ensure_image(self._image) as opened_image:
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
