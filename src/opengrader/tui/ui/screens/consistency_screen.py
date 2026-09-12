from __future__ import annotations

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from opengrader.tui.data.models import ConsistencyMember, ConsistencyReportItem
from opengrader.tui.formatting import (
    format_consistency_answer,
    format_consistency_headline,
    format_consistency_member,
    format_consistency_variant_grade,
)


class _ConsistencyMemberRow(Static):
    can_focus = True

    def __init__(self, member: ConsistencyMember, question_id: str, label) -> None:
        self._student_item_id = member.student_item_id
        self._question_id = question_id
        super().__init__(label, classes="consistency-member")

    def _select(self) -> None:
        self.post_message(
            ConsistencyScreen.MemberSelected(self._student_item_id, self._question_id)
        )

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self._select()

    def on_key(self, event: events.Key) -> None:
        if event.key in ("enter", "space"):
            event.stop()
            self._select()
        elif event.key in ("down", "j"):
            event.stop()
            self.screen.focus_relative_member(self, 1)
        elif event.key in ("up", "k"):
            event.stop()
            self.screen.focus_relative_member(self, -1)


class ConsistencyScreen(ModalScreen["tuple[str, str] | None"]):
    DEFAULT_CSS = """
    ConsistencyScreen {
        align: center middle;
    }

    #consistency-dialog {
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

    #consistency-summary {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #consistency-body {
        height: 1fr;
        margin-top: 1;
        border: round $surface-lighten-2;
        padding: 0 1;
        scrollbar-size: 1 1;
    }

    ConsistencyScreen .consistency-flag {
        width: 100%;
        height: auto;
        margin: 1 0 0 0;
    }

    ConsistencyScreen .consistency-answer {
        width: 100%;
        height: auto;
        color: $text-muted;
        margin-left: 2;
    }

    ConsistencyScreen .consistency-grade {
        width: 100%;
        height: auto;
        margin: 0 0 0 2;
    }

    ConsistencyScreen .consistency-member {
        width: 100%;
        height: auto;
        margin-left: 4;
        color: $accent;
    }

    ConsistencyScreen .consistency-member:hover {
        background: $boost;
    }

    ConsistencyScreen .consistency-member:focus {
        background: $accent 20%;
        text-style: bold;
    }

    ConsistencyScreen .consistency-member-inert {
        width: 100%;
        height: auto;
        margin-left: 4;
    }

    ConsistencyScreen .consistency-empty {
        color: $text-muted;
        padding: 1 2;
    }

    #consistency-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("i", "close", "Close"),
    ]

    class MemberSelected(Message):
        def __init__(self, student_item_id: str, question_id: str) -> None:
            self.student_item_id = student_item_id
            self.question_id = question_id
            super().__init__()

    def __init__(self, items: list[ConsistencyReportItem], *, teacher_mode: bool) -> None:
        self._items = items
        self._teacher_mode = teacher_mode
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="consistency-dialog") as dialog:
            dialog.border_title = "▸ Consistency Audit"
            yield Static(self._summary_text(), id="consistency-summary")
            with VerticalScroll(id="consistency-body"):
                if not self._items:
                    yield Label(
                        "Every shared answer was graded the same. ✓",
                        classes="consistency-empty",
                    )
                for item in self._items:
                    yield from self._flag_block(item)
            with Horizontal(id="consistency-buttons"):
                yield Button("Close", id="consistency-close", compact=True)

    def on_mount(self) -> None:
        rows = list(self.query(_ConsistencyMemberRow))
        if rows:
            rows[0].focus()
        else:
            self.query_one("#consistency-close", Button).focus()

    def focus_relative_member(self, current: _ConsistencyMemberRow, delta: int) -> None:
        rows = list(self.query(_ConsistencyMemberRow))
        if current not in rows:
            return
        target_index = rows.index(current) + delta
        if 0 <= target_index < len(rows):
            row = rows[target_index]
            row.focus()
            row.scroll_visible()

    def _flag_block(self, item: ConsistencyReportItem):
        with Vertical(classes="consistency-flag"):
            yield Label(format_consistency_headline(item))
            yield Label(format_consistency_answer(item), classes="consistency-answer")
            for variant in item.variants:
                yield Label(
                    format_consistency_variant_grade(variant),
                    classes="consistency-grade",
                )
                for member in variant.members:
                    label = format_consistency_member(member, teacher_mode=self._teacher_mode)
                    if member.is_navigable:
                        yield _ConsistencyMemberRow(member, item.question_id, label)
                    else:
                        yield Label(label, classes="consistency-member-inert")

    def _summary_text(self) -> str:
        if not self._items:
            return "No grading inconsistencies — every shared answer scored the same."
        scoring = sum(1 for item in self._items if item.severity == "high")
        rubric = len(self._items) - scoring
        return (
            f"{len(self._items)} inconsistent answer group(s): "
            f"{scoring} scoring, {rubric} rubric-only.  "
            "↑↓/jk move · enter opens the student · esc closes."
        )

    @on(MemberSelected)
    def _on_member_selected(self, event: MemberSelected) -> None:
        event.stop()
        self.dismiss((event.student_item_id, event.question_id))

    @on(Button.Pressed, "#consistency-close")
    def _on_close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
