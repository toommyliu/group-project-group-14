"""Modal grade-change audit log, opened with h.

A read-only pop-up showing the durable history journal — who changed which
grade, from what to what, when — merged across every grading session, newest
last. This is the accountability surface for multi-grader work. esc or h
closes it.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ListItem, ListView, Static

from opengrader.core.history.journal import GradeJournalEntry
from opengrader.tui.formatting import format_history_row


class GradeHistoryScreen(ModalScreen[None]):
    """Read-only, newest-last list of recorded grade changes."""

    DEFAULT_CSS = """
    GradeHistoryScreen {
        align: center middle;
    }

    #history-dialog {
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

    #history-summary {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #history-list {
        height: 1fr;
        margin-top: 1;
        background: transparent;
        border: round $surface-lighten-2;
        scrollbar-size: 1 1;
    }

    GradeHistoryScreen .history-empty {
        color: $text-muted;
        padding: 1 2;
    }

    #history-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("h", "close", "Close"),
    ]

    def __init__(
        self,
        entries: list[GradeJournalEntry],
        *,
        teacher_mode: bool,
    ) -> None:
        # Newest first reads best for a "what just happened" audit view.
        self._entries = list(reversed(entries))
        self._teacher_mode = teacher_mode
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="history-dialog") as dialog:
            dialog.border_title = "▸ Grade History"
            yield Static(self._summary_text(), id="history-summary")
            yield ListView(id="history-list")
            with Horizontal(id="history-buttons"):
                yield Button("Close", id="history-close", compact=True)

    def on_mount(self) -> None:
        list_view = self.query_one("#history-list", ListView)
        if not self._entries:
            list_view.append(
                ListItem(
                    Label(
                        "No grade history recorded yet this assignment.",
                        classes="history-empty",
                    )
                )
            )
            list_view.can_focus = False
            self.query_one("#history-close", Button).focus()
            return
        for entry in self._entries:
            list_view.append(
                ListItem(
                    Label(format_history_row(entry, teacher_mode=self._teacher_mode))
                )
            )
        list_view.index = 0
        list_view.focus()

    def _summary_text(self) -> str:
        if not self._entries:
            return "The audit journal is empty for this submissions set."
        actors = sorted({e.actor for e in self._entries if e.actor})
        who = ", ".join(actors) if actors else "unknown"
        return (
            f"{len(self._entries)} change(s) by {len(actors)} grader(s): {who}.  "
            "esc closes."
        )

    @on(ListView.Selected)
    def _swallow_selection(self, event: ListView.Selected) -> None:
        # Read-only view: selecting a row does nothing but must not bubble.
        event.stop()

    @on(Button.Pressed, "#history-close")
    def _on_close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
