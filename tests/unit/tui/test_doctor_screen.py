"""The environment-check modal (start-up screen → Helper Tools)."""

from __future__ import annotations

import asyncio

import pytest
from textual.app import App
from textual.widgets import Button, Checkbox

from opengrader.core.environment import EnvironmentCheck, EnvironmentReport
from opengrader.tui.ui.screens import doctor_screen
from opengrader.tui.ui.screens.doctor_screen import DoctorScreen


class _Host(App):
    def __init__(self, screen: DoctorScreen) -> None:
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _log_text(screen: DoctorScreen) -> str:
    return "".join(str(line) for line in screen.query_one("#doctor-log").lines)


async def _settle(pilot, predicate, limit: int = 80) -> None:  # noqa: ANN001
    for _ in range(limit):
        await pilot.pause()
        await asyncio.sleep(0.01)
        if predicate():
            return


def _stub_engine(monkeypatch: pytest.MonkeyPatch, report: EnvironmentReport) -> dict:
    """Replace the probing engine so these tests need no Tesseract or Ollama."""
    recorded: dict = {}

    def fake_build(**kwargs) -> EnvironmentReport:
        recorded.update(kwargs)
        return report

    monkeypatch.setattr(doctor_screen, "build_environment_report", fake_build)
    return recorded


def _ready_report() -> EnvironmentReport:
    return EnvironmentReport(
        checks=(EnvironmentCheck(name="Python", status="ready", detail="3.12.4"),)
    )


def test_checks_run_on_open_without_pressing_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Someone opens this dialog because something is already wrong; making them
    # press a button first to learn anything wastes the one interaction.
    _stub_engine(monkeypatch, _ready_report())

    async def run() -> None:
        screen = DoctorScreen()
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot, lambda: "Python" in _log_text(screen))
            text = _log_text(screen)
            assert "3.12.4" in text
            assert "Everything Opengrader needs" in text

    asyncio.run(run())


def test_a_failing_check_shows_its_impact_and_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_engine(
        monkeypatch,
        EnvironmentReport(
            checks=(
                EnvironmentCheck(
                    name="Tesseract OCR",
                    status="broken",
                    detail="not installed",
                    impact="Scanned exams extract to empty answers.",
                    fix="brew install tesseract",
                ),
            )
        ),
    )

    async def run() -> None:
        screen = DoctorScreen()
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot, lambda: "Tesseract" in _log_text(screen))
            text = _log_text(screen)
            assert "Scanned exams extract to empty answers." in text
            assert "brew install tesseract" in text
            assert "cannot run correctly" in text

    asyncio.run(run())


def test_the_terminal_check_is_skipped_inside_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Re-probing the terminal would write escape sequences into a terminal
    # Textual owns, and the answer is already known by the time the app runs.
    recorded = _stub_engine(monkeypatch, _ready_report())

    async def run() -> None:
        app = _Host(DoctorScreen())
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot, lambda: bool(recorded))
            assert recorded["check_graphics"] is False

    asyncio.run(run())


def test_the_ollama_settings_from_the_setup_screen_are_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _stub_engine(monkeypatch, _ready_report())

    async def run() -> None:
        screen = DoctorScreen(
            vision_model="llama3.2-vision:11b",
            text_model="llama3.1:8b",
            ollama_host="http://box:11434",
        )
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot, lambda: bool(recorded))
            assert recorded["vision_model"] == "llama3.2-vision:11b"
            assert recorded["text_model"] == "llama3.1:8b"
            assert recorded["ollama_host"] == "http://box:11434"

    asyncio.run(run())


def test_the_smoke_test_is_off_until_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = _stub_engine(monkeypatch, _ready_report())

    async def run() -> None:
        screen = DoctorScreen()
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot, lambda: bool(recorded))
            assert recorded["smoke_test"] is False

            screen.query_one("#doctor-smoke", Checkbox).value = True
            recorded.clear()
            await pilot.click("#doctor-run")
            await _settle(pilot, lambda: bool(recorded))
            assert recorded["smoke_test"] is True

    asyncio.run(run())


def test_buttons_come_back_after_a_run(monkeypatch: pytest.MonkeyPatch) -> None:
    # They are disabled while probing; leaving them disabled would strand the
    # dialog with no way to close it.
    _stub_engine(monkeypatch, _ready_report())

    async def run() -> None:
        screen = DoctorScreen()
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(
                pilot, lambda: not screen.query_one("#doctor-run", Button).disabled
            )
            assert not screen.query_one("#doctor-close", Button).disabled

    asyncio.run(run())
