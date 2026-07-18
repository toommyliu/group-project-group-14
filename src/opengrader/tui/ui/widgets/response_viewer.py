from pathlib import Path

from textual import events, on
from textual.message import Message
from textual.app import ComposeResult
from textual.containers import ScrollableContainer
from textual.widgets import Static

from opengrader.tui.ui.widgets.sharp_image import SharpImage


class ResponseViewer(ScrollableContainer):
    DEFAULT_CSS = """
    ResponseViewer {
        width: 1fr;
        height: 1fr;
        overflow: auto;
        scrollbar-size: 1 1;
        layout: vertical;
    }
    ResponseViewer > SharpImage {
        width: auto;
        height: auto;
    }
    """

    class ZoomChanged(Message):
        def __init__(self, fraction: float) -> None:
            self.fraction = fraction
            super().__init__()

    def __init__(self, image_path: str, *, id: str | None = None) -> None:
        self._image_path = image_path
        self._image_widget: SharpImage | None = None
        self._status_widget: Static | None = None
        self._at_limit = False
        self._dragging = False
        self._last_mouse_x = 0
        self._last_mouse_y = 0
        super().__init__(id=id)

    def compose(self) -> ComposeResult:
        yield from ()  # empty; image mounted in on_mount

    def on_mount(self) -> None:
        self.set_image_path(self._image_path)
        # Ensure scrollbars are calculated after initial mounting
        self.call_after_refresh(self.refresh, layout=True)

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
            self._image_widget = SharpImage(
                image_path,
                id="response-viewer-image",
                allow_upscale=True,
            )
            self._image_widget.styles.width = "auto"
            self._image_widget.styles.height = "auto"
            self.mount(self._image_widget)
            self.call_after_refresh(self._refresh_after_image_change)
            self.scroll_home(animate=False)
            return

        self._image_widget.image = image_path
        self._at_limit = False
        self.call_after_refresh(self._refresh_after_image_change)
        self.scroll_home(animate=False)

    def _refresh_after_image_change(self) -> None:
        """Relayout, then repaint the whole screen.

        Terminal graphics outlive Textual's cell diffing: sixel pixels stay
        painted (the sixel renderable's cleanup is a no-op) and stale TGP
        placeholders can survive a partial repaint. When the new image is
        smaller than the previous one, the freed cells would keep showing the
        previous image unless every cell is re-emitted.
        """
        self.refresh(layout=True)
        if self.is_attached:
            self.screen.refresh()

    def zoom_in(self) -> None:
        if self._image_widget and not self._at_limit:
            self._image_widget.fit_width_fraction = round(
                min(self._image_widget.fit_width_fraction + 0.1, 1.5), 1
            )
            self.post_message(self.ZoomChanged(self._image_widget.fit_width_fraction))
            self.call_after_refresh(self.refresh, layout=True)

    def zoom_out(self) -> None:
        if self._image_widget:
            self._at_limit = False
            self._image_widget.fit_width_fraction = round(
                max(self._image_widget.fit_width_fraction - 0.1, 0.1), 1
            )
            self.post_message(self.ZoomChanged(self._image_widget.fit_width_fraction))
            # Shrinking leaves graphics remnants just like an image swap.
            self.call_after_refresh(self._refresh_after_image_change)

    def reset_zoom(self) -> None:
        if self._image_widget:
            self._at_limit = False
            self._image_widget.fit_width_fraction = 0.7
            self.post_message(self.ZoomChanged(0.7))
            self.scroll_home(animate=False)
            self.call_after_refresh(self._refresh_after_image_change)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if event.ctrl or event.meta:
            self.zoom_in()
            event.stop()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if event.ctrl or event.meta:
            self.zoom_out()
            event.stop()

    @on(SharpImage.ScalingError)
    def _on_scaling_error(self, message: SharpImage.ScalingError) -> None:
        self._at_limit = message.at_limit

    def _remove_image_widget(self) -> None:
        if self._image_widget is not None and self._image_widget.is_attached:
            self._image_widget.remove()
        self._image_widget = None

    def _remove_status_widget(self) -> None:
        if self._status_widget is not None and self._status_widget.is_attached:
            self._status_widget.remove()
        self._status_widget = None
