import asyncio
from pathlib import Path

from PIL import Image as PILImage
from textual.app import App, ComposeResult
from textual_image.widget import Image

from opengrader.tui.ui.widgets.response_viewer import ResponseViewer


class ResponseViewerTestApp(App):
    def __init__(self, image_path: str) -> None:
        self.image_path = image_path
        super().__init__()

    def compose(self) -> ComposeResult:
        yield ResponseViewer(self.image_path, id="viewer")


def _write_image(path: Path, color: tuple[int, int, int]) -> None:
    PILImage.new("RGB", (24, 16), color=color).save(path)


def test_response_viewer_keeps_image_widget_when_path_is_unchanged(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        image_path = tmp_path / "response.png"
        _write_image(image_path, (255, 255, 255))

        app = ResponseViewerTestApp(str(image_path))
        async with app.run_test() as pilot:
            await pilot.pause()

            viewer = app.query_one("#viewer", ResponseViewer)
            image_widget = viewer.query_one(Image)

            viewer.set_image_path(str(image_path))
            await pilot.pause()

            assert viewer.query_one(Image) is image_widget

    asyncio.run(run())


def test_response_viewer_updates_existing_image_widget_for_new_path(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        first_path = tmp_path / "first.png"
        second_path = tmp_path / "second.png"
        _write_image(first_path, (255, 255, 255))
        _write_image(second_path, (0, 0, 0))

        app = ResponseViewerTestApp(str(first_path))
        async with app.run_test() as pilot:
            await pilot.pause()

            viewer = app.query_one("#viewer", ResponseViewer)
            image_widget = viewer.query_one(Image)

            viewer.set_image_path(str(second_path))
            await pilot.pause()

            updated_widget = viewer.query_one(Image)
            assert updated_widget is image_widget
            assert updated_widget.image == str(second_path)

    asyncio.run(run())


def test_new_image_triggers_full_screen_repaint(tmp_path: Path) -> None:
    """Changing the displayed image must repaint the whole screen.

    Terminal graphics (sixel pixels, TGP placeholders) survive Textual's
    partial repaints, so a smaller replacement image would otherwise leave
    remnants of the previous, larger one on screen.
    """

    async def run() -> None:
        first_path = tmp_path / "first.png"
        second_path = tmp_path / "second.png"
        _write_image(first_path, (255, 255, 255))
        _write_image(second_path, (0, 0, 0))

        app = ResponseViewerTestApp(str(first_path))
        async with app.run_test() as pilot:
            await pilot.pause()

            viewer = app.query_one("#viewer", ResponseViewer)
            screen_refreshes = 0
            original_refresh = app.screen.refresh

            def counting_refresh(*args, **kwargs):
                nonlocal screen_refreshes
                if not args and not kwargs:
                    screen_refreshes += 1
                return original_refresh(*args, **kwargs)

            app.screen.refresh = counting_refresh
            viewer.set_image_path(str(second_path))
            await pilot.pause()

            assert screen_refreshes >= 1

    asyncio.run(run())
