from __future__ import annotations

import subprocess
import sys

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, RichLog, Static

from opengrader.core import app_config
from opengrader.core.environment import (
    TESSERACT_CHECK_NAME,
    EnvironmentReport,
    build_environment_report,
)
from opengrader.core.homebrew import (
    HOMEBREW_URL,
    HomebrewError,
    brew_install,
    find_homebrew,
)
from opengrader.tui.formatting import (
    format_environment_check,
    format_environment_verdict,
)
from opengrader.tui.ui.button_roles import OutlineButtons


class DoctorScreen(OutlineButtons, ModalScreen[None]):
    DEFAULT_CSS = """
    DoctorScreen {
        align: center middle;
    }

    #doctor-dialog {
        width: 82%;
        max-width: 118;
        height: 82%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #doctor-hint, #doctor-smoke-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    DoctorScreen Checkbox {
        background: transparent;
        border: none;
        padding: 0;
        margin-top: 1;
    }

    #doctor-log {
        height: 1fr;
        margin-top: 1;
        border: round $surface-lighten-2;
        border-title-align: left;
        border-title-color: $text-muted;
        background: transparent;
        padding: 0 1;
    }

    #doctor-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #doctor-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(
        self,
        *,
        vision_model: str = app_config.DEFAULT_VISION_MODEL,
        text_model: str | None = None,
        ollama_host: str = app_config.DEFAULT_HOST,
    ) -> None:
        super().__init__()
        self._vision_model = vision_model
        self._text_model = text_model
        self._ollama_host = ollama_host
        self._brew: str | None = None
        self._install_process: subprocess.Popen[str] | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="doctor-dialog") as dialog:
            dialog.border_title = "▸ Check this computer"
            yield Static(
                "Checks everything Opengrader needs that Python cannot install "
                "for itself — the scanner's text recognition, the optional local "
                "AI, and whether this terminal can show answer crops — and names "
                "the exact command to fix anything missing. Nothing is changed "
                "unless you press Install Tesseract, which is offered on a Mac "
                "when Tesseract is missing and Homebrew is there to install it.",
                id="doctor-hint",
            )
            yield Checkbox(
                "Also build a test assignment PDF",
                value=False,
                id="doctor-smoke",
            )
            yield Static(
                "Runs the real pipeline end to end in a temporary folder — "
                "answer key to printable template — so it proves the whole chain "
                "works, not just that each piece is installed. Slower.",
                id="doctor-smoke-hint",
            )
            log = RichLog(id="doctor-log", wrap=True, auto_scroll=False, markup=False)
            log.border_title = "Result"
            yield log
            with Horizontal(id="doctor-buttons"):
                install = Button(
                    "Install Tesseract",
                    variant="success",
                    id="doctor-install-tesseract",
                    compact=True,
                )
                install.display = False
                yield install
                yield Button(
                    "Run checks", variant="primary", id="doctor-run", compact=True
                )
                yield Button("Close", id="doctor-close", compact=True)

    def on_mount(self) -> None:
        self._start_run()

    @on(Button.Pressed, "#doctor-run")
    def _on_run(self, event: Button.Pressed) -> None:
        event.stop()
        self._start_run()

    def _start_run(self) -> None:
        smoke = self.query_one("#doctor-smoke", Checkbox).value
        for button in self.query(Button):
            button.disabled = True
        log = self.query_one("#doctor-log", RichLog)
        log.clear()
        log.write("Checking…")
        self.run_worker(lambda: self._run(smoke), thread=True, exclusive=True)

    def _run(self, smoke: bool) -> None:
        report = build_environment_report(
            vision_model=self._vision_model,
            text_model=self._text_model,
            ollama_host=self._ollama_host,
            check_graphics=False,
            smoke_test=smoke,
            ai=app_config.resolve(),
            time_ai=False,
        )
        self.app.call_from_thread(self._show, report, find_homebrew())

    def _show(self, report: EnvironmentReport, brew: str | None = None) -> None:
        if not self.is_attached:
            return
        for button in self.query(Button):
            button.disabled = False
        tesseract_missing = any(
            check.name == TESSERACT_CHECK_NAME and check.status == "broken"
            for check in report.checks
        )
        self._brew = brew if tesseract_missing else None
        self.query_one("#doctor-install-tesseract", Button).display = bool(self._brew)
        log = self.query_one("#doctor-log", RichLog)
        log.clear()
        for check in report.checks:
            log.write(format_environment_check(check))
            if check.name == TESSERACT_CHECK_NAME and check.status == "broken":
                if self._brew:
                    log.write(
                        "   Homebrew is installed, so Opengrader can do this for "
                        "you: press Install Tesseract below."
                    )
                elif sys.platform == "darwin":
                    log.write(
                        "   Homebrew, the tool that installs Tesseract, is not on "
                        f"this Mac. Install it from {HOMEBREW_URL}, then run the "
                        "checks again and an Install Tesseract button appears here."
                    )
        log.write("")
        log.write(format_environment_verdict(report))

        if report.broken:
            self.app.notify(
                f"{len(report.broken)} problem(s) need fixing — see the result pane.",
                severity="error",
            )

    @on(Button.Pressed, "#doctor-install-tesseract")
    def _on_install_tesseract(self, event: Button.Pressed) -> None:
        event.stop()
        brew = self._brew
        if brew is None:
            return
        for button in self.query(Button):
            button.disabled = True
        log = self.query_one("#doctor-log", RichLog)
        log.clear()
        log.auto_scroll = True
        log.write(
            "Installing Tesseract with Homebrew. This needs the internet and can "
            "take a few minutes; closing this check stops it."
        )
        log.write("")
        self.run_worker(lambda: self._install(brew), thread=True, exclusive=True)

    def _install(self, brew: str) -> None:
        try:
            brew_install(
                brew,
                ("tesseract",),
                on_line=self._relay_line,
                on_start=self._remember_install,
            )
        except HomebrewError as exc:
            error: HomebrewError | None = exc
        else:
            error = None
        self._install_process = None
        # Closed mid-install: the screen is gone and nobody is left to tell.
        if not self.is_attached:
            return
        self.app.call_from_thread(self._install_finished, error)

    def _remember_install(self, process: subprocess.Popen[str]) -> None:
        self._install_process = process

    def _relay_line(self, line: str) -> None:
        if self.is_attached:
            self.app.call_from_thread(self._log_line, line)

    def _log_line(self, line: str) -> None:
        if self.is_attached:
            self.query_one("#doctor-log", RichLog).write(line)

    def _install_finished(self, error: HomebrewError | None) -> None:
        if not self.is_attached:
            return
        log = self.query_one("#doctor-log", RichLog)
        log.auto_scroll = False
        if error is None:
            self.app.notify("Tesseract installed — checking again.")
            self._start_run()
            return
        for button in self.query(Button):
            button.disabled = False
        log.write("")
        log.write(f"Tesseract was not installed. {error}")
        log.write(
            "Press Install Tesseract to try again, or run "
            "'brew install tesseract' in a terminal to see the whole output."
        )
        self.app.notify("Tesseract was not installed.", severity="error")

    def on_unmount(self) -> None:
        # A worker thread cannot be cancelled; ending the install is what
        # lets it, and the app, finish.
        process = self._install_process
        if process is not None and process.poll() is None:
            process.terminate()

    @on(Button.Pressed, "#doctor-close")
    def _on_close(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
