import tempfile
from pathlib import Path

from PIL import Image as PILImage
from textual import events, on
from textual.message import Message
from textual.app import ComposeResult
from textual.containers import ScrollableContainer
from textual.widgets import Static

from opengrader.tui.external_viewer import open_in_external_viewer
from opengrader.tui.terminal_graphics import GraphicsCapability, active_graphics
from opengrader.tui.ui.widgets.sharp_image import _ROTATION_TRANSPOSE, SharpImage


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
    #response-viewer-fallback {
        width: 100%;
        height: auto;
        color: $warning;
        background: $surface;
        padding: 0 1;
        margin-bottom: 1;
    }
    """

    class ZoomChanged(Message):
        def __init__(self, fraction: float) -> None:
            self.fraction = fraction
            super().__init__()

    def __init__(
        self,
        image_path: str,
        *,
        id: str | None = None,
        capability: GraphicsCapability | None = None,
    ) -> None:
        self._image_path = image_path
        self._image_widget: SharpImage | None = None
        self._status_widget: Static | None = None
        self._at_limit = False
        self._dragging = False
        self._last_mouse_x = 0
        self._last_mouse_y = 0
        self._rotated_cache_dir: Path | None = None
        self._capability = capability or active_graphics()
        super().__init__(id=id)

    def compose(self) -> ComposeResult:
        # In a terminal without pixel graphics the crop below is a coarse
        # colour-block approximation -- unreadable for handwriting. Say so
        # once, permanently, and point at the escape hatch, rather than
        # leaving the grader to conclude the scan itself is bad.
        if not self._capability.pixel_accurate:
            yield Static(self._fallback_notice(), id="response-viewer-fallback")

    def _fallback_notice(self) -> str:
        where = self._capability.terminal or "this terminal"
        return (
            f"[b]Low-fidelity preview[/b] - {where} cannot display images. "
            "Press [b]o[/b] to open the displayed image in your image viewer."
        )

    @property
    def capability(self) -> GraphicsCapability:
        return self._capability

    @property
    def image_path(self) -> str:
        """Path of the image currently on display."""
        return self._image_path

    def on_mount(self) -> None:
        self.set_image_path(self._image_path)
        # Ensure scrollbars are calculated after initial mounting
        self.call_after_refresh(self.refresh, layout=True)

    @property
    def rotation(self) -> int:
        """Clockwise display rotation in degrees (0/90/180/270)."""
        return self._image_widget.rotation if self._image_widget else 0

    def rotate(self, degrees: int) -> None:
        """Turn the displayed image by ``degrees`` (negative = anticlockwise).

        A view transform only: the crop on disk, the grade, and the persisted
        transcription are all untouched. This is for the answer a student wrote
        sideways in the box — turning the paper is what a human would do, and
        the alternative is grading with your head tilted.
        """
        if self._image_widget is None:
            return
        self._image_widget.rotation = (self._image_widget.rotation + degrees) % 360
        # A quarter turn changes the widget's shape, so the container has to
        # re-measure; and like a zoom-out it can free cells that terminal
        # graphics would otherwise keep painted.
        self.call_after_refresh(self._refresh_after_image_change)
        self.scroll_home(animate=False)

    def reset_rotation(self) -> None:
        if self._image_widget is not None and self._image_widget.rotation:
            self.rotate(-self._image_widget.rotation)

    def open_externally(self) -> str:
        """Hand the displayed image to the OS image viewer.

        Returns the opened file name; raises ``ExternalViewerError`` if the
        image is missing or no viewer could be launched.

        Honours the on-screen rotation, because ``o`` opens *what is displayed*
        — on a terminal with no graphics protocol this hatch is the only way to
        actually read the answer, so handing over the un-turned original would
        defeat the rotation exactly where it matters most.
        """
        return open_in_external_viewer(self._displayed_image_path())

    def _displayed_image_path(self) -> str:
        """The image as shown: the source file, or a turned copy of it.

        The copy is written once per orientation into a per-session temp
        directory (the same approach as the grade screen's page renders) and
        reused, so repeatedly toggling the viewer costs nothing and the source
        crop is never modified.
        """
        rotation = self.rotation
        if not rotation:
            return self._image_path

        source = Path(self._image_path)
        if self._rotated_cache_dir is None:
            self._rotated_cache_dir = Path(
                tempfile.mkdtemp(prefix="opengrader-rotated-")
            )
        destination = self._rotated_cache_dir / f"{source.stem}-r{rotation}.png"
        if not destination.exists():
            try:
                with PILImage.open(source) as opened:
                    transpose = _ROTATION_TRANSPOSE.get(rotation)
                    turned = (
                        opened.transpose(transpose) if transpose else opened.copy()
                    )
                    turned.save(destination)
            except OSError:
                # Unreadable or unwritable: fall back to the original rather
                # than failing the one escape hatch the grader has left.
                return self._image_path
        return str(destination)

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
                capability=self._capability,
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

        Terminal graphics outlive Textual's cell diffing: sixel/iTerm2 pixels
        stay painted (they are written straight to the terminal, not tracked
        as cells) and stale TGP placeholders can survive a partial repaint.
        When the new image is smaller than the previous one, the freed cells
        would keep showing the previous image unless every cell is re-emitted.
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
