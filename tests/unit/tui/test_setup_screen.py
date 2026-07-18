import asyncio

from textual.containers import VerticalScroll

from opengrader.tui.tui import TuiApp

from .fakes import DEFAULT_IMAGE_PATH


def test_setup_scroll_reaches_launch_section() -> None:
    """The launch section must be fully scrollable into view.

    Regression test: #launch-section defaulted to 1fr height inside the
    scroll container, clipping its children and truncating the scroll
    range so the launch button could never be reached on short terminals.
    """

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()

            launch_section = app.query_one("#launch-section")
            children_height = sum(
                child.outer_size.height for child in launch_section.children
            )
            assert launch_section.outer_size.height == children_height

            container = app.query_one("#setup-container", VerticalScroll)
            container.scroll_end(animate=False)
            await pilot.pause()

            button = app.query_one("#launch-button")
            assert button.region.height > 0
            assert button.region.y >= 0
            assert button.region.y + button.region.height <= app.size.height

    asyncio.run(run())
