from __future__ import annotations

from pathlib import Path

from PIL import Image as PILImage
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static
from textual_image.widget import Image

from opengrader.core.pdf.pdf_preview import get_pdf_page_count, pdf_page_to_image

_preview_cache: dict[tuple[str, int], PILImage.Image] = {}


class TemplatePreview(VerticalScroll):
    DEFAULT_CSS = """
    TemplatePreview {
        width: 1fr;
        height: 1fr;
        align: center top;
    }
    TemplatePreview Image {
        width: 90%;
        height: auto;
    }
    """

    def __init__(
        self,
        pdf_path: Path | None = None,
        *,
        label: str = "",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._pdf_path = pdf_path
        self._label = label
        self._current_page = 0
        self._total_pages = 0
        self._header: Static | None = None
        self._image: Image | None = None

    def compose(self) -> ComposeResult:
        self._header = Static("")
        yield self._header
        self._image = Image(None)
        yield self._image

    def on_mount(self) -> None:
        if self._pdf_path and self._pdf_path.exists():
            self._total_pages = get_pdf_page_count(self._pdf_path)
        self._render_template()

    def next_page(self) -> None:
        if self._current_page < self._total_pages - 1:
            self._current_page += 1
            self._render_template()

    def prev_page(self) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            self._render_template()

    def _render_template(self) -> None:
        if self._header is None or self._image is None:
            return

        if self._pdf_path is None or not self._pdf_path.exists():
            self._header.update("No template PDF found.")
            self._image.display = False
            return

        cache_key = (str(self._pdf_path), self._current_page)
        pil_image = _preview_cache.get(cache_key)

        if pil_image is None:
            try:
                pil_image = pdf_page_to_image(
                    self._pdf_path, page=self._current_page, dpi=96
                )
                _preview_cache[cache_key] = pil_image
            except Exception as exc:
                self._header.update(f"Failed to render template: {exc}")
                self._image.display = False
                return

        # TODO: move this to status_bar?
        if self._total_pages > 1:
            pagination_text = f"Page {self._current_page + 1} of {self._total_pages}"
            self._header.update(pagination_text)
        elif self._label:
            self._header.update(self._label)
        else:
            self._header.update("")

        self._image.display = True
        self._image.image = pil_image
        self.scroll_home()
