from dataclasses import dataclass, replace

from opengrader.tui.controllers.grade_controller import GradeController
from opengrader.tui.data import RubricEditResult, RubricOptionSeed
from opengrader.tui.state import PAGE_GRADE, PAGE_QA_PICKER, TuiWorkflowState
from opengrader.tui.ui.widgets.nav_item import NavItem

from .fakes import FakeDataProvider


class PersistentFakeDataProvider(FakeDataProvider):
    def __init__(self) -> None:
        self.saved_grades: list[dict[str, object]] = []
        self.saved_analyses: list[dict[str, object]] = []

    def save_question_grade(self, **kwargs: object) -> None:
        self.saved_grades.append(kwargs)

    def save_question_analysis(self, **kwargs: object) -> None:
        self.saved_analyses.append(kwargs)


class RubricPersistentFakeDataProvider(FakeDataProvider):
    def __init__(self) -> None:
        self.saved_rubrics: list[dict[str, object]] = []
        self.rubric_options = [
            RubricOptionSeed(label="Correct", points_delta=0.0),
            RubricOptionSeed(label="Incorrect", points_delta=-10.0),
        ]
        self.raise_on_save: Exception | None = None

    def build_question_seeds(self, **kwargs):  # noqa: ANN001, ANN201
        seeds = super().build_question_seeds(**kwargs)
        return [
            replace(seed, rubric_options=list(self.rubric_options))
            for seed in seeds
        ]

    def save_rubric_options(self, **kwargs: object) -> RubricEditResult:
        if self.raise_on_save is not None:
            raise self.raise_on_save
        self.saved_rubrics.append(kwargs)
        options = kwargs["options"]
        assert isinstance(options, list)
        self.rubric_options = list(options)
        return RubricEditResult(propagated_count=3)


@dataclass
class FakeGradeScreen:
    status: str = ""
    bound_student_name: str = ""
    bound_labels: list[str] | None = None
    progress: str = ""
    presented_question_id: str | None = None
    rubric_callback: object | None = None

    def bind_run(self, run, breadcrumb_labels: list[str]) -> None:
        self.bound_student_name = run.student_name
        self.bound_labels = breadcrumb_labels

    def set_status(self, text: str) -> None:
        self.status = text

    def set_progress(self, text: str) -> None:
        self.progress = text

    def present_rubric_editor(self, app, question, callback) -> None:  # noqa: ANN001
        _ = app
        self.presented_question_id = question.question_id
        self.rubric_callback = callback


@dataclass
class FakeQAPickerScreen:
    status: str = ""
    bound_questions: list[NavItem] | None = None
    bound_students: list[NavItem] | None = None
    focused: bool = False
    moved_to_index: int | None = None

    def bind_assignment(
        self,
        questions: list[NavItem],
        *,
        students: list[NavItem],
        breadcrumb_labels: list[str],
        assignment_name: str,
        student_question_statuses: dict[tuple[str, str], str] | None = None,
    ) -> None:
        _ = (breadcrumb_labels, assignment_name, student_question_statuses)
        self.bound_questions = questions
        self.bound_students = students

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


def test_open_question_picker_binds_students_with_progress() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"

    controller = GradeController(app, state, FakeDataProvider())
    controller.open_question_picker_for_assignment()

    assert app.qa_screen.bound_students is not None
    assert [item.label for item in app.qa_screen.bound_students] == [
        "Tommy Liu",
        "Andy Cortes",
    ]
    assert [item.meta for item in app.qa_screen.bound_students] == [
        "0/2 graded",
        "0/2 graded",
    ]


def test_refresh_qa_picker_updates_student_question_progress() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"

    controller = GradeController(app, state, FakeDataProvider())
    controller.open_question_picker_for_assignment()
    state.qa_student_status[("1", "1", "Q1", "s1")] = "graded"

    controller.refresh_qa_picker("")

    assert app.qa_screen.bound_students is not None
    assert [item.meta for item in app.qa_screen.bound_students] == [
        "1/2 graded",
        "0/2 graded",
    ]


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


def test_rubric_selection_persists_grade_when_provider_supports_it() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    provider = PersistentFakeDataProvider()
    controller = GradeController(app, state, provider)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    state.current_page = PAGE_GRADE

    controller.on_rubric_selected(1)

    assert provider.saved_grades == [
        {
            "student_item_id": "s1",
            "question_id": "Q1",
            "possible_points": 0.0,
            "points_awarded": 0.0,
            "rubric_label": "Incorrect",
        }
    ]
    assert app.grade_screen.status == "Saved grade to submission file."


def test_comment_update_persists_analysis_when_provider_supports_it() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    provider = PersistentFakeDataProvider()
    controller = GradeController(app, state, provider)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    state.current_page = PAGE_GRADE

    controller.on_comment_updated("Needs clearer work.")

    assert provider.saved_analyses == [
        {
            "student_item_id": "s1",
            "question_id": "Q1",
            "analysis": "Needs clearer work.",
        }
    ]


def test_open_rubric_editor_presents_modal_when_provider_supports_it() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    provider = RubricPersistentFakeDataProvider()
    controller = GradeController(app, state, provider)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    state.current_page = PAGE_GRADE

    controller.open_rubric_editor()

    assert app.grade_screen.presented_question_id == "Q1"
    assert app.grade_screen.rubric_callback is not None


def test_open_rubric_editor_reports_unsupported_provider() -> None:
    app, state, controller = build_controller()
    state.current_page = PAGE_GRADE

    controller.open_rubric_editor()

    assert app.grade_screen.status == (
        "Rubric editing is not supported by this data provider."
    )


def test_rubric_editor_save_calls_provider_and_reloads_active_student() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    provider = RubricPersistentFakeDataProvider()
    controller = GradeController(app, state, provider)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    state.current_page = PAGE_GRADE
    original_options = [
        RubricOptionSeed(option.label, option.points_delta)
        for option in state.grading_run.current_question.rubric_options
    ]
    new_options = [
        RubricOptionSeed("Correct", 0.0),
        RubricOptionSeed("Partially Correct", -4.0),
        RubricOptionSeed("Incorrect", -10.0),
    ]

    controller.on_rubric_options_saved(
        new_options,
        original_options=original_options,
    )

    assert provider.saved_rubrics == [
        {
            "student_item_id": "s1",
            "question_id": "Q1",
            "options": new_options,
            "original_options": original_options,
        }
    ]
    current = state.grading_run.current_question
    assert current is not None
    assert [option.label for option in current.rubric_options] == [
        "Correct",
        "Partially Correct",
        "Incorrect",
    ]
    assert app.grade_screen.status == "Rubric saved. Updated 3 matching grades."


def test_rubric_editor_save_reports_provider_errors() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    provider = RubricPersistentFakeDataProvider()
    provider.raise_on_save = ValueError("Cannot delete rubric option used by 1.")
    controller = GradeController(app, state, provider)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    state.current_page = PAGE_GRADE
    current = state.grading_run.current_question
    assert current is not None
    original_options = [
        RubricOptionSeed(option.label, option.points_delta)
        for option in current.rubric_options
    ]

    controller.on_rubric_options_saved(
        [
            RubricOptionSeed("Correct", 0.0),
            RubricOptionSeed("Partially Correct", -4.0),
            RubricOptionSeed("Incorrect", -10.0),
        ],
        original_options=original_options,
    )

    assert app.grade_screen.status == (
        "Could not save rubric: Cannot delete rubric option used by 1."
    )
