from __future__ import annotations

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Label, Static

from opengrader.tui.data.models import (
    AnswerPatternMember,
    AnswerPatternReportItem,
    AnswerPatternReportView,
    SimilarityReportItem,
    SimilarityReportMember,
)
from opengrader.tui.formatting import (
    format_answer_pattern_detail,
    format_answer_pattern_headline,
    format_answer_pattern_member,
    format_answer_pattern_scope,
    format_answer_pattern_shared,
    format_similarity_headline,
    format_similarity_member,
    format_similarity_pair,
)

_MAX_SHARED_ROWS = 6


class _SimilarityMemberRow(Static):
    can_focus = True

    def __init__(self, member, question_id: str, label) -> None:
        self._student_item_id = member.student_item_id
        self._question_id = question_id
        super().__init__(label, classes="similarity-member")

    def _select(self) -> None:
        self.post_message(
            SimilarityScreen.MemberSelected(self._student_item_id, self._question_id)
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


class SimilarityScreen(ModalScreen["tuple[str, str] | None"]):
    DEFAULT_CSS = """
    SimilarityScreen {
        align: center middle;
    }

    #similarity-dialog {
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

    #similarity-summary {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #similarity-body {
        height: 1fr;
        margin-top: 1;
        border: round $surface-lighten-2;
        padding: 0 1;
        scrollbar-size: 1 1;
    }

    SimilarityScreen .similarity-flag {
        width: 100%;
        height: auto;
        margin: 1 0 0 0;
    }

    SimilarityScreen .similarity-pair {
        width: 100%;
        height: auto;
        color: $text-muted;
        margin-left: 2;
    }

    SimilarityScreen .similarity-member {
        width: 100%;
        height: auto;
        margin-left: 4;
        color: $accent;
    }

    SimilarityScreen .similarity-member:hover {
        background: $boost;
    }

    SimilarityScreen .similarity-member:focus {
        background: $accent 20%;
        text-style: bold;
    }

    SimilarityScreen .similarity-member-inert {
        width: 100%;
        height: auto;
        margin-left: 4;
    }

    SimilarityScreen .similarity-empty {
        color: $text-muted;
        padding: 1 2;
    }

    SimilarityScreen .pattern-scope {
        width: 100%;
        height: auto;
        padding: 1 0 0 0;
    }

    SimilarityScreen .pattern-shared {
        width: 100%;
        height: auto;
        margin-left: 2;
        color: $text-muted;
    }

    #similarity-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("p", "close", "Close"),
        Binding("o", "toggle_view", "Objective answers", show=False),
    ]

    VIEW_RESPONSES = "responses"
    VIEW_OBJECTIVE = "objective"

    class MemberSelected(Message):
        def __init__(self, student_item_id: str, question_id: str) -> None:
            self.student_item_id = student_item_id
            self.question_id = question_id
            super().__init__()

    def __init__(
        self,
        items: list[SimilarityReportItem],
        *,
        teacher_mode: bool,
        patterns: AnswerPatternReportView | None = None,
    ) -> None:
        self._items = items
        self._teacher_mode = teacher_mode
        self._patterns = patterns
        self._view = self.VIEW_RESPONSES
        super().__init__()

    @property
    def _has_patterns(self) -> bool:
        return self._patterns is not None

    def compose(self) -> ComposeResult:
        with Vertical(id="similarity-dialog") as dialog:
            dialog.border_title = "▸ Answer Similarity"
            yield Static(self._summary_text(), id="similarity-summary")
            yield VerticalScroll(id="similarity-body")
            with Horizontal(id="similarity-buttons"):
                yield Button("Close", id="similarity-close", compact=True)

    def on_mount(self) -> None:
        self._render_view()

    def _focus_first_row(self) -> None:
        rows = list(self.query(_SimilarityMemberRow))
        if rows:
            rows[0].focus()
        else:
            self.query_one("#similarity-close", Button).focus()

    def focus_relative_member(self, current: _SimilarityMemberRow, delta: int) -> None:
        rows = list(self.query(_SimilarityMemberRow))
        if current not in rows:
            return
        target_index = rows.index(current) + delta
        if 0 <= target_index < len(rows):
            row = rows[target_index]
            row.focus()
            row.scroll_visible()


    def _render_view(self) -> None:
        self.query_one("#similarity-summary", Static).update(self._summary_text())
        dialog = self.query_one("#similarity-dialog", Vertical)
        dialog.border_title = (
            "▸ Answer Similarity — Objective Answers"
            if self._view == self.VIEW_OBJECTIVE
            else "▸ Answer Similarity"
        )
        body = self.query_one("#similarity-body", VerticalScroll)
        body.remove_children()
        body.mount_all(self._body_widgets())
        body.scroll_home(animate=False)
        self._focus_first_row()

    def _body_widgets(self) -> list[Widget]:
        if self._view == self.VIEW_OBJECTIVE:
            return self._objective_widgets()
        return self._response_widgets()

    def _response_widgets(self) -> list[Widget]:
        if not self._items:
            return [
                Label("No suspiciously-similar answers. ✓", classes="similarity-empty")
            ]
        return [self._flag_widget(item) for item in self._items]

    def _objective_widgets(self) -> list[Widget]:
        view = self._patterns
        assert view is not None
        widgets: list[Widget] = [
            Static(format_answer_pattern_scope(view), classes="pattern-scope")
        ]
        if not view.analyzed:
            widgets.append(
                Label(
                    "Nothing to compare — this assignment has no rule-graded question "
                    "with a spread of wrong answers.",
                    classes="similarity-empty",
                )
            )
            return widgets
        if not view.items:
            widgets.append(
                Label(
                    "No implausible objective answer patterns. ✓",
                    classes="similarity-empty",
                )
            )
            return widgets
        widgets.extend(self._pattern_widget(item) for item in view.items)
        return widgets

    def _flag_widget(self, item: SimilarityReportItem) -> Vertical:
        children: list[Widget] = [Label(format_similarity_headline(item))]
        children.extend(
            Label(
                format_similarity_pair(pair, teacher_mode=self._teacher_mode),
                classes="similarity-pair",
            )
            for pair in item.pairs
        )
        children.extend(
            self._member_row(
                member,
                item.question_id,
                format_similarity_member(member, teacher_mode=self._teacher_mode),
            )
            for member in item.members
        )
        return Vertical(*children, classes="similarity-flag")

    def _pattern_widget(self, item: AnswerPatternReportItem) -> Vertical:
        children: list[Widget] = [
            Label(format_answer_pattern_headline(item, teacher_mode=self._teacher_mode)),
            Label(format_answer_pattern_detail(item), classes="similarity-pair"),
        ]
        children.extend(
            Label(format_answer_pattern_shared(shared), classes="pattern-shared")
            for shared in item.shared_wrong[:_MAX_SHARED_ROWS]
        )
        remaining = item.shared_wrong_count - _MAX_SHARED_ROWS
        if remaining > 0:
            children.append(
                Label(
                    f"… and {remaining} more identical wrong answer(s)",
                    classes="pattern-shared",
                )
            )
        children.extend(
            self._member_row(
                member,
                item.first_question_id,
                format_answer_pattern_member(member, teacher_mode=self._teacher_mode),
            )
            for member in item.members
        )
        return Vertical(*children, classes="similarity-flag")

    def _member_row(
        self,
        member: SimilarityReportMember | AnswerPatternMember,
        question_id: str,
        label,
    ) -> Widget:
        if member.is_navigable:
            return _SimilarityMemberRow(member, question_id, label)
        return Label(label, classes="similarity-member-inert")

    def _summary_text(self) -> str:
        if self._view == self.VIEW_OBJECTIVE:
            return self._objective_summary_text()
        if not self._items:
            headline = (
                "No suspiciously-similar answers — every response looks independent."
            )
        else:
            high = sum(1 for item in self._items if item.severity == "high")
            medium = len(self._items) - high
            headline = (
                f"{len(self._items)} similar-answer cluster(s): "
                f"{high} near-verbatim, {medium} strongly similar."
            )
        return f"{headline}  {self._nav_hint('o shows objective answers.')}"

    def _objective_summary_text(self) -> str:
        view = self._patterns
        assert view is not None
        if not view.items:
            headline = "Objective answers: nothing stands out."
        else:
            high = sum(1 for item in view.items if item.severity == "high")
            medium = len(view.items) - high
            headline = (
                f"{len(view.items)} objective answer-pattern flag(s): "
                f"{high} extreme, {medium} notable."
            )
        return f"{headline}  {self._nav_hint('o shows free responses.')}"

    def _nav_hint(self, toggle_hint: str) -> str:
        parts = ["↑↓/jk move", "enter opens the student"]
        if self._has_patterns:
            parts.append(toggle_hint.rstrip("."))
        parts.append("esc closes")
        return " · ".join(parts) + "."


    def action_toggle_view(self) -> None:
        if not self._has_patterns:
            return
        self._view = (
            self.VIEW_OBJECTIVE
            if self._view == self.VIEW_RESPONSES
            else self.VIEW_RESPONSES
        )
        self._render_view()

    @on(MemberSelected)
    def _on_member_selected(self, event: MemberSelected) -> None:
        event.stop()
        self.dismiss((event.student_item_id, event.question_id))

    @on(Button.Pressed, "#similarity-close")
    def _on_close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
