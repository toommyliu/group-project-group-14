from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, RichLog, Static

from opengrader.core import ai_config
from opengrader.core.environment import EnvironmentReport, build_environment_report
from opengrader.core.llm.ollama_client import OllamaConfig
from opengrader.tui.formatting import (
    format_environment_check,
    format_environment_verdict,
)


class DoctorScreen(ModalScreen[None]):
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
        vision_model: str = OllamaConfig.model,
        text_model: str | None = None,
        ollama_host: str = OllamaConfig.host,
    ) -> None:
        super().__init__()
        self._vision_model = vision_model
        self._text_model = text_model
        self._ollama_host = ollama_host

    def compose(self) -> ComposeResult:
        with Vertical(id="doctor-dialog") as dialog:
            dialog.border_title = "▸ Check this computer"
            yield Static(
                "Checks everything Opengrader needs that Python cannot install "
                "for itself — the scanner's text recognition, the optional local "
                "AI, and whether this terminal can show answer crops — and names "
                "the exact command to fix anything missing. Nothing is changed.",
                id="doctor-hint",
            )
            yield Checkbox(
                "Also build a test exam PDF",
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
            ai=ai_config.resolve(),
            time_ai=False,
        )
        self.app.call_from_thread(self._show, report)

    def _show(self, report: EnvironmentReport) -> None:
        if not self.is_attached:
            return
        for button in self.query(Button):
            button.disabled = False
        log = self.query_one("#doctor-log", RichLog)
        log.clear()
        for check in report.checks:
            log.write(format_environment_check(check))
        log.write("")
        log.write(format_environment_verdict(report))

        if report.broken:
            self.app.notify(
                f"{len(report.broken)} problem(s) need fixing — see the result pane.",
                severity="error",
            )

    @on(Button.Pressed, "#doctor-close")
    def _on_close(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
