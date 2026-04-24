from opengrader.tui.controllers.grade_controller import GradeController
from opengrader.tui.state import PAGE_QA_PICKER, TuiWorkflowState

from .test_grade_controller import FakeApp
from .fakes import FakeDataProvider


def test_jump_ungraded_question_moves_to_first_incomplete_question() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"

    controller = GradeController(app, state, FakeDataProvider())
    controller.open_question_picker_for_assignment()
    state.current_page = PAGE_QA_PICKER

    for student in state.student_items:
        state.qa_student_status[("1", "1", "Q1", student.item_id)] = "graded"

    controller.qa_jump_ungraded_question()

    assert app.qa_screen.moved_to_index == 1
    assert "Jumped to Q2" in app.qa_screen.status
