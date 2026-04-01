from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from local_grader.tui.screens import BrowseScreen
from local_grader.tui.services.sample_data import SampleDataService
from local_grader.tui.state import TuiWorkflowState
from local_grader.tui.widgets.miller_column import MillerItem


class BrowseApp(Protocol):
    def get_browse_screen(self) -> BrowseScreen:
        ...


class BrowseController:
    def __init__(
        self,
        app: BrowseApp,
        state: TuiWorkflowState,
        data_service: SampleDataService,
        open_student: Callable[[int], None],
    ) -> None:
        self._app = app
        self._state = state
        self._data_service = data_service
        self._open_student = open_student

    def initialize(self) -> None:
        browse_screen = self._app.get_browse_screen()
        browse_screen.set_courses(
            self._data_service.load_courses(),
            highlight_first=True,
        )
        browse_screen.set_assignments([])
        browse_screen.set_students([])
        browse_screen.set_status(
            "Use arrows to browse columns and Enter to select."
        )
        browse_screen.focus_column("courses")

    def handle_item_selected(self, column_id: str, item: MillerItem) -> None:
        browse_screen = self._app.get_browse_screen()

        if column_id == "courses":
            self._state.selection.course_id = self._numeric_id(item.item_id)
            self._state.selection.assessment_id = None
            self._state.selection.student_id = None
            self._state.active_student_index = -1
            self._state.selection_labels["course"] = item.label
            self._state.selection_labels["assignment"] = ""
            self._state.selection_labels["student"] = ""

            assignments = self._data_service.load_assignments(
                self._state.selection.course_id
            )
            browse_screen.set_assignments(assignments, highlight_first=True)
            browse_screen.set_students([])
            browse_screen.set_status(f"Course: {item.label}")
            if assignments:
                browse_screen.focus_column("assignments")
            else:
                browse_screen.focus_column("courses")
            return

        if column_id == "assignments":
            self._state.selection.assessment_id = self._numeric_id(item.item_id)
            self._state.selection.student_id = None
            self._state.active_student_index = -1
            self._state.selection_labels["assignment"] = item.label
            self._state.selection_labels["student"] = ""

            self._state.student_items = self._data_service.load_students(
                self._student_status_for
            )
            browse_screen.set_students(self._state.student_items, highlight_first=True)
            browse_screen.set_status(f"Assignment: {item.label}")
            if self._state.student_items:
                browse_screen.focus_column("students")
            else:
                browse_screen.focus_column("assignments")
            return

        if column_id == "students":
            self._state.selection.student_id = self._numeric_id(item.item_id)
            self._state.selection_labels["student"] = item.label
            student_index = self._student_index(item.item_id)
            if student_index is not None:
                self._open_student(student_index)

    def move_up(self) -> None:
        self._ensure_focus()
        self._app.get_browse_screen().move_cursor_up()

    def move_down(self) -> None:
        self._ensure_focus()
        self._app.get_browse_screen().move_cursor_down()

    def move_left(self) -> None:
        browse_screen = self._app.get_browse_screen()
        focused_column_id = browse_screen.focused_column_id()
        if focused_column_id is None:
            self._ensure_focus()
            return
        browse_screen.focus_previous_populated_column(focused_column_id)

    def move_right(self) -> None:
        browse_screen = self._app.get_browse_screen()
        focused_column_id = browse_screen.focused_column_id()
        if focused_column_id is None:
            self._ensure_focus()
            return
        browse_screen.focus_next_populated_column(focused_column_id)

    def confirm(self) -> None:
        browse_screen = self._app.get_browse_screen()
        focused_column_id = browse_screen.focused_column_id()
        if focused_column_id is None:
            self._ensure_focus()
            return
        item = browse_screen.highlighted_item(focused_column_id)
        if item is None:
            return
        self.handle_item_selected(focused_column_id, item)

    def refresh_students(self) -> None:
        if not self._state.student_items:
            return
        self._state.student_items = self._data_service.load_students(
            self._student_status_for
        )
        browse_screen = self._app.get_browse_screen()
        browse_screen.set_students(self._state.student_items, highlight_first=True)

    def _ensure_focus(self) -> None:
        browse_screen = self._app.get_browse_screen()
        if browse_screen.focused_column_id() is not None:
            return
        browse_screen.focus_first_populated_column()

    def _status_key(self, student_item_id: str) -> tuple[str, str, str]:
        return (
            str(self._state.selection.course_id or ""),
            str(self._state.selection.assessment_id or ""),
            student_item_id,
        )

    def _student_status_for(self, student_item_id: str) -> str:
        return self._state.student_status.get(
            self._status_key(student_item_id),
            "ungraded",
        )

    def _student_index(self, item_id: str) -> int | None:
        for index, item in enumerate(self._state.student_items):
            if item.item_id == item_id:
                return index
        return None

    @staticmethod
    def _numeric_id(raw_id: str) -> int | None:
        digits = "".join(ch for ch in raw_id if ch.isdigit())
        if not digits:
            return None
        return int(digits)
