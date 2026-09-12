import asyncio
from pathlib import Path

from PIL import Image as PILImage
from textual.app import App, ComposeResult
from opengrader.tui.ui.widgets.sharp_image import SharpImage

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
            image_widget = viewer.query_one(SharpImage)

            viewer.set_image_path(str(image_path))
            await pilot.pause()

            assert viewer.query_one(SharpImage) is image_widget

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
            image_widget = viewer.query_one(SharpImage)

            viewer.set_image_path(str(second_path))
            await pilot.pause()

            updated_widget = viewer.query_one(SharpImage)
            assert updated_widget is image_widget
            assert updated_widget.image == str(second_path)

    asyncio.run(run())


def test_new_image_triggers_full_screen_repaint(tmp_path: Path) -> None:
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


def test_degraded_terminal_gets_a_permanent_fallback_notice(tmp_path: Path) -> None:
    from textual.widgets import Static

    from opengrader.tui.terminal_graphics import GraphicsCapability

    async def run() -> None:
        image_path = tmp_path / "response.png"
        _write_image(image_path, (255, 255, 255))

        class FallbackApp(App):
            def compose(self) -> ComposeResult:
                yield ResponseViewer(
                    str(image_path),
                    id="viewer",
                    capability=GraphicsCapability(
                        "halfcell", source="test", terminal="Terminal.app"
                    ),
                )

        app = FallbackApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            notice = app.query_one("#response-viewer-fallback", Static)
            rendered = str(notice.render())
            assert "Terminal.app" in rendered
            assert "o" in rendered

    asyncio.run(run())


def test_pixel_capable_terminal_gets_no_notice(tmp_path: Path) -> None:
    from textual.css.query import NoMatches

    from opengrader.tui.terminal_graphics import GraphicsCapability

    async def run() -> None:
        image_path = tmp_path / "response.png"
        _write_image(image_path, (255, 255, 255))

        class KittyApp(App):
            def compose(self) -> ComposeResult:
                yield ResponseViewer(
                    str(image_path),
                    id="viewer",
                    capability=GraphicsCapability("kitty", source="test"),
                )

        app = KittyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            try:
                app.query_one("#response-viewer-fallback")
            except NoMatches:
                return
            raise AssertionError("fallback notice shown on a capable terminal")

    asyncio.run(run())


def test_open_externally_targets_the_image_on_display(tmp_path: Path) -> None:
    import opengrader.tui.ui.widgets.response_viewer as module

    async def run() -> None:
        crop = tmp_path / "crop.png"
        math = tmp_path / "math.png"
        _write_image(crop, (255, 255, 255))
        _write_image(math, (0, 0, 0))

        opened: list[str] = []
        original = module.open_in_external_viewer
        module.open_in_external_viewer = lambda path: (
            opened.append(str(path)) or Path(path).name
        )
        try:
            app = ResponseViewerTestApp(str(crop))
            async with app.run_test() as pilot:
                await pilot.pause()
                viewer = app.query_one("#viewer", ResponseViewer)

                assert viewer.open_externally() == "crop.png"

                viewer.set_image_path(str(math))
                await pilot.pause()
                assert viewer.open_externally() == "math.png"
        finally:
            module.open_in_external_viewer = original

        assert opened == [str(crop), str(math)]

    asyncio.run(run())
