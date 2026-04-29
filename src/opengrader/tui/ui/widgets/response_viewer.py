from pathlib import Path

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from opengrader.tui.ui.widgets.sharp_image import SharpImage


class ResponseViewer(VerticalScroll):
    def __init__(self, image_path: str, *, id: str | None = None) -> None:
        self._image_path = image_path
        self._image_widget: SharpImage | None = None
        self._status_widget: Static | None = None
        super().__init__(id=id)

    def compose(self) -> ComposeResult:
        yield from ()  # empty; image mounted in on_mount

    def on_mount(self) -> None:
        self.set_image_path(self._image_path)

    def set_image_path(self, image_path: str) -> None:
        if (
            image_path == self._image_path
            and self._image_widget is not None
            and self._image_widget.is_attached
        ):
            return

        self._image_path = image_path
        path = Path(image_path)
        if not path.exists():
            self._remove_image_widget()
            message = f"Image not found: {image_path}"
            if self._status_widget is None or not self._status_widget.is_attached:
                self._status_widget = Static(message, id="response-viewer-status")
                self.mount(self._status_widget)
                return
            self._status_widget.update(message)
            return

        self._remove_status_widget()
        if self._image_widget is None or not self._image_widget.is_attached:
            self._image_widget = SharpImage(image_path, id="response-viewer-image")
            self.mount(self._image_widget)
            return

        self._image_widget.image = image_path

    def _remove_image_widget(self) -> None:
        if self._image_widget is not None and self._image_widget.is_attached:
            self._image_widget.remove()
        self._image_widget = None

    def _remove_status_widget(self) -> None:
        if self._status_widget is not None and self._status_widget.is_attached:
            self._status_widget.remove()
        self._status_widget = None
