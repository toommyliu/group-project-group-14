"""Asks whether to auto-grade without AI when the AI that grades written
answers can't be reached (Ollama not running, say).

The pipeline pauses before auto-grading for this; `opengrader run` asks the
same question in a terminal. Yes re-runs the request with
`grade_without_ai=True`; No (or Escape) re-runs it with auto-grading off, so
the submissions are still published and ready to grade by hand. Either way a
later run that can reach the AI grades the written answers with it.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from opengrader.tui.ui.button_roles import OutlineButtons

_CHOICES = (
    "Yes: grade without AI now. Only answers that can be checked exactly "
    "against the answer key are scored; the rest are left for you.\n"
    "No: skip auto-grading. Start the AI and run again to auto-grade."
)


class GradeWithoutAIScreen(OutlineButtons, ModalScreen[bool]):
    """True: auto-grade without AI. False: skip auto-grading."""

    BINDINGS = [Binding("escape", "skip", "Skip auto-grading")]

    DEFAULT_CSS = """
    GradeWithoutAIScreen {
        align: center middle;
    }

    #grade-without-ai-dialog {
        width: 84%;
        max-width: 90;
        height: auto;
        background: $surface;
        border: round $warning;
        border-title-align: left;
        border-title-color: $warning;
        border-title-style: bold;
        padding: 1 2;
    }

    #grade-without-ai-problem {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }

    #grade-without-ai-choices {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #grade-without-ai-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #grade-without-ai-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, problem: str) -> None:
        super().__init__()
        self._problem = problem

    def compose(self) -> ComposeResult:
        with Vertical(id="grade-without-ai-dialog") as dialog:
            dialog.border_title = "▸ Auto-grade without AI?"
            yield Static(self._problem, id="grade-without-ai-problem", markup=False)
            yield Static(_CHOICES, id="grade-without-ai-choices", markup=False)
            with Horizontal(id="grade-without-ai-buttons"):
                yield Button("No, skip auto-grading", id="grade-without-ai-no")
                yield Button("Yes, grade without AI", id="grade-without-ai-yes", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#grade-without-ai-yes", Button).focus()

    @on(Button.Pressed, "#grade-without-ai-yes")
    def _on_yes(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(True)

    @on(Button.Pressed, "#grade-without-ai-no")
    def _on_no(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(False)

    def action_skip(self) -> None:
        self.dismiss(False)
