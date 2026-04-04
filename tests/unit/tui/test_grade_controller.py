from dataclasses import dataclass

from opengrader.tui.controllers.grade_controller import GradeController
from opengrader.tui.state import PAGE_HOME, PAGE_GRADE, TuiWorkflowState
from opengrader.tui.widgets.nav_item import NavItem

from .fakes import FakeDataProvider


@dataclass
class FakeHomeScreen:
    status: str = ""
    focused: bool = False

    def set_status(self, text: str) -> None:
        self.status = text

    def focus_courses(self) -> bool:
        self.focused = True
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
        self.home_screen = FakeHomeScreen()
        self.grade_screen = FakeGradeScreen()
        self.page = PAGE_HOME

    def get_home_screen(self) -> FakeHomeScreen:
        return self.home_screen

    def get_grade_screen(self) -> FakeGradeScreen:
        return self.grade_screen

    def switch_to(self, page_id: str) -> None:
        self.page = page_id


def build_controller() -> tuple[FakeApp, TuiWorkflowState, GradeController]:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    state.student_items = [
        NavItem("s1", "Tommy Liu"),
        NavItem("s2", "Andy Cortes"),
    ]
    controller = GradeController(app, state, FakeDataProvider())
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


def test_finalize_last_student_returns_to_home() -> None:
    app, state, controller = build_controller()

    controller.open_student_by_index(1)
    state.current_page = PAGE_GRADE
    controller.finalize_student()

    assert app.page == PAGE_HOME
    assert app.home_screen.status == "All students finalized for this assignment."
    assert app.home_screen.focused is True
