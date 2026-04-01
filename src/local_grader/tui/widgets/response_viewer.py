import uuid
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static
from textual_image.widget import Image


class ResponseViewer(VerticalScroll):
    def __init__(self, image_path: str, *, id: str | None = None) -> None:
        self._image_path = image_path
        super().__init__(id=id)

    def compose(self) -> ComposeResult:
        yield from ()  # empty; image mounted in on_mount

    def on_mount(self) -> None:
        self.set_image_path(self._image_path)

    def set_image_path(self, image_path: str) -> None:
        self._image_path = image_path
        for child in list(self.children):
            child.remove()

        path = Path(image_path)
        if not path.exists():
            self.mount(Static(f"Image not found: {image_path}"))
            return

        unique_id = f"resp-img-{uuid.uuid4().hex[:8]}"
        self.mount(Image(image_path, id=unique_id))
