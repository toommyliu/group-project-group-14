import asyncio
from pathlib import Path

from textual.pilot import Pilot
from textual.widgets import Collapsible, Input

from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens import PathPickerScreen

from .fakes import DEFAULT_IMAGE_PATH


def _setup_app() -> TuiApp:
    return TuiApp(default_image_path=DEFAULT_IMAGE_PATH)


async def _expand_pipeline_section(app: TuiApp, pilot: Pilot) -> None:
    app.query_one("#section-pipeline", Collapsible).collapsed = False
    # The answer-key field lives under Advanced settings.
    app.query_one("#pipe-advanced", Collapsible).collapsed = False
    await pilot.pause()
    await pilot.wait_for_scheduled_animations()
    await pilot.pause()


async def _click_browse(app: TuiApp, pilot: Pilot, input_id: str) -> None:
    # The card is taller than the screen: bring the row into view first.
    app.query_one(f"#browse-{input_id}").scroll_visible(animate=False)
    await pilot.pause()
    await pilot.click(f"#browse-{input_id}")
    await pilot.pause()


def test_browse_button_opens_picker_and_file_click_fills_input(tmp_path: Path) -> None:
    sample_pdf = tmp_path / "scan.pdf"
    sample_pdf.write_bytes(b"%PDF-1.4")

    async def run() -> None:
        app = _setup_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _expand_pipeline_section(app, pilot)

            file_input = app.query_one("#pipe-qa", Input)
            file_input.value = str(tmp_path)

            await _click_browse(app, pilot, "pipe-qa")
            assert isinstance(app.screen, PathPickerScreen)

            await pilot.pause()
            await pilot.press("down", "enter")
            await pilot.pause()

            assert not isinstance(app.screen, PathPickerScreen)
            assert file_input.value == str(sample_pdf)

    asyncio.run(run())


def test_picker_escape_cancels_without_touching_input(tmp_path: Path) -> None:
    async def run() -> None:
        app = _setup_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _expand_pipeline_section(app, pilot)

            file_input = app.query_one("#pipe-qa", Input)
            file_input.value = str(tmp_path)

            await _click_browse(app, pilot, "pipe-qa")
            assert isinstance(app.screen, PathPickerScreen)

            await pilot.press("escape")
            await pilot.pause()

            assert not isinstance(app.screen, PathPickerScreen)
            assert file_input.value == str(tmp_path)

    asyncio.run(run())


def test_directory_mode_select_button_confirms_highlighted_directory(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        app = _setup_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _expand_pipeline_section(app, pilot)

            output_input = app.query_one("#pipe-out", Input)
            output_input.value = str(tmp_path)

            await _click_browse(app, pilot, "pipe-out")
            assert isinstance(app.screen, PathPickerScreen)

            await pilot.click("#path-picker-select")
            await pilot.pause()

            assert not isinstance(app.screen, PathPickerScreen)
            assert output_input.value == str(tmp_path)

    asyncio.run(run())


def test_file_mode_select_button_rejects_directory(tmp_path: Path) -> None:
    async def run() -> None:
        app = _setup_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _expand_pipeline_section(app, pilot)

            file_input = app.query_one("#pipe-qa", Input)
            file_input.value = str(tmp_path)

            await _click_browse(app, pilot, "pipe-qa")

            await pilot.click("#path-picker-select")
            await pilot.pause()

            assert isinstance(app.screen, PathPickerScreen)
            assert file_input.value == str(tmp_path)

            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(run())


def test_picker_defaults_to_home_directory() -> None:
    picker = PathPickerScreen(title="Choose a file", mode="file")
    assert picker._start_path == Path.home()


def test_picker_falls_back_to_home_for_missing_start_path() -> None:
    picker = PathPickerScreen(
        title="Choose a file",
        mode="file",
        start_path=Path("/does/not/exist"),
    )
    assert picker._start_path == Path.home()
