from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from opengrader.core.grading.edits import GradeEdit
from opengrader.core.history.journal import read_journal, resolve_history_dir
from opengrader.core.submissions.student_file import (
    StudentSubmissionGradeUpdate,
    load_student_submission_markdown,
    update_student_submission_file,
)
from opengrader.core.workspace.intake import publish_pipeline
from opengrader.core.workspace.models import AssignmentInputs, PipelineOutput
from opengrader.core.workspace.store import Workspace
from opengrader.core.workspace.writeback import (
    WriteBackError,
    apply_write_back,
    locate_submissions_dir,
    plan_write_back,
)


@pytest.fixture
def cls(tmp_path: Path, copy_cs146_sample):
    root = tmp_path / "quiz"
    copy_cs146_sample(root)
    store = Workspace.create(root)
    published = publish_pipeline(
        store,
        store.assignment_id_for_folder(root),
        PipelineOutput(
            qa_markdown=(root / "qa.md").read_text(encoding="utf-8"),
            submissions_dir=str((root / "student-submissions").resolve()),
        ),
    )
    store.save_assignment_inputs(
        AssignmentInputs(
            id=published.assignment.id,
            folder=".",
            scan="",
            output=".",
            submissions="student-submissions",
        )
    )
    first, second = (s.id for s in store.submissions(published.assignment.id))
    files = sorted((root / "student-submissions").rglob("*.submission.md"))
    return {
        "root": root,
        "store": store,
        "aid": published.assignment.id,
        "sids": (first, second),
        "files": files,
    }


def _ws_grade(cls, index: int, question: str, points: float, comment: str = "") -> None:
    store, aid = cls["store"], cls["aid"]
    sid = cls["sids"][index]
    answer = store.answer(aid, sid, question)
    store.save_grade(
        aid,
        sid,
        question,
        GradeEdit(
            request_id=uuid.uuid4(),
            expected_context_revision=answer.context_revision,
            expected_revision=answer.revision,
            rubric_labels=[],
            analysis=comment,
            score_mode="manual",
            points_awarded=points,
        ),
    )


def _file_grade(cls, index: int, question: str, points: float) -> None:
    update_student_submission_file(
        cls["files"][index],
        StudentSubmissionGradeUpdate(question_id=question, points_awarded=points, rubric_labels=()),
    )


def _read(cls, index: int, question: str):
    document = load_student_submission_markdown(cls["files"][index])
    return {q.question_id: q for q in document.questions}[question]


def test_the_imported_folder_comes_from_the_assignment_binding(cls) -> None:
    folder = locate_submissions_dir(cls["store"], cls["aid"])

    assert folder == (cls["root"] / "student-submissions").resolve()


def test_nothing_changed_means_nothing_to_write(cls) -> None:
    plan = plan_write_back(cls["root"], cls["aid"])

    assert plan.nothing_to_do
    assert plan.base_from == "import"


def test_workspace_only_edits_are_written_file_only_edits_are_kept(cls) -> None:
    _ws_grade(cls, 0, "q1", 5, "from the workspace")
    _file_grade(cls, 1, "q2", 3)

    plan = plan_write_back(cls["root"], cls["aid"])
    outcome = apply_write_back(cls["root"], plan)

    assert {(c.question_id, c.aspect) for c in plan.plan.changes} == {("q1", "grade"), ("q1", "comment")}
    assert plan.plan.conflicts == ()
    assert plan.plan.kept_ours == 1
    assert len(outcome.written) == 2
    assert (_read(cls, 0, "q1").points_awarded, _read(cls, 0, "q1").analysis) == (5.0, "from the workspace")
    assert _read(cls, 1, "q2").points_awarded == 3.0


def test_edits_on_both_sides_are_conflicts_and_keep_the_file_unless_told(cls) -> None:
    _ws_grade(cls, 0, "q3", 1)
    _file_grade(cls, 0, "q3", 2)

    plan = plan_write_back(cls["root"], cls["aid"])
    [conflict] = [c for c in plan.plan.conflicts if c.aspect == "grade"]
    apply_write_back(cls["root"], plan)

    assert conflict.reason == "both_edited"
    assert _read(cls, 0, "q3").points_awarded == 2.0


def test_a_conflict_decided_for_the_workspace_is_written(cls) -> None:
    _ws_grade(cls, 0, "q3", 1)
    _file_grade(cls, 0, "q3", 2)

    plan = plan_write_back(cls["root"], cls["aid"])
    decisions = {c.conflict_id: "theirs" for c in plan.plan.conflicts}
    apply_write_back(cls["root"], plan, decisions=decisions)

    assert _read(cls, 0, "q3").points_awarded == 1.0


def test_a_second_write_back_does_not_reask_or_redo_anything(cls) -> None:
    _ws_grade(cls, 0, "q3", 1)
    _file_grade(cls, 0, "q3", 2)
    _ws_grade(cls, 1, "q1", 4)
    apply_write_back(cls["root"], plan_write_back(cls["root"], cls["aid"]))

    again = plan_write_back(cls["root"], cls["aid"])

    assert again.base_from == "last write-back"
    assert again.nothing_to_do


def test_a_later_workspace_edit_is_a_plain_change_after_a_write_back(cls) -> None:
    _ws_grade(cls, 0, "q1", 5)
    apply_write_back(cls["root"], plan_write_back(cls["root"], cls["aid"]))
    _ws_grade(cls, 0, "q1", 6)

    plan = plan_write_back(cls["root"], cls["aid"])

    assert [(c.question_id, c.theirs.points) for c in plan.plan.changes] == [("q1", 6.0)]
    assert plan.plan.conflicts == ()


def test_write_back_is_in_grade_history_under_its_own_actor(cls) -> None:
    _ws_grade(cls, 0, "q1", 5)

    apply_write_back(cls["root"], plan_write_back(cls["root"], cls["aid"]))

    entries = read_journal(resolve_history_dir(cls["root"] / "student-submissions"))
    ours = [e for e in entries if e.actor == "merge:workspace"]
    assert ours and ours[0].question_id == "q1"
    assert ours[0].student_id == "123456789"


def test_a_failed_write_is_offered_again_next_time(cls) -> None:
    _ws_grade(cls, 0, "q1", 5)
    plan = plan_write_back(cls["root"], cls["aid"])
    cls["files"][0].chmod(0o444)
    try:
        outcome = apply_write_back(cls["root"], plan)
    finally:
        cls["files"][0].chmod(0o644)

    assert outcome.failed and not outcome.written
    retry = plan_write_back(cls["root"], cls["aid"])
    assert [(c.question_id, c.aspect) for c in retry.plan.changes] == [("q1", "grade")]


def test_a_student_id_fixed_in_the_file_still_matches_by_path(cls) -> None:
    path = cls["files"][0]
    path.write_text(path.read_text().replace("123456789", "123456780"), encoding="utf-8")
    _ws_grade(cls, 0, "q1", 5)

    plan = plan_write_back(cls["root"], cls["aid"])

    assert [(c.question_id, c.aspect) for c in plan.plan.changes] == [("q1", "grade")]
    assert plan.plan.unknown_questions == ()


def test_blank_placeholders_for_questions_a_file_lacks_are_not_noise(cls) -> None:
    """The workspace adds a blank answer for every question the extraction
    missed (here q5a, cut from the first file). Blank, they are not reported;
    graded, they are, since a file cannot gain a question."""
    path = cls["files"][0]
    before, remainder = path.read_text().split("- q5a:", 1)
    _, after = remainder.split("- q5b:", 1)
    path.write_text(before + "- q5b:" + after)
    assert plan_write_back(cls["root"], cls["aid"]).plan.unknown_questions == ()

    _ws_grade(cls, 0, "q5a", 2)

    assert plan_write_back(cls["root"], cls["aid"]).plan.unknown_questions == ("123456789 · q5a",)


def test_a_deleted_file_is_reported_not_fatal(cls) -> None:
    cls["files"][1].unlink()
    _ws_grade(cls, 0, "q1", 5)

    plan = plan_write_back(cls["root"], cls["aid"])

    assert len(plan.missing_files) == 1
    assert [c.question_id for c in plan.plan.changes] == ["q1"]


def test_an_assignment_made_in_the_web_app_has_no_folder(cls) -> None:
    web = cls["store"].create_assignment("---\ntitle: Web only\n---\n- (1 points) Q\n")

    with pytest.raises(WriteBackError, match="no available source submissions folder"):
        plan_write_back(cls["root"], web.id)


def test_a_moved_folder_is_not_guessed(cls) -> None:
    (cls["root"] / "student-submissions").rename(cls["root"] / "renamed")

    with pytest.raises(WriteBackError, match="moved"):
        plan_write_back(cls["root"], cls["aid"])


def test_the_sync_state_lives_in_the_workspace_not_the_class_files(cls) -> None:
    _ws_grade(cls, 0, "q1", 5)
    apply_write_back(cls["root"], plan_write_back(cls["root"], cls["aid"]))

    state = cls["root"] / ".opengrader" / "write-back" / f"{cls['aid']}.json"
    body = json.loads(state.read_text(encoding="utf-8"))
    assert body["version"] == 1 and body["cells"]
