import asyncio
from pathlib import Path

from textual.widgets import Input

from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens import CanvasExportScreen, PathPickerScreen
from opengrader.tui.ui.screens import canvas_export_screen as export_module

from .fakes import DEFAULT_IMAGE_PATH, FakeDataProvider


def _qa_picker_app() -> TuiApp:
    # A non-stub provider starts the app on the QA picker page.
    return TuiApp(
        default_image_path=DEFAULT_IMAGE_PATH,
        data_provider=FakeDataProvider(),
    )


def test_x_opens_canvas_export_dialog() -> None:
    async def run() -> None:
        app = _qa_picker_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            await pilot.press("x")
            await pilot.pause()

            assert isinstance(app.screen, CanvasExportScreen)

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, CanvasExportScreen)

    asyncio.run(run())


def test_export_requires_all_fields(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        export_module, "run_canvas_export_cli", lambda argv: calls.append(argv)
    )

    async def run() -> None:
        app = _qa_picker_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause()

            # Submissions/course/assignment are all empty.
            await pilot.click("#canvas-export-run")
            await pilot.pause()

            assert isinstance(app.screen, CanvasExportScreen)
            assert calls == []

    asyncio.run(run())


def test_export_runs_cli_with_expected_argv(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_cli(argv: list[str]) -> None:
        calls.append(argv)
        print("2 ready, 0 skipped, 0 failed.")

    monkeypatch.setattr(export_module, "run_canvas_export_cli", fake_cli)

    async def run() -> None:
        app = _qa_picker_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause()

            app.screen.query_one("#canvas-export-submissions", Input).value = str(tmp_path)
            app.screen.query_one("#canvas-export-course", Input).value = "CS146"
            app.screen.query_one("#canvas-export-assignment", Input).value = "Quiz 1"

            await pilot.click("#canvas-export-run")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert calls == [
                [
                    "--submissions", str(tmp_path),
                    "--course", "CS146",
                    "--assignment", "Quiz 1",
                    "--dry-run",
                ]
            ]
            # The Export button is usable again after the run.
            assert not app.screen.query_one("#canvas-export-run").disabled

    asyncio.run(run())


def test_unchecking_dry_run_omits_the_flag(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        export_module, "run_canvas_export_cli", lambda argv: calls.append(argv)
    )

    async def run() -> None:
        app = _qa_picker_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause()

            app.screen.query_one("#canvas-export-submissions", Input).value = str(tmp_path)
            app.screen.query_one("#canvas-export-course", Input).value = "CS146"
            app.screen.query_one("#canvas-export-assignment", Input).value = "Quiz 1"
            app.screen.query_one("#canvas-export-dry-run").value = False

            await pilot.click("#canvas-export-run")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert len(calls) == 1
            assert "--dry-run" not in calls[0]

    asyncio.run(run())


def test_browse_button_fills_submissions_input(tmp_path: Path) -> None:
    async def run() -> None:
        app = _qa_picker_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause()

            submissions_input = app.screen.query_one("#canvas-export-submissions", Input)
            submissions_input.value = str(tmp_path)

            await pilot.click("#canvas-export-browse")
            await pilot.pause()
            assert isinstance(app.screen, PathPickerScreen)

            # Confirm the highlighted root directory ("any" mode accepts it).
            await pilot.click("#path-picker-select")
            await pilot.pause()

            assert isinstance(app.screen, CanvasExportScreen)
            assert submissions_input.value == str(tmp_path)

    asyncio.run(run())
