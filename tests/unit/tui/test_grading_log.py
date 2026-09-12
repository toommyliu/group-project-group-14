from datetime import datetime

from opengrader.tui.controllers.grade_controller import GradeController
from opengrader.tui.formatting import format_grading_log_row
from opengrader.tui.state import PAGE_GRADE, PAGE_QA_PICKER, TuiWorkflowState
from opengrader.tui.state.grading_log import GradeRevert, GradingLog, GradingLogEntry

from .fakes import FakeDataProvider
from .test_grade_controller import (
    FakeApp,
    PersistentFakeDataProvider,
    RubricPersistentFakeDataProvider,
    _seed,
)


def _entry(
    kind: str = "grade",
    question_id: str = "Q1",
    student_item_id: str = "s1",
    summary: str = "10/10 pts",
    revert: GradeRevert | None = None,
) -> GradingLogEntry:
    return GradingLogEntry(
        kind=kind,  # type: ignore[arg-type]
        question_id=question_id,
        summary=summary,
        student_item_id=student_item_id,
        student_name="Tommy Liu" if student_item_id else "",
        student_display_id=student_item_id,
        revert=revert,
    )


def test_grading_log_returns_entries_latest_first() -> None:
    log = GradingLog()
    log.record(_entry(question_id="Q1"))
    log.record(_entry(question_id="Q2"))

    assert [entry.question_id for entry in log.entries_latest_first()] == ["Q2", "Q1"]


def test_grading_log_coalesces_consecutive_edits_to_same_grade() -> None:
    log = GradingLog()
    log.record(_entry(summary="10/10 pts"))
    log.record(_entry(summary="5/10 pts"))

    assert len(log) == 1
    assert log.entries_latest_first()[0].summary == "5/10 pts"


def test_coalescing_preserves_the_original_revert() -> None:
    log = GradingLog()
    first = GradeRevert(kind="grade", student_item_id="s1", question_id="Q1")
    later = GradeRevert(
        kind="grade",
        student_item_id="s1",
        question_id="Q1",
        rubric_labels=("Incorrect",),
    )
    log.record(_entry(summary="10/10 pts", revert=first))
    log.record(_entry(summary="5/10 pts", revert=later))

    assert len(log) == 1
    top = log.entries_latest_first()[0]
    assert top.revert is first
    assert top.summary == "5/10 pts"


def test_peek_and_drop_undoable_entries() -> None:
    log = GradingLog()
    revert = GradeRevert(kind="grade", student_item_id="s1", question_id="Q1")
    log.record(_entry(summary="0/10 pts", revert=revert))
    log.record(_entry(kind="rubric", student_item_id="", summary="Rubric edited"))

    peeked = log.peek_last_undoable()
    assert peeked is not None and peeked.revert is revert

    version_before = log.version
    assert log.drop_entry(peeked) is True
    assert log.version == version_before + 1
    assert log.peek_last_undoable() is None
    assert [entry.kind for entry in log.entries_latest_first()] == ["rubric"]
    assert log.drop_entry(peeked) is False


def test_grading_log_does_not_coalesce_across_kind_student_or_question() -> None:
    log = GradingLog()
    log.record(_entry(kind="grade"))
    log.record(_entry(kind="comment"))
    log.record(_entry(kind="comment", student_item_id="s2"))
    log.record(_entry(kind="comment", student_item_id="s2", question_id="Q2"))

    assert len(log) == 4


def test_grading_log_caps_entry_count() -> None:
    log = GradingLog(max_entries=3)
    for index in range(5):
        log.record(_entry(question_id=f"Q{index}"))

    assert len(log) == 3
    assert [entry.question_id for entry in log.entries_latest_first()] == [
        "Q4",
        "Q3",
        "Q2",
    ]


def test_grading_log_filters_by_student_and_question() -> None:
    log = GradingLog()
    log.record(_entry(student_item_id="s1", question_id="Q1"))
    log.record(_entry(student_item_id="s2", question_id="Q1"))
    log.record(_entry(kind="rubric", student_item_id="", question_id="Q1"))

    assert [e.student_item_id for e in log.entries_for_student("s1")] == ["s1"]
    assert len(log.entries_for_question("Q1")) == 3
    assert log.entries_for_question("Q2") == []


def test_grading_log_version_increments_on_record() -> None:
    log = GradingLog()
    assert log.version == 0
    log.record(_entry())
    log.record(_entry())
    assert log.version == 2


def _build_controller(provider) -> tuple[FakeApp, TuiWorkflowState, GradeController]:
    app = FakeApp()
    state = TuiWorkflowState()
    state.selection.course_id = 1
    state.selection.assignment_id = 1
    state.selection_labels["course"] = "CMPE 195"
    state.selection_labels["assignment"] = "HW 1"
    controller = GradeController(app, state, provider)
    controller.open_question_picker_for_assignment()
    controller.open_question_by_index(0)
    state.current_page = PAGE_GRADE
    return app, state, controller


def test_saved_grade_is_recorded_in_grading_log() -> None:
    _app, state, controller = _build_controller(PersistentFakeDataProvider())

    controller.on_rubric_selected(1)

    entries = state.grading_log.entries_latest_first()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.kind == "grade"
    assert entry.question_id == "Q1"
    assert entry.student_item_id == "s1"
    assert entry.student_name == "Tommy Liu"
    assert entry.student_display_id == "s1"
    assert entry.summary == "0/0 pts · Incorrect"


def test_unsupported_provider_records_no_log_entries() -> None:
    _app, state, controller = _build_controller(FakeDataProvider())

    controller.on_rubric_selected(1)
    controller.on_comment_updated("Nice work.")

    assert len(state.grading_log) == 0


def test_comment_edits_record_once_and_coalesce_keystrokes() -> None:
    _app, state, controller = _build_controller(PersistentFakeDataProvider())

    controller.on_comment_updated("N")
    controller.on_comment_updated("Ni")
    controller.on_comment_updated("Nice work.")

    assert len(state.grading_log) == 0
    controller.commit_pending_comment()

    entries = state.grading_log.entries_latest_first()
    assert len(entries) == 1
    assert entries[0].kind == "comment"
    assert "Nice work." in entries[0].summary


def test_rebinding_unchanged_comment_is_not_logged() -> None:
    _app, state, controller = _build_controller(PersistentFakeDataProvider())
    controller.on_comment_updated("Nice work.")
    controller.commit_pending_comment()
    version_after_edit = state.grading_log.version

    controller.on_comment_updated("Nice work.")
    controller.commit_pending_comment()

    assert state.grading_log.version == version_after_edit
    assert len(state.grading_log) == 1


def test_failed_grade_save_is_not_logged() -> None:
    class FailingProvider(PersistentFakeDataProvider):
        def save_question_grade(self, **kwargs: object) -> None:
            raise ValueError("disk full")

    _app, state, controller = _build_controller(FailingProvider())

    controller.on_rubric_selected(1)

    assert len(state.grading_log) == 0


def test_rubric_edit_is_logged_without_student_attribution() -> None:
    _app, state, controller = _build_controller(RubricPersistentFakeDataProvider())
    current = state.grading_run.current_question
    assert current is not None
    original_options = [
        _seed(option.label, option.points_delta)
        for option in current.rubric_options
    ]

    controller.on_rubric_options_saved(
        [_seed("Correct", 0.0), _seed("Incorrect", -10.0)],
        original_options=original_options,
    )

    entries = state.grading_log.entries_latest_first()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.kind == "rubric"
    assert entry.question_id == "Q1"
    assert entry.student_item_id == ""
    assert entry.summary == "Rubric edited · 3 grades updated"


def test_selected_log_entry_opens_its_question_and_student() -> None:
    app, state, controller = _build_controller(PersistentFakeDataProvider())

    controller.on_grading_log_entry_selected(
        _entry(question_id="Q2", student_item_id="s2")
    )

    assert app.page == PAGE_GRADE
    assert state.selection.question_id == "Q2"
    assert state.selection.student_item_id == "s2"
    assert state.active_question_index == 1
    assert state.active_student_index == 1


def test_selected_log_entry_without_student_opens_first_ungraded() -> None:
    app, state, controller = _build_controller(PersistentFakeDataProvider())
    controller.on_rubric_selected(1)

    controller.on_grading_log_entry_selected(
        _entry(kind="rubric", question_id="Q1", student_item_id="")
    )

    assert app.page == PAGE_GRADE
    assert state.selection.question_id == "Q1"
    assert state.active_student_index == 1


def test_selected_log_entry_for_unknown_question_stays_on_picker() -> None:
    app, state, controller = _build_controller(PersistentFakeDataProvider())
    app.page = PAGE_QA_PICKER

    controller.on_grading_log_entry_selected(_entry(question_id="Q99"))

    assert app.page == PAGE_QA_PICKER
    assert "not part of the current assignment" in app.qa_screen.status


def test_format_grading_log_row_respects_anonymity() -> None:
    entry = GradingLogEntry(
        kind="grade",
        question_id="q1",
        summary="8/10 pts · Correct",
        student_item_id="s1",
        student_name="Tommy Liu",
        student_display_id="1234",
        timestamp=datetime(2026, 7, 19, 14, 32),
    )

    anonymous = format_grading_log_row(entry, teacher_mode=False).plain
    teacher = format_grading_log_row(entry, teacher_mode=True).plain

    assert anonymous == "14:32 ✓ Question 1 · 1234 · 8/10 pts · Correct"
    assert teacher == "14:32 ✓ Question 1 · Tommy Liu · 8/10 pts · Correct"


def test_format_grading_log_row_omits_missing_student() -> None:
    entry = GradingLogEntry(
        kind="rubric",
        question_id="q2",
        summary="Rubric edited · 3 grades updated",
        timestamp=datetime(2026, 7, 19, 9, 5),
    )

    row = format_grading_log_row(entry, teacher_mode=True).plain

    assert row == "09:05 ± Question 2 · Rubric edited · 3 grades updated"
