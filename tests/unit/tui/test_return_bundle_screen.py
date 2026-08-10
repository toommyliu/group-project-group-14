"""Tests for the return-bundle TUI modal and its b hotkey."""

import asyncio
from pathlib import Path

from textual.app import App
from textual.widgets import Input, Select

from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens import PathPickerScreen, ReturnBundleScreen
from opengrader.tui.ui.screens import return_bundle_screen as bundle_module

from .fakes import DEFAULT_IMAGE_PATH, FakeDataProvider


def _qa_picker_app() -> TuiApp:
    return TuiApp(
        default_image_path=DEFAULT_IMAGE_PATH,
        data_provider=FakeDataProvider(),
    )


class _BundleApp(App):
    """Minimal host that pushes a ReturnBundleScreen with a known submissions path."""

    def __init__(self, submissions_path: str) -> None:
        super().__init__()
        self._submissions_path = submissions_path

    def on_mount(self) -> None:
        self.push_screen(
            ReturnBundleScreen(
                submissions_path=self._submissions_path,
                output_path="/tmp/out",
                default_name="quiz-1-return-2026-07-28",
            )
        )


def test_b_opens_return_bundle_dialog() -> None:
    async def run() -> None:
        app = _qa_picker_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("b")
            await pilot.pause()
            assert isinstance(app.screen, ReturnBundleScreen)

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ReturnBundleScreen)

    asyncio.run(run())


def test_content_dropdown_defaults_to_everything() -> None:
    async def run() -> None:
        app = _BundleApp("/tmp/subs")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            select = app.screen.query_one("#return-bundle-content", Select)
            assert select.value == "all"
            values = [value for _label, value in select._options]  # type: ignore[attr-defined]
            assert set(values) == {
                "all",
                "gradebook",
                "student-pdfs",
                "gradebook-student",
                "analysis",
            }

    asyncio.run(run())


def test_export_builds_expected_argv(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_cli(argv: list[str]) -> None:
        calls.append(argv)
        print("Bundle written to /somewhere")

    monkeypatch.setattr(bundle_module, "run_return_bundle_cli", fake_cli)

    async def run() -> None:
        app = _BundleApp(str(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#return-bundle-output", Input).value = str(tmp_path / "dest")
            screen.query_one("#return-bundle-name", Input).value = "my-return"
            screen.query_one("#return-bundle-content", Select).value = "gradebook-student"
            await pilot.pause()

            await pilot.click("#return-bundle-run")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert calls == [
                [
                    "--submissions", str(tmp_path),
                    "--content", "gradebook-student",
                    "--output", str(tmp_path / "dest"),
                    "--name", "my-return",
                ]
            ]
            assert not screen.query_one("#return-bundle-run").disabled

    asyncio.run(run())


def test_export_without_submissions_notifies_and_skips(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        bundle_module, "run_return_bundle_cli", lambda argv: calls.append(argv)
    )

    async def run() -> None:
        app = _BundleApp("")  # no submissions path
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#return-bundle-run")
            await pilot.pause()
            assert calls == []

    asyncio.run(run())


def test_browse_fills_output_and_uses_directory_mode(tmp_path: Path) -> None:
    async def run() -> None:
        app = _BundleApp(str(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            output_input = app.screen.query_one("#return-bundle-output", Input)
            output_input.value = str(tmp_path)

            await pilot.click("#return-bundle-browse")
            await pilot.pause()
            assert isinstance(app.screen, PathPickerScreen)

            await pilot.click("#path-picker-select")
            await pilot.pause()
            assert isinstance(app.screen, ReturnBundleScreen)
            assert output_input.value == str(tmp_path)

    asyncio.run(run())
