"""Modal grading-session analytics, opened with v (teacher mode only).

A read-only pop-up summarizing grading throughput — how much grading happened,
how fast, over how many sessions, per grader — computed from the same audit
journal the grade-history view (h) lists. Because it names graders, it's kept to
teacher mode like grade history. esc or v closes it.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from opengrader.core.analytics import GradingSessionReport
from opengrader.tui.formatting import (
    format_grading_session_actor,
    format_grading_session_overview,
)


class GradingSessionScreen(ModalScreen[None]):
    """Read-only grading throughput summary + per-grader breakdown."""

    DEFAULT_CSS = """
    GradingSessionScreen {
        align: center middle;
    }

    #grading-session-dialog {
        width: 80%;
        max-width: 110;
        height: 80%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #grading-session-overview {
        width: 100%;
        height: auto;
        padding: 0 1 1 1;
        border-bottom: round $surface-lighten-2;
    }

    #grading-session-body {
        height: 1fr;
        margin-top: 1;
        scrollbar-size: 1 1;
    }

    #grading-session-body .grading-session-heading {
        color: $text-muted;
        text-style: bold;
        margin-bottom: 1;
    }

    #grading-session-body .grading-session-actor {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }

    #grading-session-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("v", "close", "Close"),
    ]

    def __init__(self, report: GradingSessionReport) -> None:
        self._report = report
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="grading-session-dialog") as dialog:
            dialog.border_title = "▸ Grading Session Analytics"
            yield Static(
                format_grading_session_overview(self._report),
                id="grading-session-overview",
            )
            with VerticalScroll(id="grading-session-body"):
                if self._report.actors:
                    yield Static(
                        "PER GRADER", classes="grading-session-heading"
                    )
                    for actor in self._report.actors:
                        yield Static(
                            format_grading_session_actor(actor),
                            classes="grading-session-actor",
                        )
            with Horizontal(id="grading-session-buttons"):
                yield Button("Close", id="grading-session-close", compact=True)

    def on_mount(self) -> None:
        self.query_one("#grading-session-close", Button).focus()

    @on(Button.Pressed, "#grading-session-close")
    def _on_close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
