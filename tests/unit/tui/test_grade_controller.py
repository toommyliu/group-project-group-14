from dataclasses import dataclass

from local_grader.tui.controllers.grade_controller import GradeController
from local_grader.tui.state import PAGE_BROWSE, PAGE_GRADE, TuiWorkflowState
from local_grader.tui.widgets.miller_column import MillerItem

from .fakes import FakeSampleDataService


@dataclass
class FakeBrowseScreen:
    status: str = ""
    focused_column: str | None = None

    def set_status(self, text: str) -> None:
        self.status = text

    def focus_column(self, column_id: str) -> bool:
        self.focused_column = column_id
        return True


@dataclass
class FakeGradeScreen:
    status: str = ""
    bound_student_name: str = ""
    bound_labels: list[str] | None = None

    def bind_run(self, run, breadcrumb_labels: list[str]) -> None:
        self.bound_student_name = run.student_name
        self.bound_labels = breadcrumb_labels

    def set_status(self, text: str) -> None:
        self.status = text


class FakeApp:
    def __init__(self) -> None:
        self.browse_screen = FakeBrowseScreen()
        self.grade_screen = FakeGradeScreen()
        self.page = PAGE_BROWSE

    def get_browse_screen(self) -> FakeBrowseScreen:
        return self.browse_screen

    def get_grade_screen(self) -> FakeGradeScreen:
        return self.grade_screen

    def switch_to(self, page_id: str) -> None:
        self.page = page_id


def build_controller() -> tuple[FakeApp, TuiWorkflowState, GradeController]:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assessment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    state.student_items = [
        MillerItem("s1", "Tommy Liu"),
        MillerItem("s2", "Andy Cortes"),
    ]
    controller = GradeController(app, state, FakeSampleDataService())
    controller.attach_student_refresh(lambda: None)
    return app, state, controller


def test_question_navigation_clamps_to_bounds() -> None:
    app, state, controller = build_controller()

    controller.open_student_by_index(0)
    state.current_page = PAGE_GRADE

    controller.next_question()
    controller.next_question()
    controller.previous_question()
    controller.previous_question()
    controller.previous_question()

    assert app.page == PAGE_GRADE
    assert state.grading_run.current_question_index == 0


def test_finalize_student_marks_status_and_advances_to_next_student() -> None:
    app, state, controller = build_controller()

    controller.open_student_by_index(0)
    state.current_page = PAGE_GRADE

    refreshed: list[str] = []
    controller.attach_student_refresh(lambda: refreshed.append("called"))
    controller.finalize_student()

    assert refreshed == ["called"]
    assert state.student_status[("1", "1", "s1")] == "graded"
    assert state.active_student_index == 1
    assert state.selection_labels["student"] == "Andy Cortes"
    assert app.grade_screen.status == "Student finalized. Loaded next student."


def test_finalize_last_student_returns_to_browse() -> None:
    app, state, controller = build_controller()

    controller.open_student_by_index(1)
    state.current_page = PAGE_GRADE
    controller.finalize_student()

    assert app.page == PAGE_BROWSE
    assert app.browse_screen.status == "All students finalized for this assignment."
    assert app.browse_screen.focused_column == "students"
