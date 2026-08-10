"""Modal regrade-request queue, opened with Shift+R.

A pop-up that turns student grade disputes into a worked list. Each open request
(filed via the ``regrade`` CLI or a future student portal, stored in
``.opengrader/regrades.jsonl``) is one actionable row showing who is disputing
which question and why. From a focused row a grader can:

* **open** the disputed question on the grade screen (enter / click) — the same
  routing the needs-review queue uses (`GradeController.open_review_target`), so
  they land exactly where the fix is needed;
* **resolve** it (``r``) once the grade has been reviewed/adjusted;
* **deny** it (``d``) when the grade stands.

Resolve/deny dismiss with the chosen action; ``TuiApp`` performs it (persisting
to the append-only log) and reopens the refreshed queue so triage continues —
mirroring how `AssignIdentityScreen` resolves an identity flag. Anonymity-aware:
names show only in teacher mode. esc or Shift+R closes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from opengrader.tui.data.models import RegradeReportItem
from opengrader.tui.formatting import format_regrade_headline, format_regrade_reason

RegradeActionKind = Literal["open", "resolve", "deny"]


@dataclass(frozen=True)
class RegradeChoice:
    """What the grader chose to do with a dispute, returned on dismiss."""

    action: RegradeActionKind
    request_id: str
    student_item_id: str
    question_id: str


class _RegradeRow(Static):
    """A focusable dispute row: enter opens it, r resolves, d denies."""

    can_focus = True

    def __init__(self, item: RegradeReportItem, label) -> None:
        self._item = item
        super().__init__(label, classes="regrade-headline")

    def _post(self, action: RegradeActionKind) -> None:
        # "open" is only meaningful for a navigable dispute (mapped submission).
        if action == "open" and not self._item.is_navigable:
            return
        self.post_message(
            RegradeQueueScreen.ActionChosen(
                RegradeChoice(
                    action=action,
                    request_id=self._item.request_id,
                    student_item_id=self._item.student_item_id,
                    question_id=self._item.question_id,
                )
            )
        )

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self._post("open")

    def on_key(self, event: events.Key) -> None:
        # The focused row is first in the bubble path, so intercepting the nav
        # keys here (and stopping them) keeps the enclosing VerticalScroll from
        # hijacking up/down to scroll instead of moving between disputes.
        if event.key in ("enter", "space"):
            event.stop()
            self._post("open")
        elif event.key == "r":
            event.stop()
            self._post("resolve")
        elif event.key == "d":
            event.stop()
            self._post("deny")
        elif event.key in ("down", "j"):
            event.stop()
            self.screen.focus_relative_row(self, 1)
        elif event.key in ("up", "k"):
            event.stop()
            self.screen.focus_relative_row(self, -1)


class RegradeQueueScreen(ModalScreen["RegradeChoice | None"]):
    """Worked list of open regrade requests (grade disputes).

    Dismisses with a :class:`RegradeChoice` when the grader acts on a dispute,
    or ``None`` on close.
    """

    DEFAULT_CSS = """
    RegradeQueueScreen {
        align: center middle;
    }

    #regrade-dialog {
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

    #regrade-summary {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #regrade-body {
        height: 1fr;
        margin-top: 1;
        border: round $surface-lighten-2;
        padding: 0 1;
        scrollbar-size: 1 1;
    }

    RegradeQueueScreen .regrade-flag {
        width: 100%;
        height: auto;
        margin: 1 0 0 0;
    }

    RegradeQueueScreen .regrade-headline {
        width: 100%;
        height: auto;
        color: $accent;
    }

    RegradeQueueScreen .regrade-headline:hover {
        background: $boost;
    }

    RegradeQueueScreen .regrade-headline:focus {
        background: $accent 20%;
        text-style: bold;
    }

    RegradeQueueScreen .regrade-reason {
        width: 100%;
        height: auto;
        color: $text-muted;
        margin-left: 2;
    }

    RegradeQueueScreen .regrade-empty {
        color: $text-muted;
        padding: 1 2;
    }

    #regrade-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("R", "close", "Close"),
    ]

    class ActionChosen(Message):
        """Posted when the grader opens/resolves/denies a dispute."""

        def __init__(self, choice: RegradeChoice) -> None:
            self.choice = choice
            super().__init__()

    def __init__(self, items: list[RegradeReportItem], *, teacher_mode: bool) -> None:
        self._items = items
        self._teacher_mode = teacher_mode
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="regrade-dialog") as dialog:
            dialog.border_title = "▸ Regrade Requests"
            yield Static(self._summary_text(), id="regrade-summary")
            with VerticalScroll(id="regrade-body"):
                if not self._items:
                    yield Label(
                        "No open regrade requests. ✓",
                        classes="regrade-empty",
                    )
                for item in self._items:
                    yield from self._flag_block(item)
            with Horizontal(id="regrade-buttons"):
                yield Button("Close", id="regrade-close", compact=True)

    def on_mount(self) -> None:
        # Land on the first dispute so the keyboard actions work immediately;
        # fall back to the close button when the queue is empty (Textual's
        # `.first()` raises on an empty query, so iterate instead).
        rows = list(self.query(_RegradeRow))
        if rows:
            rows[0].focus()
        else:
            self.query_one("#regrade-close", Button).focus()

    def focus_relative_row(self, current: _RegradeRow, delta: int) -> None:
        """Move focus to the next/previous dispute row (arrow / j·k keys)."""
        rows = list(self.query(_RegradeRow))
        if current not in rows:
            return
        target_index = rows.index(current) + delta
        if 0 <= target_index < len(rows):
            row = rows[target_index]
            row.focus()
            row.scroll_visible()

    def _flag_block(self, item: RegradeReportItem):
        with Vertical(classes="regrade-flag"):
            yield _RegradeRow(
                item, format_regrade_headline(item, teacher_mode=self._teacher_mode)
            )
            yield Label(format_regrade_reason(item), classes="regrade-reason")

    def _summary_text(self) -> str:
        if not self._items:
            return "No open regrade requests — no grades are being disputed."
        count = len(self._items)
        label = "dispute" if count == 1 else "disputes"
        return (
            f"{count} open regrade {label}.  "
            "↑↓/jk move · enter opens the question · r resolves · d denies · esc closes."
        )

    @on(ActionChosen)
    def _on_action_chosen(self, event: ActionChosen) -> None:
        event.stop()
        self.dismiss(event.choice)

    @on(Button.Pressed, "#regrade-close")
    def _on_close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
