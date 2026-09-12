import asyncio
from pathlib import Path

from textual.pilot import Pilot
from textual.widgets import Collapsible, Input

from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens import PathPickerScreen

from .fakes import DEFAULT_IMAGE_PATH


def _setup_app() -> TuiApp:
    return TuiApp(default_image_path=DEFAULT_IMAGE_PATH)


async def _expand_splitter_section(app: TuiApp, pilot: Pilot) -> None:
    app.query_one("#section-splitter", Collapsible).collapsed = False
    await pilot.pause()
    await pilot.wait_for_scheduled_animations()
    await pilot.pause()


def test_browse_button_opens_picker_and_file_click_fills_input(tmp_path: Path) -> None:
    sample_pdf = tmp_path / "scan.pdf"
    sample_pdf.write_bytes(b"%PDF-1.4")

    async def run() -> None:
        app = _setup_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _expand_splitter_section(app, pilot)

            split_input = app.query_one("#split-input", Input)
            split_input.value = str(tmp_path)

            await pilot.click("#browse-split-input")
            await pilot.pause()
            assert isinstance(app.screen, PathPickerScreen)

            await pilot.pause()
            await pilot.press("down", "enter")
            await pilot.pause()

            assert not isinstance(app.screen, PathPickerScreen)
            assert split_input.value == str(sample_pdf)

    asyncio.run(run())


def test_picker_escape_cancels_without_touching_input(tmp_path: Path) -> None:
    async def run() -> None:
        app = _setup_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _expand_splitter_section(app, pilot)

            split_input = app.query_one("#split-input", Input)
            split_input.value = str(tmp_path)

            await pilot.click("#browse-split-input")
            await pilot.pause()
            assert isinstance(app.screen, PathPickerScreen)

            await pilot.press("escape")
            await pilot.pause()

            assert not isinstance(app.screen, PathPickerScreen)
            assert split_input.value == str(tmp_path)

    asyncio.run(run())


def test_directory_mode_select_button_confirms_highlighted_directory(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        app = _setup_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _expand_splitter_section(app, pilot)

            output_input = app.query_one("#split-output", Input)
            output_input.value = str(tmp_path)

            await pilot.click("#browse-split-output")
            await pilot.pause()
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
            await _expand_splitter_section(app, pilot)

            split_input = app.query_one("#split-input", Input)
            split_input.value = str(tmp_path)

            await pilot.click("#browse-split-input")
            await pilot.pause()

            await pilot.click("#path-picker-select")
            await pilot.pause()

            assert isinstance(app.screen, PathPickerScreen)
            assert split_input.value == str(tmp_path)

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
