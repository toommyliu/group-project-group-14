from dataclasses import dataclass, replace

from opengrader.tui.controllers.grade_controller import GradeController
from opengrader.tui.data import RubricEditResult, RubricOptionSeed, StudentSummary
from opengrader.tui.state import PAGE_GRADE, PAGE_QA_PICKER, TuiWorkflowState
from opengrader.tui.statistics import QuestionStatistics
from opengrader.tui.ui.widgets.nav_item import NavItem

from .fakes import FakeDataProvider


def _seed(
    label: str,
    points_delta: float,
    selection_mode: str = "exclusive",
) -> RubricOptionSeed:
    kind = (
        "correct"
        if label == "Correct"
        else "incorrect"
        if label == "Incorrect"
        else "partial"
    )
    return RubricOptionSeed(label, points_delta, kind, selection_mode)


class PersistentFakeDataProvider(FakeDataProvider):
    def __init__(self) -> None:
        self.saved_grades: list[dict[str, object]] = []
        self.saved_analyses: list[dict[str, object]] = []
        self.committed_analyses: list[dict[str, object]] = []
        self.saved_annotations: list[dict[str, object]] = []
        self.annotation_error: Exception | None = None

    def save_question_grade(self, **kwargs: object) -> None:
        self.saved_grades.append(kwargs)

    def stage_question_analysis(self, **kwargs: object) -> None:
        self.saved_analyses.append(kwargs)

    def commit_question_analysis(self, **kwargs: object) -> None:
        if self.annotation_error is not None:
            raise self.annotation_error
        self.committed_analyses.append(kwargs)
        self.saved_annotations.append(
            {
                "student_item_id": kwargs.get("student_item_id"),
                "question_id": kwargs.get("question_id"),
                "comment": kwargs.get("analysis"),
            }
        )

    def save_question_analysis(self, **kwargs: object) -> None:
        self.stage_question_analysis(**kwargs)
        self.commit_question_analysis(previous_analysis="", **kwargs)

    def save_question_comment_annotation(self, **kwargs: object) -> None:
        if self.annotation_error is not None:
            raise self.annotation_error
        self.saved_annotations.append(kwargs)


class SavedProgressFakeDataProvider(FakeDataProvider):
    def build_question_seeds(self, **kwargs):  # noqa: ANN001, ANN201
        student_item_id = kwargs.get("student_item_id")
        seeds = super().build_question_seeds(**kwargs)
        if student_item_id not in {"s1", "s2"}:
            return seeds
        return [
            replace(seed, selected_rubric_labels=("Correct",))
            if seed.question_id == "Q1"
            else seed
            for seed in seeds
        ]


class SavedStatisticsFakeDataProvider(FakeDataProvider):
    def __init__(self) -> None:
        self.q1_s1_score = 8.0

    def build_question_seeds(self, **kwargs):  # noqa: ANN001, ANN201
        student_item_id = kwargs.get("student_item_id")
        seeds = super().build_question_seeds(**kwargs)
        if student_item_id == "s1":
            return [
                replace(seed, points=10, points_awarded=self.q1_s1_score)
                if seed.question_id == "Q1"
                else replace(seed, points=10)
                for seed in seeds
            ]
        if student_item_id == "s2":
            return [
                replace(seed, points=10, points_awarded=6.0)
                if seed.question_id == "Q1"
                else replace(seed, points=10)
                for seed in seeds
            ]
        return [replace(seed, points=10) for seed in seeds]

    def save_question_grade(self, **kwargs: object) -> None:
        if kwargs.get("student_item_id") == "s1" and kwargs.get("question_id") == "Q1":
            points_awarded = kwargs.get("points_awarded")
            assert isinstance(points_awarded, float | int)
            self.q1_s1_score = float(points_awarded)


class ChecklistPersistentFakeDataProvider(PersistentFakeDataProvider):
    def build_question_seeds(self, **kwargs):  # noqa: ANN001, ANN201
        seeds = super().build_question_seeds(**kwargs)
        rubric_options = [
            _seed("Correct", 0.0),
            _seed("Partially Correct", -5.0, "checklist"),
            _seed("Incorrect", -10.0),
        ]
        return [
            replace(seed, rubric_options=list(rubric_options), points=10)
            for seed in seeds
        ]


class RubricPersistentFakeDataProvider(FakeDataProvider):
    def __init__(self) -> None:
        self.saved_rubrics: list[dict[str, object]] = []
        self.rubric_options = [
            _seed("Correct", 0.0),
            _seed("Incorrect", -10.0),
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


class StatefulFakeDataProvider(FakeDataProvider):
    def __init__(self) -> None:
        self.grades: dict[tuple[str, str], dict[str, object]] = {}
        self.analyses: dict[tuple[str, str], str] = {}
        self.transcriptions: dict[tuple[str, str], dict[str, object]] = {}
        self.save_grade_calls: list[dict[str, object]] = []
        self.save_analysis_calls: list[dict[str, object]] = []
        self.save_transcription_calls: list[dict[str, object]] = []

    def build_question_seeds(self, **kwargs):  # noqa: ANN003, ANN201
        student_item_id = kwargs.get("student_item_id")
        seeds = super().build_question_seeds(**kwargs)
        result = []
        for seed in seeds:
            key = (student_item_id, seed.question_id)
            seed = replace(seed, points=10)
            grade = self.grades.get(key)
            if grade is not None:
                seed = replace(
                    seed,
                    points_awarded=grade["points_awarded"],
                    selected_rubric_labels=grade["rubric_labels"],
                )
            analysis = self.analyses.get(key)
            if analysis:
                seed = replace(seed, analysis=analysis)
            transcription = self.transcriptions.get(key)
            if transcription is not None:
                seed = replace(
                    seed,
                    response_text=transcription["response_text"],
                    response_confidence=transcription["response_confidence"],
                )
            result.append(seed)
        return result

    def save_question_grade(self, **kwargs: object) -> None:
        self.save_grade_calls.append(kwargs)
        key = (kwargs["student_item_id"], kwargs["question_id"])
        self.grades[key] = {
            "points_awarded": kwargs["points_awarded"],
            "rubric_labels": kwargs["rubric_labels"],
        }

    def save_question_analysis(self, **kwargs: object) -> None:
        self.save_analysis_calls.append(kwargs)
        key = (kwargs["student_item_id"], kwargs["question_id"])
        self.analyses[key] = kwargs["analysis"]  # type: ignore[assignment]

    def save_question_transcription(self, **kwargs: object) -> None:
        self.save_transcription_calls.append(kwargs)
        key = (kwargs["student_item_id"], kwargs["question_id"])
        self.transcriptions[key] = {
            "response_text": kwargs["response_text"],
            "response_confidence": kwargs.get("response_confidence", 100.0),
        }

    def save_question_comment_annotation(self, **kwargs: object) -> None:
        _ = kwargs


def _build_stateful_controller() -> (
    "tuple[FakeApp, TuiWorkflowState, StatefulFakeDataProvider, GradeController]"
):
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    provider = StatefulFakeDataProvider()
    controller = GradeController(app, state, provider)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    state.current_page = PAGE_GRADE
    return app, state, provider, controller


def test_undo_restores_grade_to_previous_persisted_state() -> None:
    app, state, provider, controller = _build_stateful_controller()

    controller.on_rubric_selected(1)
    assert provider.grades[("s1", "Q1")]["rubric_labels"] == ("Incorrect",)
    assert len(state.grading_log) == 1
    assert state.qa_student_status.get(("1", "1", "Q1", "s1")) == "graded"

    controller.undo_last_grade_change()

    assert provider.save_grade_calls[-1]["points_awarded"] is None
    assert provider.save_grade_calls[-1]["rubric_labels"] == ()
    assert provider.grades[("s1", "Q1")]["points_awarded"] is None
    assert len(state.grading_log) == 0
    assert ("1", "1", "Q1", "s1") not in state.qa_student_status
    assert app.grade_screen.status == "Undid grade · Question 1."


def test_undo_restores_comment_to_previous_value() -> None:
    _, state, provider, controller = _build_stateful_controller()

    controller.on_comment_updated("Great work")
    assert provider.analyses[("s1", "Q1")] == "Great work"
    controller.commit_pending_comment()
    assert len(state.grading_log) == 1

    controller.undo_last_grade_change()

    assert provider.analyses[("s1", "Q1")] == ""
    assert provider.save_analysis_calls[-1]["analysis"] == ""
    assert len(state.grading_log) == 0


def test_transcription_edit_persists_marks_verified_and_logs() -> None:
    _, state, provider, controller = _build_stateful_controller()

    controller.on_transcription_updated("the cat sat on the mat")

    call = provider.save_transcription_calls[-1]
    assert call["response_text"] == "the cat sat on the mat"
    assert provider.transcriptions[("s1", "Q1")]["response_text"] == (
        "the cat sat on the mat"
    )
    assert state.grading_run.current_question.response_confidence == 100.0
    assert len(state.grading_log) == 1
    entry = state.grading_log.peek_last_undoable()
    assert entry is not None and entry.kind == "transcription"


def test_transcription_edit_is_noop_when_unchanged() -> None:
    _, state, provider, controller = _build_stateful_controller()

    controller.on_transcription_updated("")

    assert provider.save_transcription_calls == []
    assert len(state.grading_log) == 0


def test_undo_restores_transcription_to_previous_reading() -> None:
    _, state, provider, controller = _build_stateful_controller()

    controller.on_transcription_updated("corrected reading")
    assert provider.transcriptions[("s1", "Q1")]["response_text"] == (
        "corrected reading"
    )
    assert len(state.grading_log) == 1

    controller.undo_last_grade_change()

    assert provider.save_transcription_calls[-1]["response_text"] == ""
    assert provider.transcriptions[("s1", "Q1")]["response_text"] == ""
    assert len(state.grading_log) == 0


def test_undo_reports_nothing_to_undo_when_log_empty() -> None:
    app, _, _, controller = _build_stateful_controller()

    controller.undo_last_grade_change()

    assert app.grade_screen.status == "Nothing to undo."


def test_undo_is_multi_level_and_newest_first() -> None:
    _, state, provider, controller = _build_stateful_controller()

    controller.on_rubric_selected(1)
    controller.on_comment_updated("See rubric")
    controller.commit_pending_comment()
    assert len(state.grading_log) == 2

    controller.undo_last_grade_change()
    assert provider.analyses[("s1", "Q1")] == ""
    assert provider.grades[("s1", "Q1")]["rubric_labels"] == ("Incorrect",)
    assert len(state.grading_log) == 1

    controller.undo_last_grade_change()
    assert provider.grades[("s1", "Q1")]["points_awarded"] is None
    assert len(state.grading_log) == 0


def test_undo_of_coalesced_grades_restores_pre_streak_state() -> None:
    _, state, provider, controller = _build_stateful_controller()

    controller.on_rubric_selected(1)
    controller.on_rubric_selected(0)
    assert len(state.grading_log) == 1
    assert provider.grades[("s1", "Q1")]["rubric_labels"] == ("Correct",)

    controller.undo_last_grade_change()

    assert provider.grades[("s1", "Q1")]["points_awarded"] is None
    assert provider.grades[("s1", "Q1")]["rubric_labels"] == ()
    assert len(state.grading_log) == 0


def test_undo_skips_rubric_edits_without_revert() -> None:
    from opengrader.tui.state import GradingLogEntry

    _, state, provider, controller = _build_stateful_controller()

    controller.on_rubric_selected(1)
    state.grading_log.record(
        GradingLogEntry(kind="rubric", question_id="Q1", summary="Rubric edited")
    )
    assert len(state.grading_log) == 2

    controller.undo_last_grade_change()

    assert provider.grades[("s1", "Q1")]["points_awarded"] is None
    remaining = state.grading_log.entries_latest_first()
    assert [entry.kind for entry in remaining] == ["rubric"]


class GroupingFakeDataProvider(PersistentFakeDataProvider):
    _RESPONSES = {"s1": "42", "s2": "42 ", "s3": "seven"}

    def list_students(self, course_id: int | None) -> list[StudentSummary]:
        if course_id is None:
            return []
        return [
            StudentSummary(1, "Tommy Liu"),
            StudentSummary(2, "Andy Cortes"),
            StudentSummary(3, "Casey Kim"),
        ]

    def build_question_seeds(self, **kwargs):  # noqa: ANN003, ANN201
        student_item_id = kwargs.get("student_item_id")
        seeds = super().build_question_seeds(**kwargs)
        response = self._RESPONSES.get(student_item_id, "")
        return [
            replace(seed, points=10, response_text=response)
            if seed.question_id == "Q1"
            else replace(seed, points=10)
            for seed in seeds
        ]


@dataclass
class FakeGradeScreen:
    status: str = ""
    bound_student_name: str = ""
    bound_labels: list[str] | None = None
    progress: str = ""
    answer_group: str = ""
    presented_question_id: str | None = None
    rubric_callback: object | None = None

    def bind_run(
        self, run, breadcrumb_labels: list[str], *, preserve_view: bool = False
    ) -> None:
        self.bound_student_name = run.student_name
        self.bound_labels = breadcrumb_labels

    def set_status(self, text: str) -> None:
        self.status = text

    def set_progress(self, text: str) -> None:
        self.progress = text

    def set_answer_group(self, text: str) -> None:
        self.answer_group = text

    def present_rubric_editor(self, app, question, callback) -> None:  # noqa: ANN001
        _ = app
        self.presented_question_id = question.question_id
        self.rubric_callback = callback


@dataclass
class FakeQAPickerScreen:
    status: str = ""
    bound_questions: list[NavItem] | None = None
    bound_students: list[NavItem] | None = None
    question_statistics: dict[str, QuestionStatistics] | None = None
    focused: bool = False
    focused_pane: str | None = None
    active_pane: str = "questions"
    question_index: int = 0
    student_index: int = 0
    moved_to_index: int | None = None
    moved_student_to_index: int | None = None

    def bind_assignment(
        self,
        questions: list[NavItem],
        *,
        students: list[NavItem],
        breadcrumb_labels: list[str],
        assignment_name: str,
        student_question_statuses: dict[tuple[str, str], str] | None = None,
        question_statistics: dict[str, QuestionStatistics] | None = None,
        preserve_view: bool = False,
    ) -> None:
        _ = (breadcrumb_labels, assignment_name, student_question_statuses)
        self.bound_questions = questions
        self.bound_students = students
        self.question_statistics = question_statistics

    def set_status(self, text: str) -> None:
        self.status = text

    def focus_questions(self) -> None:
        self.focused = True
        self.focused_pane = "questions"
        self.active_pane = "questions"

    def focus_students(self) -> None:
        self.focused = True
        self.focused_pane = "students"
        self.active_pane = "students"

    def show_question_details(self, item: NavItem) -> None:
        _ = item

    def move_cursor(self, direction: int) -> None:
        _ = direction

    def move_to_index(self, index: int) -> None:
        self.question_index = index
        self.moved_to_index = index

    def move_student_to_index(self, index: int) -> None:
        self.student_index = index
        self.moved_student_to_index = index

    def selected_question(self) -> NavItem | None:
        if not self.bound_questions:
            return None
        return self.bound_questions[self.question_index]

    def selected_student(self) -> NavItem | None:
        if not self.bound_students:
            return None
        return self.bound_students[self.student_index]

    def students_have_focus(self) -> bool:
        return self.active_pane == "students"


class FakeApp:
    def show_grade_conflict(self) -> None:
        pass

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


def test_open_review_target_opens_that_student_and_question() -> None:
    app, state, controller = build_controller()

    controller.open_review_target("s2", "Q2")

    assert app.page == PAGE_GRADE
    assert state.selection.student_item_id == "s2"
    assert state.selection.question_id == "Q2"


def test_open_review_target_without_question_opens_first_question() -> None:
    app, state, controller = build_controller()

    controller.open_review_target("s2", "")

    assert app.page == PAGE_GRADE
    assert state.selection.student_item_id == "s2"
    assert state.selection.question_id == "Q1"


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


def test_open_question_picker_hydrates_saved_qa_progress() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"

    controller = GradeController(app, state, SavedProgressFakeDataProvider())
    controller.open_question_picker_for_assignment()

    assert app.qa_screen.bound_questions is not None
    assert [item.meta for item in app.qa_screen.bound_questions] == [
        "2/2 graded",
        "0/2 graded",
    ]
    assert app.qa_screen.bound_students is not None
    assert [item.meta for item in app.qa_screen.bound_students] == [
        "1/2 graded",
        "1/2 graded",
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


def test_refresh_qa_picker_passes_question_statistics() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"

    controller = GradeController(app, state, SavedStatisticsFakeDataProvider())
    controller.open_question_picker_for_assignment()

    stats = app.qa_screen.question_statistics
    assert stats is not None
    assert stats["Q1"].graded_count == 2
    assert stats["Q1"].average == 7.0
    assert stats["Q2"].graded_count == 0


def test_question_statistics_refresh_after_saved_grade() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    provider = SavedStatisticsFakeDataProvider()
    controller = GradeController(app, state, provider)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    state.current_page = PAGE_GRADE

    controller.on_rubric_selected(1)
    controller.return_to_qa_picker()

    stats = app.qa_screen.question_statistics
    assert stats is not None
    assert stats["Q1"].graded_count == 2
    assert stats["Q1"].average == 3.0


def test_jump_ungraded_question_uses_hydrated_saved_progress() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"

    controller = GradeController(app, state, SavedProgressFakeDataProvider())
    controller.open_question_picker_for_assignment()
    state.current_page = PAGE_QA_PICKER

    controller.qa_jump_ungraded_question()

    assert app.qa_screen.moved_to_index == 1
    assert app.qa_screen.status == "Jumped to Question 2 (0/2 graded)"


def test_finalize_student_marks_status_and_advances_to_next_student() -> None:
    app, state, controller = build_controller()
    state.current_page = PAGE_GRADE

    refreshed: list[str] = []
    controller.attach_student_refresh(lambda: refreshed.append("called"))
    controller.finalize_student()

    assert refreshed == ["called"]
    assert state.student_status[("1", "1", "s1")] == "graded"
    assert state.active_student_index == 1
    assert state.selection_labels["student"] == "s2"
    assert app.grade_screen.status == "QA finalized. Loaded next ungraded student."
    assert app.grade_screen.progress == "Progress: 1 / 2 students"


def test_next_previous_ungraded_student_skip_graded_for_selected_qa() -> None:
    _app, state, controller = build_controller()
    state.current_page = PAGE_GRADE
    state.qa_student_status[("1", "1", "Q1", "s1")] = "graded"

    controller.open_student_by_index(0)
    controller.next_ungraded_student()

    assert state.active_student_index == 1
    assert state.selection_labels["student"] == "s2"

    state.qa_student_status[("1", "1", "Q1", "s2")] = "graded"
    controller.previous_ungraded_student()

    assert state.active_student_index == 1


def test_finalize_last_student_returns_to_qa_picker() -> None:
    app, state, controller = build_controller()

    state.qa_student_status[("1", "1", "Q1", "s1")] = "graded"
    controller.open_student_by_index(1)
    state.current_page = PAGE_GRADE
    controller.finalize_student()

    assert app.page == PAGE_QA_PICKER
    assert app.qa_screen.status == "All students finalized for this QA."
    assert app.qa_screen.focused is True
    assert app.qa_screen.focused_pane == "questions"


def test_return_to_home_alias_returns_to_qa_picker() -> None:
    app, state, controller = build_controller()
    state.current_page = PAGE_GRADE

    controller.return_to_home()

    assert app.page == PAGE_QA_PICKER
    assert app.qa_screen.focused is True
    assert app.qa_screen.focused_pane == "questions"


def test_return_to_qa_picker_restores_active_question_selection() -> None:
    app, state, controller = build_controller()
    controller.open_question_by_index(1)
    state.current_page = PAGE_GRADE

    controller.return_to_qa_picker()

    assert app.page == PAGE_QA_PICKER
    assert app.qa_screen.moved_to_index == 1
    assert app.qa_screen.focused is True
    assert app.qa_screen.focused_pane == "questions"


def test_return_to_qa_picker_restores_active_student_selection() -> None:
    app, state, controller = build_controller()
    controller.open_student_by_index(1)
    state.current_page = PAGE_GRADE

    controller.return_to_qa_picker()

    assert app.page == PAGE_QA_PICKER
    assert app.qa_screen.moved_student_to_index == 1
    assert app.qa_screen.focused is True
    assert app.qa_screen.focused_pane == "questions"


def test_return_to_qa_picker_restores_question_pane_origin() -> None:
    app, state, controller = build_controller()
    state.current_page = PAGE_QA_PICKER
    app.qa_screen.active_pane = "questions"
    app.qa_screen.move_to_index(1)

    item = app.qa_screen.selected_question()
    assert item is not None
    controller.on_qa_selected(item)
    state.current_page = PAGE_GRADE
    controller.return_to_qa_picker()

    assert app.qa_screen.moved_to_index == 1
    assert app.qa_screen.focused_pane == "questions"


def test_return_to_qa_picker_restores_student_pane_origin() -> None:
    app, state, controller = build_controller()
    state.current_page = PAGE_QA_PICKER
    app.qa_screen.active_pane = "students"
    app.qa_screen.move_to_index(1)
    app.qa_screen.move_student_to_index(1)

    item = app.qa_screen.selected_question()
    assert item is not None
    controller.on_qa_selected(item)
    state.current_page = PAGE_GRADE
    controller.next_student()
    controller.return_to_qa_picker()

    assert app.qa_screen.moved_to_index == 1
    assert app.qa_screen.moved_student_to_index == 1
    assert app.qa_screen.focused_pane == "students"


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
            "rubric_labels": ("Incorrect",),
        }
    ]
    assert app.grade_screen.status == "Grade saved."
    assert state.qa_student_status[("1", "1", "Q1", "s1")] == "graded"
    assert app.grade_screen.progress == "Progress: 1 / 2 students"


def test_clearing_last_checklist_selection_removes_qa_progress() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    provider = ChecklistPersistentFakeDataProvider()
    controller = GradeController(app, state, provider)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    state.current_page = PAGE_GRADE

    controller.on_rubric_selected(1)
    controller.on_rubric_selected(1)

    assert ("1", "1", "Q1", "s1") not in state.qa_student_status
    assert provider.saved_grades[-1] == {
        "student_item_id": "s1",
        "question_id": "Q1",
        "possible_points": 10,
        "points_awarded": None,
        "rubric_labels": (),
    }
    assert app.grade_screen.progress == "Progress: 0 / 2 students"


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
    assert provider.saved_annotations == []

    controller.commit_pending_comment()

    assert provider.saved_annotations == [
        {
            "student_item_id": "s1",
            "question_id": "Q1",
            "comment": "Needs clearer work.",
        }
    ]


def test_failed_comment_commit_keeps_pending_work_for_retry() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    provider = PersistentFakeDataProvider()
    provider.annotation_error = ValueError("Question source metadata missing")
    controller = GradeController(app, state, provider)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    state.current_page = PAGE_GRADE

    controller.on_comment_updated("Needs clearer work.")
    assert provider.saved_analyses
    controller.commit_pending_comment()

    assert provider.saved_annotations == []
    assert app.grade_screen.status == (
        "Could not save feedback: Question source metadata missing"
    )

    assert len(state.grading_log) == 0
    provider.annotation_error = None
    assert controller.commit_pending_comment() == ""
    assert len(provider.saved_annotations) == 1
    assert len(state.grading_log) == 1


class CommentSeedFakeDataProvider(PersistentFakeDataProvider):
    def build_question_seeds(self, **kwargs):  # noqa: ANN001, ANN201
        seeds = super().build_question_seeds(**kwargs)
        annotated = []
        for seed in seeds:
            options = [
                replace(option, default_comment="See me about recursion.")
                if option.label == "Incorrect"
                else option
                for option in seed.rubric_options
            ]
            annotated.append(replace(seed, rubric_options=options))
        return annotated


def _comment_controller() -> tuple[FakeApp, CommentSeedFakeDataProvider, GradeController]:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    provider = CommentSeedFakeDataProvider()
    controller = GradeController(app, state, provider)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    state.current_page = PAGE_GRADE
    return app, provider, controller


def test_rubric_selection_auto_inserts_default_comment() -> None:
    _, provider, controller = _comment_controller()

    controller.on_rubric_selected(1)

    assert provider.saved_grades[-1]["rubric_labels"] == ("Incorrect",)
    assert provider.saved_analyses == [
        {
            "student_item_id": "s1",
            "question_id": "Q1",
            "analysis": "See me about recursion.",
        }
    ]
    assert provider.saved_annotations[-1] == {
        "student_item_id": "s1",
        "question_id": "Q1",
        "comment": "See me about recursion.",
    }


def test_rubric_default_comment_is_not_duplicated_on_reselect() -> None:
    _, provider, controller = _comment_controller()

    controller.on_rubric_selected(1)
    controller.on_rubric_selected(1)
    controller.on_rubric_selected(0)

    assert len(provider.saved_analyses) == 1
    assert provider.saved_analyses[0]["analysis"] == "See me about recursion."


def test_rubric_default_comment_appends_to_existing_comment() -> None:
    _, provider, controller = _comment_controller()

    controller.on_comment_updated("Nice attempt.")
    controller.on_rubric_selected(1)

    assert provider.saved_analyses[-1]["analysis"] == (
        "Nice attempt. See me about recursion."
    )


def test_rubric_selection_without_default_comment_writes_no_analysis() -> None:
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

    assert provider.saved_grades
    assert provider.saved_analyses == []


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
        _seed(option.label, option.points_delta)
        for option in state.grading_run.current_question.rubric_options
    ]
    new_options = [
        _seed("Correct", 0.0),
        _seed("Partially Correct", -4.0),
        _seed("Incorrect", -10.0),
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


def _build_grouping_controller() -> tuple[FakeApp, TuiWorkflowState, GroupingFakeDataProvider, GradeController]:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    provider = GroupingFakeDataProvider()
    controller = GradeController(app, state, provider)
    controller.attach_student_refresh(lambda: None)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    state.current_page = PAGE_GRADE
    return app, state, provider, controller


def test_answer_group_indicator_shows_shared_answer_count() -> None:
    app, _state, _provider, controller = _build_grouping_controller()

    controller.refresh_grade_screen(status="")

    assert app.grade_screen.answer_group == (
        "Answer group: 2 students share this answer"
    )


def test_apply_grade_to_answer_group_applies_to_matching_students_only() -> None:
    app, state, provider, controller = _build_grouping_controller()

    controller.on_rubric_selected(0)
    controller.apply_grade_to_answer_group()

    graded_students = {
        (grade["student_item_id"], grade["question_id"])
        for grade in provider.saved_grades
    }
    assert ("s1", "Q1") in graded_students
    assert ("s2", "Q1") in graded_students
    assert ("s3", "Q1") not in graded_students

    s2_grade = next(
        grade for grade in provider.saved_grades if grade["student_item_id"] == "s2"
    )
    assert s2_grade["rubric_labels"] == ("Correct",)
    assert s2_grade["points_awarded"] == 10.0

    assert state.qa_student_status[("1", "1", "Q1", "s2")] == "graded"
    assert ("1", "1", "Q1", "s3") not in state.qa_student_status
    assert app.grade_screen.status == (
        "Applied this grade to 1 other student(s) with the same answer."
    )


def test_apply_grade_to_answer_group_logs_a_single_batch_entry() -> None:
    _app, state, _provider, controller = _build_grouping_controller()

    controller.on_rubric_selected(0)
    controller.apply_grade_to_answer_group()

    batch_entries = [
        entry
        for entry in state.grading_log.entries_latest_first()
        if entry.summary.startswith("Group grade")
    ]
    assert len(batch_entries) == 1
    assert batch_entries[0].summary == "Group grade · 2 students · 10/10 pts"


def test_apply_grade_to_answer_group_requires_a_selection() -> None:
    app, _state, provider, controller = _build_grouping_controller()

    controller.apply_grade_to_answer_group()

    assert provider.saved_grades == []
    assert app.grade_screen.status == (
        "Select a rubric option before grading the answer group."
    )


class MixedRubricGroupingFakeDataProvider(GroupingFakeDataProvider):
    _RESPONSES = {"s1": "42", "s2": "42", "s3": "seven"}

    def build_question_seeds(self, **kwargs):  # noqa: ANN003, ANN201
        student_item_id = kwargs.get("student_item_id")
        seeds = super().build_question_seeds(**kwargs)
        if student_item_id != "s2":
            return seeds
        other_rubric = [
            _seed("Full", 0.0),
            _seed("None", -10.0),
        ]
        return [
            replace(seed, rubric_options=list(other_rubric))
            if seed.question_id == "Q1"
            else seed
            for seed in seeds
        ]


def test_apply_grade_to_answer_group_skips_incompatible_rubric() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    provider = MixedRubricGroupingFakeDataProvider()
    controller = GradeController(app, state, provider)
    controller.attach_student_refresh(lambda: None)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    state.current_page = PAGE_GRADE

    controller.on_rubric_selected(0)
    controller.apply_grade_to_answer_group()

    assert not any(
        grade["student_item_id"] == "s2" for grade in provider.saved_grades
    )
    assert ("1", "1", "Q1", "s2") not in state.qa_student_status
    assert "skipped (different rubric)" in app.grade_screen.status


def test_apply_grade_to_answer_group_reports_unique_answer() -> None:
    app, _state, provider, controller = _build_grouping_controller()

    controller.open_student_by_index(2)
    controller.on_rubric_selected(0)
    saved_before = len(provider.saved_grades)

    controller.apply_grade_to_answer_group()

    assert len(provider.saved_grades) == saved_before
    assert app.grade_screen.status == "No other students share this answer."


def test_apply_grade_to_answer_group_unsupported_provider() -> None:
    app, state, controller = build_controller()
    state.current_page = PAGE_GRADE

    controller.apply_grade_to_answer_group()

    assert app.grade_screen.status == (
        "Group grading is not supported by this data provider."
    )


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
        _seed(option.label, option.points_delta)
        for option in current.rubric_options
    ]

    controller.on_rubric_options_saved(
        [
            _seed("Correct", 0.0),
            _seed("Partially Correct", -4.0),
            _seed("Incorrect", -10.0),
        ],
        original_options=original_options,
    )

    assert app.grade_screen.status == (
        "Could not save rubric: Cannot delete rubric option used by 1."
    )


def test_group_mode_reports_how_many_distinct_answers_there_are() -> None:
    app, state, _provider, controller = _build_grouping_controller()

    controller.toggle_group_mode()

    assert state.group_mode is True
    assert "2 distinct answer(s)" in app.grade_screen.status
    assert "2 still to grade" in app.grade_screen.status


def test_group_mode_indicator_is_always_shown_even_for_a_unique_answer() -> None:
    app, _state, _provider, controller = _build_grouping_controller()
    controller.toggle_group_mode()

    assert app.grade_screen.answer_group.startswith("By answer · answer 1 of 2")
    assert "2 students share this answer" in app.grade_screen.answer_group

    controller.next_student()
    assert "only this student gave it" in app.grade_screen.answer_group


def test_moving_forward_skips_the_rest_of_the_current_answer_group() -> None:
    _app, state, _provider, controller = _build_grouping_controller()
    controller.toggle_group_mode()

    controller.next_student()

    assert state.selection.student_item_id == "s3"


def test_moving_forward_without_group_mode_still_walks_students() -> None:
    _app, state, _provider, controller = _build_grouping_controller()

    controller.next_student()

    assert state.selection.student_item_id == "s2"


def test_moving_back_returns_to_the_previous_answer() -> None:
    _app, state, _provider, controller = _build_grouping_controller()
    controller.toggle_group_mode()
    controller.next_student()
    assert state.selection.student_item_id == "s3"

    controller.previous_student()

    assert state.selection.student_item_id == "s1"


def test_next_ungraded_skips_answers_that_are_fully_graded() -> None:
    _app, state, _provider, controller = _build_grouping_controller()
    controller.toggle_group_mode()

    controller.on_rubric_selected(0)
    controller.apply_grade_to_answer_group()

    assert state.selection.student_item_id == "s3"


def test_grading_a_group_advances_and_says_what_is_left() -> None:
    app, _state, _provider, controller = _build_grouping_controller()
    controller.toggle_group_mode()

    controller.on_rubric_selected(0)
    controller.apply_grade_to_answer_group()

    status = app.grade_screen.status
    assert "Applied this grade to 1 other student(s)" in status
    assert "1 answer(s) left on this question." in status


def test_the_last_group_says_the_question_is_finished() -> None:
    app, state, _provider, controller = _build_grouping_controller()
    controller.toggle_group_mode()

    controller.on_rubric_selected(0)
    controller.apply_grade_to_answer_group()
    controller.on_rubric_selected(0)
    controller.apply_grade_to_answer_group()

    assert "Every distinct answer on this question is graded." in app.grade_screen.status
    assert state.selection.student_item_id == "s3"


def test_grading_a_group_outside_group_mode_does_not_move() -> None:
    _app, state, _provider, controller = _build_grouping_controller()

    controller.on_rubric_selected(0)
    controller.apply_grade_to_answer_group()

    assert state.selection.student_item_id == "s1"


def test_turning_group_mode_off_restores_student_navigation() -> None:
    app, state, _provider, controller = _build_grouping_controller()
    controller.toggle_group_mode()
    controller.toggle_group_mode()

    assert state.group_mode is False
    assert app.grade_screen.status == "Grading student by student again."
    controller.next_student()
    assert state.selection.student_item_id == "s2"


def test_the_groups_are_disjoint_so_the_count_can_reach_zero() -> None:
    _app, _state, _provider, controller = _build_grouping_controller()

    groups = controller._grading_groups("Q1")

    members = [member for group in groups for member in group]
    assert sorted(members) == ["s1", "s2", "s3"]
    assert len(members) == len(set(members))


def test_moving_past_the_last_answer_says_so_and_stays_put() -> None:
    app, state, _provider, controller = _build_grouping_controller()
    controller.toggle_group_mode()
    controller.next_student()

    controller.next_student()

    assert state.selection.student_item_id == "s3"
    assert app.grade_screen.status == "No later answers for this question."


def test_changing_question_lands_on_an_answer_that_still_needs_grading() -> None:
    _app, state, _provider, controller = _build_grouping_controller()
    controller.toggle_group_mode()
    controller.next_student()
    controller.on_rubric_selected(0)
    controller.apply_grade_to_answer_group()

    controller.next_question()
    controller.previous_question()

    assert state.grading_run.current_question.question_id == "Q1"
    assert state.selection.student_item_id == "s1"


def test_changing_question_stays_put_when_the_current_answer_is_ungraded() -> None:
    _app, state, _provider, controller = _build_grouping_controller()
    controller.toggle_group_mode()
    controller.next_student()

    controller.next_question()

    assert state.grading_run.current_question.question_id == "Q2"
    assert state.selection.student_item_id == "s3"


def test_changing_question_without_group_mode_keeps_the_student() -> None:
    _app, state, _provider, controller = _build_grouping_controller()
    controller.next_student()
    assert state.selection.student_item_id == "s2"

    controller.next_question()

    assert state.selection.student_item_id == "s2"


def test_answers_claimed_by_another_grader_do_not_hold_up_the_count() -> None:
    from opengrader.core.claims import WorkClaim, WorkSlice

    _app, state, _provider, controller = _build_grouping_controller()
    state.grader_actor = "prof"
    state.active_claims = (
        WorkClaim(
            claim_id="c1",
            actor="alice",
            created_at="2026-08-06T00:00:00+00:00",
            work=WorkSlice(question_ids=("Q1",), student_ids=("s3",)),
        ),
    )
    controller.toggle_group_mode()

    controller.on_rubric_selected(0)
    controller.apply_grade_to_answer_group()

    assert "Every distinct answer on this question is graded." in _app_status(controller)


def _app_status(controller: GradeController) -> str:
    return controller._app.get_grade_screen().status


def test_entering_group_mode_moves_to_the_most_given_answer() -> None:
    _app, state, _provider, controller = _build_grouping_controller()
    assert state.selection.student_item_id == "s1"

    controller.toggle_group_mode()

    groups = controller._grading_groups("Q1")
    assert controller._current_group_index(groups) == 0


class AiGradedFakeDataProvider(PersistentFakeDataProvider):
    def __init__(self) -> None:
        super().__init__()
        self.analyses = {
            "s1": "[AI · 62% · llama3.1] Names one of two properties.",
            "s2": "Partially correct per manual review",
            "s3": "[AI · 55% · llama3.1] Close enough.",
        }

    def list_students(self, course_id: int | None) -> list[StudentSummary]:
        if course_id is None:
            return []
        return [
            StudentSummary(1, "Tommy Liu"),
            StudentSummary(2, "Andy Cortes"),
            StudentSummary(3, "Casey Kim"),
        ]

    def build_question_seeds(self, **kwargs):  # noqa: ANN003, ANN201
        student_item_id = kwargs.get("student_item_id")
        seeds = super().build_question_seeds(**kwargs)
        analysis = self.analyses.get(student_item_id, "")
        return [
            replace(seed, points=10, points_awarded=6.0, analysis=analysis)
            if seed.question_id == "Q1"
            else replace(seed, points=10)
            for seed in seeds
        ]

    def stage_question_analysis(self, **kwargs: object) -> None:
        super().stage_question_analysis(**kwargs)
        if kwargs.get("question_id") == "Q1":
            student = kwargs.get("student_item_id")
            assert isinstance(student, str)
            analysis = kwargs.get("analysis")
            assert isinstance(analysis, str)
            self.analyses[student] = analysis


def _build_ai_controller():  # noqa: ANN202
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    provider = AiGradedFakeDataProvider()
    controller = GradeController(app, state, provider)
    controller.attach_student_refresh(lambda: None)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    state.current_page = PAGE_GRADE
    return app, state, provider, controller


def test_ai_review_mode_reports_how_many_need_confirming() -> None:
    app, state, _provider, controller = _build_ai_controller()

    controller.toggle_ai_review_mode()

    assert state.ai_review_mode is True
    assert "2 on this question" in app.grade_screen.status


def test_ai_review_mode_refuses_when_there_is_nothing_to_confirm() -> None:
    app, state, provider, controller = _build_ai_controller()
    provider.analyses = {"s1": "human", "s2": "human", "s3": "human"}

    controller.toggle_ai_review_mode()

    assert state.ai_review_mode is False
    assert "No unconfirmed AI grades" in app.grade_screen.status


def test_ai_review_mode_moves_between_ai_grades_only() -> None:
    _app, state, _provider, controller = _build_ai_controller()
    controller.toggle_ai_review_mode()

    assert state.active_student_index == 0
    controller.next_student()
    assert state.active_student_index == 2


def test_ai_review_indicator_names_the_model_and_confidence() -> None:
    app, _state, _provider, controller = _build_ai_controller()
    controller.toggle_ai_review_mode()

    assert "AI 62% · llama3.1" in app.grade_screen.answer_group
    assert "2 left" in app.grade_screen.answer_group


def test_confirming_keeps_the_grade_and_marks_the_note() -> None:
    _app, _state, provider, controller = _build_ai_controller()

    controller.confirm_ai_grade()

    saved = provider.saved_analyses[-1]
    assert saved["student_item_id"] == "s1"
    assert "confirmed" in saved["analysis"]
    assert "62%" in saved["analysis"]
    assert provider.saved_grades == []


def test_confirming_advances_to_the_next_one_in_the_mode() -> None:
    app, state, _provider, controller = _build_ai_controller()
    controller.toggle_ai_review_mode()

    controller.confirm_ai_grade()

    assert state.active_student_index == 2
    assert "1 left" in app.grade_screen.status


def test_confirming_the_last_one_clears_the_queue() -> None:
    app, _state, provider, controller = _build_ai_controller()
    controller.toggle_ai_review_mode()
    controller.confirm_ai_grade()
    controller.confirm_ai_grade()

    assert all("confirmed" in text for text in provider.analyses.values() if "AI" in text)
    assert "all confirmed" in app.grade_screen.answer_group


def test_confirming_a_human_grade_says_so() -> None:
    app, _state, _provider, controller = _build_ai_controller()
    controller.open_student_by_index(1)

    controller.confirm_ai_grade()

    assert "not AI-suggested" in app.grade_screen.status


def test_confirming_twice_is_reported_not_repeated() -> None:
    app, _state, provider, controller = _build_ai_controller()
    controller.confirm_ai_grade()
    writes = len(provider.saved_analyses)

    controller.confirm_ai_grade()

    assert app.grade_screen.status == "Already confirmed."
    assert len(provider.saved_analyses) == writes


def test_confirming_works_outside_the_mode() -> None:
    _app, state, provider, controller = _build_ai_controller()

    controller.confirm_ai_grade()

    assert state.ai_review_mode is False
    assert "confirmed" in provider.analyses["s1"]


def test_ai_review_and_group_mode_are_mutually_exclusive() -> None:
    _app, state, _provider, controller = _build_ai_controller()

    controller.toggle_ai_review_mode()
    assert state.ai_review_mode is True
    controller.toggle_group_mode()
    assert state.group_mode is True
    assert state.ai_review_mode is False
    controller.toggle_ai_review_mode()
    assert state.ai_review_mode is True
    assert state.group_mode is False


def test_confirming_invalidates_the_cached_count() -> None:
    app, _state, _provider, controller = _build_ai_controller()
    controller.toggle_ai_review_mode()
    assert "2 left" in app.grade_screen.answer_group

    controller.confirm_ai_grade()

    assert "1 left" in app.grade_screen.answer_group


def test_changing_the_claim_filter_recomputes_the_count() -> None:
    _app, _state, provider, controller = _build_ai_controller()
    controller.toggle_ai_review_mode()
    assert controller._unconfirmed_ai_indices("Q1") == [0, 2]

    provider.analyses["s3"] = "graded by a person"
    controller.refresh_after_claim_change()

    assert controller._unconfirmed_ai_indices("Q1") == [0]


def test_regrading_an_ai_answer_marks_it_overridden() -> None:
    _app, _state, provider, controller = _build_ai_controller()

    controller.on_rubric_selected(0)

    note = provider.analyses["s1"]
    assert "overridden" in note
    assert "confirmed" not in note
    assert "62%" in note and "llama3.1" in note
    assert "Names one of two properties" not in note


def test_an_overridden_answer_leaves_the_confirm_queue() -> None:
    _app, _state, _provider, controller = _build_ai_controller()
    assert controller._unconfirmed_ai_indices("Q1") == [0, 2]

    controller.on_rubric_selected(0)

    assert controller._unconfirmed_ai_indices("Q1") == [2]


def test_grading_to_the_same_grade_counts_as_confirming() -> None:
    _app, _state, provider, controller = _build_ai_controller()

    controller._record_human_review_of_ai(changed=False)

    assert "confirmed" in provider.analyses["s1"]
    assert "overridden" not in provider.analyses["s1"]


def test_grading_to_a_different_grade_counts_as_overriding() -> None:
    _app, _state, provider, controller = _build_ai_controller()

    controller._record_human_review_of_ai(changed=True)

    assert "overridden" in provider.analyses["s1"]


def test_regrading_a_human_graded_answer_writes_no_tag() -> None:
    _app, _state, provider, controller = _build_ai_controller()
    controller.open_student_by_index(1)

    controller.on_rubric_selected(0)

    assert provider.analyses["s2"] == "Partially correct per manual review"


def test_confirm_key_refuses_an_answer_the_grader_replaced() -> None:
    app, _state, _provider, controller = _build_ai_controller()
    controller.on_rubric_selected(0)

    controller.confirm_ai_grade()

    assert "already recorded as yours" in app.grade_screen.status


class AiGroupFakeDataProvider(AiGradedFakeDataProvider):
    _RESPONSES = {"s1": "42", "s2": "42 ", "s3": "seven"}

    def build_question_seeds(self, **kwargs):  # noqa: ANN003, ANN201
        student_item_id = kwargs.get("student_item_id")
        seeds = super().build_question_seeds(**kwargs)
        response = self._RESPONSES.get(student_item_id, "")
        return [
            replace(seed, response_text=response) if seed.question_id == "Q1" else seed
            for seed in seeds
        ]


def _build_ai_group_controller():  # noqa: ANN202
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    provider = AiGroupFakeDataProvider()
    provider.analyses["s2"] = "[AI · 55% · llama3.1] Same answer, same call."
    controller = GradeController(app, state, provider)
    controller.attach_student_refresh(lambda: None)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    state.current_page = PAGE_GRADE
    return app, state, provider, controller


def test_batch_grading_retires_the_ai_tag_on_every_member() -> None:
    _app, _state, provider, controller = _build_ai_group_controller()

    controller.on_rubric_selected(0)
    controller.apply_grade_to_answer_group()

    assert "overridden" in provider.analyses["s1"]
    assert "overridden" in provider.analyses["s2"]
    assert provider.analyses["s3"] == "[AI · 55% · llama3.1] Close enough."


def test_batch_grading_leaves_the_confirm_queue_empty_for_the_group() -> None:
    _app, _state, _provider, controller = _build_ai_group_controller()
    assert controller._unconfirmed_ai_indices("Q1") == [0, 1, 2]

    controller.on_rubric_selected(0)
    controller.apply_grade_to_answer_group()

    assert controller._unconfirmed_ai_indices("Q1") == [2]


def _noop_comment_controller():  # noqa: ANN202
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    provider = PersistentFakeDataProvider()
    controller = GradeController(app, state, provider)
    controller.attach_student_refresh(lambda: None)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    state.current_page = PAGE_GRADE
    return app, state, provider, controller


def test_reemitting_an_unchanged_comment_writes_nothing() -> None:
    _app, state, provider, controller = _noop_comment_controller()
    controller.on_comment_updated("Nice work")
    provider.saved_analyses.clear()
    provider.saved_annotations.clear()
    log_length = len(state.grading_log)

    controller.on_comment_updated("Nice work")

    assert provider.saved_analyses == []
    assert provider.saved_annotations == []
    assert len(state.grading_log) == log_length


def test_a_real_comment_edit_still_writes_logs_and_annotates() -> None:
    _app, state, provider, controller = _noop_comment_controller()

    controller.on_comment_updated("Missing the units")
    assert len(provider.saved_analyses) == 1
    assert provider.saved_analyses[0]["analysis"] == "Missing the units"

    controller.commit_pending_comment()

    assert len(provider.saved_annotations) == 1
    assert len(state.grading_log) == 1


def test_clearing_a_comment_is_a_real_edit() -> None:
    _app, _state, provider, controller = _noop_comment_controller()
    controller.on_comment_updated("typo")
    provider.saved_analyses.clear()

    controller.on_comment_updated("")

    assert [call["analysis"] for call in provider.saved_analyses] == [""]


def test_typing_stages_text_but_defers_the_derived_work() -> None:
    _app, state, provider, controller = _noop_comment_controller()

    for i in range(1, len("Good work") + 1):
        controller.on_comment_updated("Good work"[:i])

    assert len(provider.saved_analyses) == len("Good work")
    assert provider.saved_analyses[-1]["analysis"] == "Good work"
    assert provider.committed_analyses == []
    assert len(state.grading_log) == 0


def test_committing_runs_the_derived_work_exactly_once() -> None:
    _app, state, provider, controller = _noop_comment_controller()
    for i in range(1, len("Good work") + 1):
        controller.on_comment_updated("Good work"[:i])

    controller.commit_pending_comment()

    assert len(provider.committed_analyses) == 1
    assert len(provider.saved_annotations) == 1
    assert len(state.grading_log) == 1


def test_the_audit_entry_records_the_edit_not_the_last_keystroke() -> None:
    _app, _state, provider, controller = _noop_comment_controller()
    controller.on_comment_updated("First note")
    controller.commit_pending_comment()
    provider.committed_analyses.clear()

    for text in ("First note!", "First note!!", "Revised"):
        controller.on_comment_updated(text)
    controller.commit_pending_comment()

    committed = provider.committed_analyses[-1]
    assert committed["previous_analysis"] == "First note"
    assert committed["analysis"] == "Revised"


def test_undo_after_an_edit_restores_the_whole_comment() -> None:
    _app, state, provider, controller = _build_stateful_controller()
    typed = "Nearly there"
    for i in range(1, len(typed) + 1):
        controller.on_comment_updated(typed[:i])
    controller.commit_pending_comment()
    assert provider.analyses[("s1", "Q1")] == typed
    assert len(state.grading_log) == 1

    controller.undo_last_grade_change()

    assert provider.analyses[("s1", "Q1")] == ""


def test_moving_to_another_student_finishes_the_edit_first() -> None:
    _app, _state, provider, controller = _noop_comment_controller()
    controller.on_comment_updated("For the first student")

    controller.next_student()

    assert len(provider.committed_analyses) == 1
    assert provider.committed_analyses[0]["student_item_id"] == "s1"
    assert provider.committed_analyses[0]["analysis"] == "For the first student"


def test_moving_to_another_question_finishes_the_edit_first() -> None:
    _app, _state, provider, controller = _noop_comment_controller()
    controller.on_comment_updated("On question one")

    controller.next_question()

    assert len(provider.committed_analyses) == 1
    assert provider.committed_analyses[0]["question_id"] == "Q1"


def test_returning_to_the_picker_finishes_the_edit_first() -> None:
    _app, _state, provider, controller = _noop_comment_controller()
    controller.on_comment_updated("Then escaped")

    controller.return_to_qa_picker()

    assert len(provider.committed_analyses) == 1


def test_editing_a_second_answer_finishes_the_first_edit() -> None:
    _app, _state, provider, controller = _noop_comment_controller()
    controller.on_comment_updated("First")
    controller.open_student_by_index(1)
    controller.on_comment_updated("Second")

    assert len(provider.committed_analyses) == 1
    assert provider.committed_analyses[0]["student_item_id"] == "s1"
    assert provider.committed_analyses[0]["analysis"] == "First"

    controller.commit_pending_comment()
    assert provider.committed_analyses[1]["student_item_id"] == "s2"
    assert provider.committed_analyses[1]["analysis"] == "Second"


def test_committing_twice_does_nothing_the_second_time() -> None:
    _app, _state, provider, controller = _noop_comment_controller()
    controller.on_comment_updated("Once")
    controller.commit_pending_comment()
    controller.commit_pending_comment()

    assert len(provider.committed_analyses) == 1


def test_committing_with_no_pending_edit_is_a_no_op() -> None:
    _app, state, provider, controller = _noop_comment_controller()

    assert controller.commit_pending_comment() == ""
    assert provider.committed_analyses == []
    assert len(state.grading_log) == 0


class ShuffleAwareFakeDataProvider(FakeDataProvider):
    _COHORT_SIZE = 24

    def __init__(self, submissions_path: str, *, actor: str = "test_grader") -> None:
        self._submissions_path = submissions_path
        self._actor = actor

    @property
    def submissions_path(self) -> str:
        return self._submissions_path

    def grader_actor(self) -> str:
        return self._actor

    def list_students(self, course_id: int | None) -> list[StudentSummary]:
        if course_id is None:
            return []
        return [
            StudentSummary(index, f"Student {index:02d}")
            for index in range(1, self._COHORT_SIZE + 1)
        ]


def _student_order(provider: ShuffleAwareFakeDataProvider) -> list[str]:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"

    controller = GradeController(app, state, provider)
    controller.open_question_picker_for_assignment()
    return [item.item_id for item in state.student_items]


def test_student_order_is_unshuffled_with_no_policy(tmp_path) -> None:  # type: ignore
    provider = ShuffleAwareFakeDataProvider(str(tmp_path))
    order = _student_order(provider)

    expected = [f"s{index}" for index in range(1, provider._COHORT_SIZE + 1)]
    assert order == expected


def test_student_order_is_shuffled_with_shuffle_policy(tmp_path) -> None:  # type: ignore
    from opengrader.core.policy import GradingPolicy, write_policy

    write_policy(tmp_path, GradingPolicy(traversal_shuffle=True))
    provider = ShuffleAwareFakeDataProvider(str(tmp_path))
    order = _student_order(provider)

    on_disk = [f"s{index}" for index in range(1, provider._COHORT_SIZE + 1)]
    assert order != on_disk
    assert sorted(order) == sorted(on_disk)


def test_shuffled_order_is_stable_across_sessions(tmp_path) -> None:  # type: ignore
    from opengrader.core.policy import GradingPolicy, write_policy

    write_policy(tmp_path, GradingPolicy(traversal_shuffle=True))

    first = _student_order(ShuffleAwareFakeDataProvider(str(tmp_path)))
    second = _student_order(ShuffleAwareFakeDataProvider(str(tmp_path)))
    assert first == second


def test_two_graders_get_different_orders(tmp_path) -> None:  # type: ignore
    from opengrader.core.policy import GradingPolicy, write_policy

    write_policy(tmp_path, GradingPolicy(traversal_shuffle=True))

    alice = _student_order(
        ShuffleAwareFakeDataProvider(str(tmp_path), actor="alice")
    )
    bob = _student_order(ShuffleAwareFakeDataProvider(str(tmp_path), actor="bob"))
    assert alice != bob
    assert sorted(alice) == sorted(bob)


def test_shuffle_is_announced_in_the_picker_status(tmp_path) -> None:  # type: ignore
    from opengrader.core.policy import GradingPolicy, write_policy

    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"

    write_policy(tmp_path, GradingPolicy(traversal_shuffle=True))
    controller = GradeController(
        app, state, ShuffleAwareFakeDataProvider(str(tmp_path))
    )
    controller.open_question_picker_for_assignment()

    assert "randomized order" in app.qa_screen.status


class GrowableShuffleFakeDataProvider(ShuffleAwareFakeDataProvider):
    def __init__(self, submissions_path: str, *, actor: str = "test_grader") -> None:
        super().__init__(submissions_path, actor=actor)
        self._extra = 0

    def list_students(self, course_id: int | None) -> list[StudentSummary]:
        students = super().list_students(course_id)
        if course_id is None:
            return students
        students.extend(
            StudentSummary(self._COHORT_SIZE + i, f"New Student {i:02d}")
            for i in range(1, self._extra + 1)
        )
        return students

    def add_student(self) -> None:
        self._extra += 1


def test_fold_in_restores_question_and_student_by_id_across_a_reshuffle(
    tmp_path,
) -> None:
    from opengrader.core.policy import GradingPolicy, write_policy

    write_policy(tmp_path, GradingPolicy(traversal_shuffle=True))
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"

    provider = GrowableShuffleFakeDataProvider(str(tmp_path))
    controller = GradeController(app, state, provider)
    controller.attach_student_refresh(lambda: None)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(1)
    state.current_page = PAGE_GRADE

    current_question_id = state.selection.question_id
    current_student_item_id = state.selection.student_item_id
    assert current_question_id == "Q2"
    assert current_student_item_id

    for _ in range(8):
        provider.add_student()
    controller.note_extraction_progress(added=8, appears_active=True)

    controller._fold_in_pending_submissions()

    assert state.selection.question_id == current_question_id
    assert state.selection.student_item_id == current_student_item_id
    assert len(state.student_items) == provider._COHORT_SIZE + 8


class GrowableFakeDataProvider(FakeDataProvider):
    def __init__(self) -> None:
        self._students = [
            StudentSummary(1, "Tommy Liu"),
            StudentSummary(2, "Andy Cortes"),
        ]

    def list_students(self, course_id: int | None) -> list[StudentSummary]:
        if course_id is None:
            return []
        return list(self._students)

    def add_student(self, student_id: int, name: str) -> None:
        self._students.append(StudentSummary(student_id, name))


def test_return_to_qa_picker_surfaces_newly_folded_in_students() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"

    growable = GrowableFakeDataProvider()
    controller = GradeController(app, state, growable)
    controller.attach_student_refresh(lambda: None)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    state.current_page = PAGE_GRADE
    assert len(state.student_items) == 2

    growable.add_student(3, "Casey Kim")
    controller.note_extraction_progress(added=1, appears_active=True)

    assert len(state.student_items) == 2

    controller.return_to_qa_picker()

    assert len(state.student_items) == 3
    assert any(item.label == "Casey Kim" for item in state.student_items)


def test_progress_label_is_qualified_while_extraction_is_active() -> None:
    app, state, controller = build_controller()
    plain = controller._qa_student_progress_label()
    assert "extraction" not in plain.lower()

    controller.note_extraction_progress(added=0, appears_active=True)
    assert "extraction still running" in controller._qa_student_progress_label()

    controller.note_extraction_progress(added=0, appears_active=False)
    assert controller._qa_student_progress_label() == plain


def test_all_questions_graded_message_is_qualified_while_extraction_is_active() -> None:
    app, state, controller = build_controller()
    state.current_page = PAGE_QA_PICKER
    for question in state.grading_run.questions:
        for student in state.student_items:
            controller._set_qa_student_status(
                question.question_id, student.item_id, graded=True
            )

    controller.note_extraction_progress(added=0, appears_active=True)
    controller.qa_jump_ungraded_question()
    assert "All questions are fully graded for all students!" in app.qa_screen.status
    assert "extraction still running" in app.qa_screen.status

    controller.note_extraction_progress(added=0, appears_active=False)
    controller.qa_jump_ungraded_question()
    assert app.qa_screen.status == "All questions are fully graded for all students!"
