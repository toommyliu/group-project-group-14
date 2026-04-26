from dataclasses import dataclass

from opengrader.tui.controllers.grade_controller import GradeController
from opengrader.tui.state import PAGE_GRADE, PAGE_QA_PICKER, TuiWorkflowState
from opengrader.tui.ui.widgets.nav_item import NavItem

from .fakes import FakeDataProvider


@dataclass
class FakeGradeScreen:
    status: str = ""
    bound_student_name: str = ""
    bound_labels: list[str] | None = None
    progress: str = ""

    def bind_run(self, run, breadcrumb_labels: list[str]) -> None:
        self.bound_student_name = run.student_name
        self.bound_labels = breadcrumb_labels

    def set_status(self, text: str) -> None:
        self.status = text

    def set_progress(self, text: str) -> None:
        self.progress = text


@dataclass
class FakeQAPickerScreen:
    status: str = ""
    bound_questions: list[NavItem] | None = None
    focused: bool = False
    moved_to_index: int | None = None

    def bind_assignment(
        self,
        questions: list[NavItem],
        *,
        breadcrumb_labels: list[str],
        assignment_name: str,
    ) -> None:
        _ = (breadcrumb_labels, assignment_name)
        self.bound_questions = questions

    def set_status(self, text: str) -> None:
        self.status = text

    def focus_questions(self) -> None:
        self.focused = True

    def show_question_details(self, item: NavItem) -> None:
        _ = item

    def move_cursor(self, direction: int) -> None:
        _ = direction

    def move_to_index(self, index: int) -> None:
        self.moved_to_index = index

    def selected_question(self) -> NavItem | None:
        if not self.bound_questions:
            return None
        return self.bound_questions[0]


class FakeApp:
    def __init__(self) -> None:
        self.grade_screen = FakeGradeScreen()
        self.qa_screen = FakeQAPickerScreen()
        self.page = PAGE_QA_PICKER

    def get_grade_screen(self) -> FakeGradeScreen:
        return self.grade_screen

    def get_qa_picker_screen(self) -> FakeQAPickerScreen:
        return self.qa_screen

    def switch_to(self, page_id: str) -> None:
        self.page = page_id


def build_controller() -> tuple[FakeApp, TuiWorkflowState, GradeController]:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    controller = GradeController(app, state, FakeDataProvider())
    controller.attach_student_refresh(lambda: None)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    return app, state, controller


def test_open_question_picker_binds_questions_and_focuses_picker() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"

    controller = GradeController(app, state, FakeDataProvider())
    controller.open_question_picker_for_assignment()

    assert app.page == PAGE_QA_PICKER
    assert [item.item_id for item in state.question_items] == ["Q1", "Q2"]
    assert app.qa_screen.bound_questions is not None
    assert app.qa_screen.focused is True


def test_finalize_student_marks_status_and_advances_to_next_student() -> None:
    app, state, controller = build_controller()
    state.current_page = PAGE_GRADE

    refreshed: list[str] = []
    controller.attach_student_refresh(lambda: refreshed.append("called"))
    controller.finalize_student()

    assert refreshed == ["called"]
    assert state.student_status[("1", "1", "s1")] == "graded"
    assert state.active_student_index == 1
    assert state.selection_labels["student"] == "Andy Cortes"
    assert app.grade_screen.status == "QA finalized. Loaded next ungraded student."
    assert app.grade_screen.progress == "Progress: 1 / 2 students"


def test_next_previous_ungraded_student_skip_graded_for_selected_qa() -> None:
    _app, state, controller = build_controller()
    state.current_page = PAGE_GRADE
    state.qa_student_status[("1", "1", "Q1", "s1")] = "graded"

    controller.open_student_by_index(0)
    controller.next_ungraded_student()

    assert state.active_student_index == 1
    assert state.selection_labels["student"] == "Andy Cortes"

    state.qa_student_status[("1", "1", "Q1", "s2")] = "graded"
    controller.previous_ungraded_student()

    assert state.active_student_index == 1


def test_finalize_last_student_returns_to_qa_picker() -> None:
    app, state, controller = build_controller()

    controller.open_student_by_index(1)
    state.current_page = PAGE_GRADE
    controller.finalize_student()

    assert app.page == PAGE_QA_PICKER
    assert app.qa_screen.status == "All students finalized for this QA."
    assert app.qa_screen.focused is True


def test_return_to_home_alias_returns_to_qa_picker() -> None:
    app, state, controller = build_controller()
    state.current_page = PAGE_GRADE

    controller.return_to_home()

    assert app.page == PAGE_QA_PICKER
    assert app.qa_screen.focused is True
