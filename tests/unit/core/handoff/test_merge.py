from __future__ import annotations

from datetime import datetime, timedelta, timezone

from opengrader.core.handoff.merge import (
    baseline_from_journal,
    merge_actor,
    plan_merge,
    resolve_conflicts,
)
from opengrader.core.history.journal import GradeJournalEntry
from opengrader.core.history.restore import AspectValue, CellState

BASE_TIME = datetime(2026, 8, 4, 9, 0, 0, tzinfo=timezone.utc)


def _cell(points=None, rubric=(), comment="", text=""):  # noqa: ANN001, ANN202
    return CellState(
        values={
            "grade": AspectValue(points=points, rubric=tuple(rubric)),
            "comment": AspectValue(text=comment),
            "transcription": AspectValue(text=text),
        }
    )


def _side(**cells):  # noqa: ANN003, ANN202
    return {("s1", qid): cell for qid, cell in cells.items()}


def test_incoming_edit_is_taken_when_ours_never_moved() -> None:
    plan = plan_merge(_side(q1=_cell()), _side(q1=_cell(5, ("Correct",))), _side(q1=_cell()))
    assert len(plan.changes) == 1
    change = plan.changes[0]
    assert (change.question_id, change.aspect, change.reason) == (
        "q1",
        "grade",
        "incoming_edit",
    )
    assert change.theirs.points == 5.0
    assert not plan.conflicts


def test_our_edit_is_kept_when_theirs_is_stale() -> None:
    plan = plan_merge(
        _side(q1=_cell(8, ("Correct",))), _side(q1=_cell()), _side(q1=_cell())
    )
    assert not plan.changes
    assert not plan.conflicts
    assert plan.kept_ours == 1


def test_both_edited_differently_is_a_conflict() -> None:
    plan = plan_merge(
        _side(q1=_cell(8, ("Correct",))),
        _side(q1=_cell(2, ("Partial",))),
        _side(q1=_cell()),
    )
    assert not plan.changes
    assert len(plan.conflicts) == 1
    conflict = plan.conflicts[0]
    assert conflict.reason == "both_edited"
    assert (conflict.ours.points, conflict.theirs.points) == (8.0, 2.0)


def test_both_reaching_the_same_answer_is_not_a_conflict() -> None:
    plan = plan_merge(
        _side(q1=_cell(5, ("Correct",))),
        _side(q1=_cell(5, ("Correct",))),
        _side(q1=_cell()),
    )
    assert not plan.changes and not plan.conflicts
    assert plan.already_agree == 3


def test_every_aspect_is_decided_separately() -> None:
    plan = plan_merge(
        _side(q1=_cell(points=None, comment="mine")),
        _side(q1=_cell(points=4, comment="theirs")),
        _side(q1=_cell()),
    )
    assert [change.aspect for change in plan.changes] == ["grade"]
    assert [conflict.aspect for conflict in plan.conflicts] == ["comment"]


def test_without_a_baseline_a_blank_is_filled() -> None:
    plan = plan_merge(_side(q1=_cell()), _side(q1=_cell(3, ("Partial",))), None)
    assert [change.reason for change in plan.changes] == ["filled_blank"]
    assert not plan.conflicts


def test_without_a_baseline_an_existing_grade_is_never_overwritten() -> None:
    plan = plan_merge(_side(q1=_cell(9)), _side(q1=_cell(3)), None)
    assert not plan.changes
    assert [conflict.reason for conflict in plan.conflicts] == ["no_baseline"]


def test_a_whitespace_only_comment_still_counts_as_blank() -> None:
    plan = plan_merge(
        _side(q1=_cell(comment="   ")), _side(q1=_cell(comment="see me")), None
    )
    assert [change.aspect for change in plan.changes] == ["comment"]


def test_a_question_we_do_not_have_is_reported_not_written() -> None:
    plan = plan_merge({}, _side(q9=_cell(5)), {})
    assert not plan.changes and not plan.conflicts
    assert plan.unknown_questions == ("s1 · q9",)


def test_unmatched_students_are_carried_into_the_plan() -> None:
    plan = plan_merge({}, {}, {}, unmatched_students=["og-deadbeef", "og-deadbeef"])
    assert plan.unmatched_students == ("og-deadbeef",)


def test_labels_name_the_student_in_every_row() -> None:
    plan = plan_merge(
        _side(q1=_cell()),
        _side(q1=_cell(5)),
        _side(q1=_cell()),
        labels={"s1": "Ada Lovelace"},
    )
    assert plan.changes[0].student_label == "Ada Lovelace"


def test_resolve_conflicts_only_produces_work_for_theirs() -> None:
    plan = plan_merge(_side(q1=_cell(8)), _side(q1=_cell(2)), _side(q1=_cell()))
    assert resolve_conflicts(plan.conflicts, policy="ours") == ()
    taken = resolve_conflicts(plan.conflicts, policy="theirs")
    assert [change.theirs.points for change in taken] == [2.0]


def test_a_per_conflict_decision_overrides_the_blanket_policy() -> None:
    plan = plan_merge(
        _side(q1=_cell(8), q2=_cell(8)),
        _side(q1=_cell(2), q2=_cell(3)),
        _side(q1=_cell(), q2=_cell()),
    )
    only_q2 = {
        conflict.conflict_id: "theirs"
        for conflict in plan.conflicts
        if conflict.question_id == "q2"
    }
    taken = resolve_conflicts(plan.conflicts, policy="ours", decisions=only_q2)
    assert [change.question_id for change in taken] == ["q2"]


def test_conflict_ids_are_stable_and_unique() -> None:
    plan = plan_merge(
        _side(q1=_cell(8, ("A",), comment="mine")),
        _side(q1=_cell(2, ("B",), comment="theirs")),
        _side(q1=_cell()),
    )
    ids = [conflict.conflict_id for conflict in plan.conflicts]
    assert len(ids) == len(set(ids))
    assert all(conflict.question_id in cid for conflict, cid in zip(plan.conflicts, ids))


def _entry(minutes: int, old, new, *, qid="q1", kind="grade"):  # noqa: ANN001, ANN202
    return GradeJournalEntry(
        timestamp=(BASE_TIME + timedelta(minutes=minutes)).isoformat(),
        actor="alice",
        kind=kind,
        student_id="s1",
        student_name="",
        question_id=qid,
        summary="",
        old=old,
        new=new,
    )


def test_baseline_from_journal_takes_the_earliest_recorded_old_value() -> None:
    entries = [
        _entry(0, {"points": None, "rubric": []}, {"points": 3, "rubric": []}),
        _entry(5, {"points": 3, "rubric": []}, {"points": 7, "rubric": []}),
    ]
    baseline = baseline_from_journal(entries)
    assert baseline[("s1", "q1")].value("grade") == AspectValue(points=None, rubric=())


def test_a_journal_baseline_makes_the_merge_take_their_edit() -> None:
    entries = [_entry(0, {"points": None, "rubric": []}, {"points": 6, "rubric": []})]
    plan = plan_merge(
        _side(q1=_cell(points=None)),
        _side(q1=_cell(points=6)),
        baseline_from_journal(entries),
    )
    assert [change.reason for change in plan.changes] == ["incoming_edit"]


def test_an_answer_nobody_touched_has_no_baseline_and_no_change() -> None:
    baseline = baseline_from_journal([_entry(0, {}, {}, qid="q1")])
    assert ("s1", "q2") not in baseline


def test_merge_actor_is_a_stable_slug() -> None:
    assert merge_actor("Quiz 1 for Alice") == "merge:quiz-1-for-alice"
    assert merge_actor("") == "merge:copy"


def test_anonymous_labels_fall_back_to_the_student_key() -> None:
    plan = plan_merge(
        _side(q1=_cell()),
        _side(q1=_cell(5)),
        _side(q1=_cell()),
        labels={"s1": "Ada Lovelace"},
    )
    assert plan.changes[0].student_label == "Ada Lovelace"

    anonymous = plan_merge(
        _side(q1=_cell()),
        _side(q1=_cell(5)),
        _side(q1=_cell()),
        labels={"s1": "s1"},
    )
    assert anonymous.changes[0].student_label == "s1"
    assert "Ada" not in anonymous.changes[0].describe()
