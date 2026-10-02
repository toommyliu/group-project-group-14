from __future__ import annotations

import asyncio
import threading

import pytest
from textual.app import App
from textual.widgets import Button, Checkbox

from opengrader.core.environment import (
    TESSERACT_CHECK_NAME,
    EnvironmentCheck,
    EnvironmentReport,
)
from opengrader.core.tesseract_install import TesseractInstallError
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
    recorded: dict = {}

    def fake_build(**kwargs) -> EnvironmentReport:
        recorded.update(kwargs)
        return report

    monkeypatch.setattr(doctor_screen, "build_environment_report", fake_build)
    # Whether this machine happens to have Homebrew must not change a test.
    monkeypatch.setattr(doctor_screen, "find_homebrew", lambda: None)
    return recorded


def _ready_report() -> EnvironmentReport:
    return EnvironmentReport(
        checks=(EnvironmentCheck(name="Python", status="ready", detail="3.12.4"),)
    )


def test_checks_run_on_open_without_pressing_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def _missing_tesseract_report() -> EnvironmentReport:
    return EnvironmentReport(
        checks=(
            EnvironmentCheck(
                name=TESSERACT_CHECK_NAME,
                status="broken",
                detail="not installed",
                impact="Scanned exams extract to empty answers.",
                fix="brew install tesseract",
            ),
        )
    )


def _log_words(screen: DoctorScreen) -> str:
    # The log wraps long lines; rejoin them so a sentence can be asserted on.
    lines = screen.query_one("#doctor-log").lines
    return " ".join(" ".join(line.text for line in lines).split())


def _install_button(screen: DoctorScreen) -> Button:
    return screen.query_one("#doctor-install-tesseract", Button)


def test_no_install_button_when_tesseract_is_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_engine(monkeypatch, _ready_report())
    monkeypatch.setattr(doctor_screen, "find_homebrew", lambda: "/opt/homebrew/bin/brew")

    async def run() -> None:
        screen = DoctorScreen()
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot, lambda: "Python" in _log_text(screen))
            assert _install_button(screen).display is False

    asyncio.run(run())


def test_missing_tesseract_without_homebrew_points_at_homebrew(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_engine(monkeypatch, _missing_tesseract_report())
    monkeypatch.setattr(doctor_screen.sys, "platform", "darwin")

    async def run() -> None:
        screen = DoctorScreen()
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot, lambda: "Tesseract" in _log_text(screen))
            assert _install_button(screen).display is False
            assert "https://brew.sh" in _log_words(screen)

    asyncio.run(run())


def test_missing_tesseract_with_homebrew_installs_it_and_checks_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _stub_engine(monkeypatch, _missing_tesseract_report())
    monkeypatch.setattr(doctor_screen, "find_homebrew", lambda: "/opt/homebrew/bin/brew")
    installs: list[str] = []

    def fake_install(brew, *, on_line=None, on_start=None) -> None:  # noqa: ANN001
        installs.append(brew)
        on_line("==> Pouring tesseract")
        # Installed: the next check finds it.
        _stub_engine(monkeypatch, _ready_report())

    monkeypatch.setattr(doctor_screen, "install_tesseract", fake_install)

    async def run() -> None:
        screen = DoctorScreen()
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot, lambda: "Tesseract" in _log_text(screen))
            assert _install_button(screen).display is True
            assert "press Install Tesseract below" in _log_words(screen)
            assert installs == []
            assert recorded

            await pilot.click("#doctor-install-tesseract")
            await _settle(pilot, lambda: "Python" in _log_text(screen))

            assert installs == ["/opt/homebrew/bin/brew"]
            assert "Everything Opengrader needs" in _log_text(screen)
            assert _install_button(screen).display is False
            assert not screen.query_one("#doctor-run", Button).disabled

    asyncio.run(run())


def test_a_failed_install_shows_why_and_can_be_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_engine(monkeypatch, _missing_tesseract_report())
    monkeypatch.setattr(doctor_screen, "find_homebrew", lambda: "/usr/local/bin/brew")

    def fake_install(brew, *, on_line=None, on_start=None) -> None:  # noqa: ANN001
        on_line("Error: no bottle available")
        raise TesseractInstallError("`brew install tesseract` failed (exit 1).")

    monkeypatch.setattr(doctor_screen, "install_tesseract", fake_install)

    async def run() -> None:
        screen = DoctorScreen()
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot, lambda: "Tesseract" in _log_text(screen))
            await pilot.click("#doctor-install-tesseract")
            await _settle(pilot, lambda: "was not installed" in _log_text(screen))

            text = _log_words(screen)
            assert "Error: no bottle available" in text
            assert "failed (exit 1)" in text
            button = _install_button(screen)
            assert button.display is True
            assert not button.disabled

    asyncio.run(run())


def test_closing_the_check_stops_a_running_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_engine(monkeypatch, _missing_tesseract_report())
    monkeypatch.setattr(doctor_screen, "find_homebrew", lambda: "/usr/local/bin/brew")
    release = threading.Event()

    class _Process:
        terminated = False

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            self.terminated = True
            release.set()

    process = _Process()

    def fake_install(brew, *, on_line=None, on_start=None) -> None:  # noqa: ANN001
        on_start(process)
        release.wait(timeout=10)
        raise TesseractInstallError("stopped")

    monkeypatch.setattr(doctor_screen, "install_tesseract", fake_install)

    async def run() -> None:
        screen = DoctorScreen()
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await _settle(pilot, lambda: "Tesseract" in _log_text(screen))
            await pilot.click("#doctor-install-tesseract")
            await _settle(pilot, lambda: screen._install_process is not None)
            await pilot.press("escape")
            await _settle(pilot, lambda: process.terminated)
            assert process.terminated

    try:
        asyncio.run(run())
    finally:
        release.set()
