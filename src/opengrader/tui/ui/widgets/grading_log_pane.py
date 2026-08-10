from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import Label, ListItem

from opengrader.tui.formatting import format_grading_log_row
from opengrader.tui.state.grading_log import GradingLog, GradingLogEntry

from .tabbed_list_pane import IndexedListView, TabbedListPane


class _GradingLogEntryItem(ListItem):
    """One log row; clicking it reopens that grade on the grade screen."""

    def __init__(self, entry: GradingLogEntry, *, teacher_mode: bool) -> None:
        self.entry = entry
        super().__init__(
            Label(format_grading_log_row(entry, teacher_mode=teacher_mode)),
            classes="grading-log-entry",
        )

    def on_click(self, event: events.Click) -> None:
        self.post_message(GradingLogPane.EntrySelected(self.entry))
        event.stop()


class GradingLogPane(TabbedListPane):
    """Session-activity feed styled like the Students pane.

    Three tabs slice the same log: Latest (everything, newest first) and the
    teacher-mode-only Student/Question tabs, which follow whatever the picker
    currently highlights. The lists never take keyboard focus — tabs are
    switched by clicking their labels in the border title. Rows themselves
    are clickable: each posts :class:`EntrySelected` so the app can jump to
    that entry's question/student on the grade screen.
    """

    class EntrySelected(Message):
        """Posted when the user clicks a log row to revisit that grade."""

        def __init__(self, entry: GradingLogEntry) -> None:
            self.entry = entry
            super().__init__()

    TAB_LATEST = 0
    TAB_STUDENT = 1
    TAB_QUESTION = 2

    # Bound the widget cost per refresh; the log itself keeps more history.
    _MAX_ROWS = 50

    def __init__(self, *, id: str | None = None) -> None:
        self._tab_counts: dict[int, int] = {}
        self._render_keys: dict[int, tuple[object, ...]] = {}
        self._latest_list = IndexedListView(id="grading-log-latest")
        self._student_list = IndexedListView(id="grading-log-student")
        self._question_list = IndexedListView(id="grading-log-question")
        for list_view in (
            self._latest_list,
            self._student_list,
            self._question_list,
        ):
            list_view.can_focus = False
        super().__init__(
            "Grading Log",
            tabs=[
                ("Latest", self._latest_list),
                ("Student", self._student_list),
                ("Question", self._question_list),
            ],
            id=id,
        )

    def set_teacher_tabs_visible(self, visible: bool) -> None:
        """Anonymous grading only exposes the Latest tab."""
        self.set_tab_hidden(self.TAB_STUDENT, not visible)
        self.set_tab_hidden(self.TAB_QUESTION, not visible)

    def update_log(
        self,
        log: GradingLog,
        *,
        teacher_mode: bool,
        student_item_id: str | None,
        question_id: str | None,
    ) -> None:
        self.set_teacher_tabs_visible(teacher_mode)
        self._populate(
            self.TAB_LATEST,
            self._latest_list,
            (log.version, teacher_mode),
            log.entries_latest_first(),
            teacher_mode=teacher_mode,
            empty_text="No grading activity yet this session.",
        )
        self._populate(
            self.TAB_STUDENT,
            self._student_list,
            (log.version, teacher_mode, student_item_id),
            log.entries_for_student(student_item_id) if student_item_id else [],
            teacher_mode=teacher_mode,
            empty_text="No activity for the highlighted student.",
        )
        self._populate(
            self.TAB_QUESTION,
            self._question_list,
            (log.version, teacher_mode, question_id),
            log.entries_for_question(question_id) if question_id else [],
            teacher_mode=teacher_mode,
            empty_text="No activity for the highlighted question.",
        )
        self.refresh_index_subtitle()

    def refresh_index_subtitle(self) -> None:
        """Show the active tab's entry count instead of a cursor position."""
        count = self._tab_counts.get(self.active_tab_index, 0)
        self.set_subtitle(f"{count} entr{'y' if count == 1 else 'ies'}")

    def _populate(
        self,
        tab_index: int,
        list_view: IndexedListView,
        render_key: tuple[object, ...],
        entries: list[GradingLogEntry],
        *,
        teacher_mode: bool,
        empty_text: str,
    ) -> None:
        if self._render_keys.get(tab_index) == render_key:
            return
        self._render_keys[tab_index] = render_key
        self._tab_counts[tab_index] = len(entries)

        list_view.clear()
        if not entries:
            list_view.append(
                ListItem(Label(empty_text, classes="grading-log-empty"))
            )
            return
        for entry in entries[: self._MAX_ROWS]:
            list_view.append(_GradingLogEntryItem(entry, teacher_mode=teacher_mode))
