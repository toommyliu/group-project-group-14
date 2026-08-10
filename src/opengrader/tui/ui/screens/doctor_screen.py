"""Environment pre-flight from inside the TUI.

The headless `doctor` CLI is the right tool for someone already in a terminal.
This dialog is for the person Opengrader is actually aimed at: an instructor who
launched the app from a desktop icon, has no terminal open, and is looking at
answer crops rendered as coloured blocks or an extraction that produced empty
answers. Asking them to find a shell and type a command is how a fixable problem
becomes an abandoned tool.

It lives on the setup screen's Helper Tools and deliberately has **no workspace
hotkey**: unlike the sharing and backup tools, this is about the machine rather
than about the cohort being graded, and the keymap's shift+letter group is
reserved for reports about the class.

Engine: ``core/environment.py`` (all the judgement); rendering:
``tui/formatting.py``.
"""

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
    """Check this machine for everything Opengrader needs, and say how to fix it."""

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
        # Prefilled from whatever the setup screen's Ollama fields say, so the
        # check is run against the models this user's pipeline would actually
        # ask for rather than against the defaults.
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
            # Short label, detail underneath: a Textual Checkbox is one line
            # tall (ToggleButton.get_content_height returns 1), so anything past
            # the dialog's width is silently dropped.
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
        # Run once on open rather than making the user press a button to learn
        # anything: they opened this dialog because something is already wrong.
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
        # A thread worker: probing reaches out to the Ollama server and runs the
        # Tesseract binary, and neither may block the UI event loop.
        self.run_worker(lambda: self._run(smoke), thread=True, exclusive=True)

    def _run(self, smoke: bool) -> None:
        report = build_environment_report(
            vision_model=self._vision_model,
            text_model=self._text_model,
            ollama_host=self._ollama_host,
            # The terminal check is skipped here on purpose: inside a running
            # Textual app the answer is already known and re-probing would write
            # escape sequences into a terminal the app owns. The setup screen
            # states the detected protocol instead.
            check_graphics=False,
            smoke_test=smoke,
            # What the AI is switched on to *do*, not only which models are
            # configured. Someone whose scans extract to empty answers may
            # simply have handwriting reading turned off, and until this row
            # existed the report could not say so. Resolved here rather than
            # passed in by the caller, for the same reason the setup screen
            # re-reads on every use: AI setup may have been saved since this
            # screen was opened.
            ai=ai_config.resolve(),
            # Timing a real request is deliberately left to `doctor --time-ai`
            # and to AI setup's Test connection: a first call loads the model's
            # weights and can take half a minute, which is too slow to spend on
            # every press of a button labelled "Run check".
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

        # A modal the user has to read to discover a failure is a modal whose
        # failures get missed; the notification carries the verdict out.
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
