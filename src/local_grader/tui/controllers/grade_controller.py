from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from local_grader.tui.screens import BrowseScreen, GradeScreen
from local_grader.tui.services.sample_data import SampleDataService
from local_grader.tui.state import PAGE_BROWSE, PAGE_GRADE, TuiWorkflowState
from local_grader.tui.widgets.miller_column import MillerItem


class GradeApp(Protocol):
    def get_browse_screen(self) -> BrowseScreen:
        ...

    def get_grade_screen(self) -> GradeScreen:
        ...

    def switch_to(self, page_id: str) -> None:
        ...


class GradeController:
    def __init__(
        self,
        app: GradeApp,
        state: TuiWorkflowState,
        data_service: SampleDataService,
    ) -> None:
        self._app = app
        self._state = state
        self._data_service = data_service
        self._refresh_students: Callable[[], None] | None = None

    def attach_student_refresh(self, callback: Callable[[], None]) -> None:
        self._refresh_students = callback

    def open_student_by_index(self, index: int) -> None:
        if index < 0 or index >= len(self._state.student_items):
            return

        self._state.active_student_index = index
        student_item = self._state.student_items[index]
        self._state.selection.student_id = self._numeric_id(student_item.item_id)
        self._state.selection_labels["student"] = student_item.label

        self._state.grading_run.reset(
            student_id=self._state.selection.student_id,
            student_name=student_item.label,
            assignment_name=self._state.selection_labels.get("assignment")
            or "Assignment",
        )
        self._state.grading_run.max_points = 100.0
        self._state.grading_run.questions = self._data_service.build_question_grades()

        self.refresh_grade_screen(status="")
        self._app.switch_to(PAGE_GRADE)

    def on_question_selected(self, question_index: int) -> None:
        self._state.grading_run.select_question(question_index)
        self.refresh_grade_screen(status="")

    def on_rubric_selected(self, option_index: int) -> None:
        self._state.grading_run.apply_option(option_index)
        self.refresh_grade_screen(status="")

    def on_comment_updated(self, comment: str) -> None:
        self._state.grading_run.update_comment(comment)

    def return_to_browse(self) -> None:
        self._app.switch_to(PAGE_BROWSE)
        self._app.get_browse_screen().focus_column("students")

    def next_question(self) -> None:
        if not self._on_grade_page():
            return
        self._state.grading_run.next_question()
        self.refresh_grade_screen(status="")

    def previous_question(self) -> None:
        if not self._on_grade_page():
            return
        self._state.grading_run.previous_question()
        self.refresh_grade_screen(status="")

    def next_student(self) -> None:
        if not self._on_grade_page():
            return
        next_index = self._state.active_student_index + 1
        if next_index >= len(self._state.student_items):
            self.refresh_grade_screen(status="Already on last student.")
            return
        self.open_student_by_index(next_index)

    def previous_student(self) -> None:
        if not self._on_grade_page():
            return
        previous_index = self._state.active_student_index - 1
        if previous_index < 0:
            self.refresh_grade_screen(status="Already on first student.")
            return
        self.open_student_by_index(previous_index)

    def finalize_student(self) -> None:
        if not self._on_grade_page():
            return
        current = self._current_student_item()
        if current is None:
            return

        self._state.student_status[self._status_key(current.item_id)] = "graded"
        if self._refresh_students is not None:
            self._refresh_students()

        next_index = self._state.active_student_index + 1
        if next_index < len(self._state.student_items):
            self.open_student_by_index(next_index)
            self.refresh_grade_screen(status="Student finalized. Loaded next student.")
            return

        self._app.switch_to(PAGE_BROWSE)
        browse_screen = self._app.get_browse_screen()
        browse_screen.set_status("All students finalized for this assignment.")
        browse_screen.focus_column("students")

    def refresh_grade_screen(self, status: str) -> None:
        grade_screen = self._app.get_grade_screen()
        grade_screen.bind_run(
            self._state.grading_run,
            breadcrumb_labels=self._state.breadcrumb_labels(),
        )
        grade_screen.set_status(status)

    def _current_student_item(self) -> MillerItem | None:
        if self._state.active_student_index < 0:
            return None
        if self._state.active_student_index >= len(self._state.student_items):
            return None
        return self._state.student_items[self._state.active_student_index]

    def _status_key(self, student_item_id: str) -> tuple[str, str, str]:
        return (
            str(self._state.selection.course_id or ""),
            str(self._state.selection.assessment_id or ""),
            student_item_id,
        )

    def _on_grade_page(self) -> bool:
        return self._state.current_page == PAGE_GRADE

    @staticmethod
    def _numeric_id(raw_id: str) -> int | None:
        digits = "".join(ch for ch in raw_id if ch.isdigit())
        if not digits:
            return None
        return int(digits)
