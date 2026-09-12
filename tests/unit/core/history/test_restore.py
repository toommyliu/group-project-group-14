from datetime import datetime, timedelta, timezone

import pytest

from opengrader.core.history.journal import GradeJournalEntry
from opengrader.core.history.restore import (
    AspectValue,
    CellState,
    RestoreError,
    format_aspect_value,
    journal_payload,
    merge_update_for_question,
    parse_restore_time,
    plan_restore,
    replay_journal,
    student_key,
)

BASE = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)


def _entry(
    *,
    minutes: int,
    kind: str = "grade",
    actor: str = "alice",
    sid: str = "1001",
    name: str = "Ada",
    qid: str = "q1",
    old: dict | None = None,
    new: dict | None = None,
) -> GradeJournalEntry:
    return GradeJournalEntry(
        timestamp=(BASE + timedelta(minutes=minutes)).isoformat(),
        actor=actor,
        kind=kind,  # type: ignore[arg-type]
        student_id=sid,
        student_name=name,
        question_id=qid,
        summary="",
        old=old or {},
        new=new or {},
    )


def _grade(points, rubric=()):  # noqa: ANN001
    return {"points": points, "rubric": list(rubric)}


def test_replay_takes_the_last_entry_at_or_before_the_target_time() -> None:
    entries = [
        _entry(minutes=0, old=_grade(None), new=_grade(5, ("Partial",))),
        _entry(minutes=10, old=_grade(5, ("Partial",)), new=_grade(10, ("Correct",))),
    ]
    snapshot = replay_journal(entries, at=BASE + timedelta(minutes=5))
    value = snapshot.cell("1001", "q1").value("grade")
    assert value == AspectValue(points=5.0, rubric=("Partial",))


def test_replay_with_no_target_time_takes_the_latest_state() -> None:
    entries = [
        _entry(minutes=0, old=_grade(None), new=_grade(5)),
        _entry(minutes=10, old=_grade(5), new=_grade(10)),
    ]
    snapshot = replay_journal(entries)
    assert snapshot.cell("1001", "q1").value("grade").points == 10.0


def test_replay_before_the_first_entry_uses_its_old_value_as_the_baseline() -> None:
    entries = [_entry(minutes=10, old=_grade(None), new=_grade(10, ("Correct",)))]
    snapshot = replay_journal(entries, at=BASE)
    value = snapshot.cell("1001", "q1").value("grade")
    assert value == AspectValue(points=None, rubric=())


def test_excluding_an_actor_falls_back_to_the_baseline_when_only_they_touched_it() -> None:
    entries = [_entry(minutes=0, actor="bot", old=_grade(None), new=_grade(0))]
    snapshot = replay_journal(entries, exclude_actors=["bot"])
    assert snapshot.cell("1001", "q1").value("grade").points is None


def test_excluding_an_actor_keeps_a_later_graders_edit() -> None:
    entries = [
        _entry(minutes=0, actor="bob", old=_grade(None), new=_grade(0)),
        _entry(minutes=5, actor="alice", old=_grade(0), new=_grade(9)),
    ]
    snapshot = replay_journal(entries, exclude_actors=["bob"])
    assert snapshot.cell("1001", "q1").value("grade").points == 9.0


def test_actor_exclusion_is_case_insensitive_and_ignores_blanks() -> None:
    entries = [_entry(minutes=0, actor="Bot", old=_grade(None), new=_grade(0))]
    snapshot = replay_journal(entries, exclude_actors=["  bot  ", ""])
    assert snapshot.cell("1001", "q1").value("grade").points is None


def test_comment_and_transcription_aspects_replay_independently() -> None:
    entries = [
        _entry(minutes=0, kind="comment", old={"analysis": ""}, new={"analysis": "Good"}),
        _entry(
            minutes=1,
            kind="transcription",
            old={"response_text": "abc"},
            new={"response_text": "xyz"},
        ),
    ]
    cell = replay_journal(entries).cell("1001", "q1")
    assert cell.value("comment").text == "Good"
    assert cell.value("transcription").text == "xyz"
    assert cell.value("grade") is None
    assert cell.known == frozenset({"comment", "transcription"})


def test_rubric_and_identity_events_are_not_restorable() -> None:
    entries = [
        _entry(minutes=0, kind="rubric", qid="q1", new={"options": ["A"]}),
        _entry(minutes=1, kind="identity", new={"student_id": "1001"}),
    ]
    assert replay_journal(entries).cells == {}


def test_identity_events_alias_earlier_work_onto_todays_id() -> None:
    entries = [
        _entry(minutes=0, sid="", name="Ada", old=_grade(None), new=_grade(7)),
        _entry(
            minutes=5,
            kind="identity",
            sid="1001",
            name="Ada",
            old={"student_id": "", "student_name": "Ada"},
            new={"student_id": "1001", "student_name": "Ada"},
        ),
    ]
    snapshot = replay_journal(entries)
    assert snapshot.cell("1001", "q1").value("grade").points == 7.0
    assert snapshot.cell("name:ada", "q1") is None


def test_entries_with_no_id_and_no_name_are_counted_not_guessed() -> None:
    entries = [_entry(minutes=0, sid="", name="", new=_grade(5))]
    snapshot = replay_journal(entries)
    assert snapshot.cells == {}
    assert snapshot.unattributed_entries == 1


def test_unparseable_timestamps_are_counted_not_crashed_on() -> None:
    bad = GradeJournalEntry(
        timestamp="not-a-time",
        actor="a",
        kind="grade",
        student_id="1001",
        student_name="Ada",
        question_id="q1",
        summary="",
    )
    snapshot = replay_journal([bad, _entry(minutes=0, new=_grade(3))])
    assert snapshot.unattributed_entries == 1
    assert snapshot.cell("1001", "q1").value("grade").points == 3.0


def test_malformed_payload_values_degrade_instead_of_raising() -> None:
    entries = [
        _entry(minutes=0, old={}, new={"points": "not a number", "rubric": "nope"})
    ]
    value = replay_journal(entries).cell("1001", "q1").value("grade")
    assert value == AspectValue(points=None, rubric=())


def test_same_instant_entries_keep_journal_order() -> None:
    entries = [
        _entry(minutes=0, new=_grade(1)),
        _entry(minutes=0, new=_grade(2)),
    ]
    assert replay_journal(entries).cell("1001", "q1").value("grade").points == 2.0


def _current(points=None, rubric=(), analysis="", text=""):  # noqa: ANN001
    return CellState(
        values={
            "grade": AspectValue(points=points, rubric=tuple(rubric)),
            "comment": AspectValue(text=analysis),
            "transcription": AspectValue(text=text),
        }
    )


def test_plan_reports_only_what_actually_differs_on_disk() -> None:
    snapshot = replay_journal([_entry(minutes=0, old=_grade(None), new=_grade(10))])
    plan = plan_restore(
        snapshot,
        {("1001", "q1"): _current(points=10.0)},
        known_students=["1001"],
    )
    assert plan.changes == ()
    assert not plan


def test_plan_diffs_against_the_file_not_the_journals_idea_of_now() -> None:
    snapshot = replay_journal([_entry(minutes=0, old=_grade(None), new=_grade(10))])
    plan = plan_restore(
        snapshot,
        {("1001", "q1"): _current(points=3.0)},
        known_students=["1001"],
    )
    assert len(plan.changes) == 1
    change = plan.changes[0]
    assert change.before.points == 3.0
    assert change.after.points == 10.0
    assert change.aspect == "grade"


def test_plan_can_restore_a_question_back_to_ungraded() -> None:
    snapshot = replay_journal(
        [_entry(minutes=0, actor="bot", old=_grade(None), new=_grade(0, ("Incorrect",)))],
        exclude_actors=["bot"],
    )
    plan = plan_restore(
        snapshot,
        {("1001", "q1"): _current(points=0.0, rubric=("Incorrect",))},
        known_students=["1001"],
    )
    assert plan.changes[0].after == AspectValue(points=None, rubric=())


def test_plan_leaves_alone_a_question_the_journal_never_saw() -> None:
    snapshot = replay_journal([_entry(minutes=0, qid="q1", new=_grade(10))])
    plan = plan_restore(
        snapshot,
        {("1001", "q1"): _current(points=10.0), ("1001", "q9"): _current(points=2.0)},
        known_students=["1001"],
    )
    assert plan.changes == ()


def test_plan_filters_by_student_question_and_aspect() -> None:
    entries = [
        _entry(minutes=0, qid="q1", old=_grade(None), new=_grade(10)),
        _entry(minutes=0, qid="q2", old=_grade(None), new=_grade(10)),
        _entry(
            minutes=0,
            qid="q1",
            kind="comment",
            old={"analysis": ""},
            new={"analysis": "hi"},
        ),
        _entry(minutes=0, sid="2002", name="Bo", qid="q1", old=_grade(None), new=_grade(4)),
    ]
    snapshot = replay_journal(entries)
    current = {
        ("1001", "q1"): _current(),
        ("1001", "q2"): _current(),
        ("2002", "q1"): _current(),
    }
    plan = plan_restore(
        snapshot,
        current,
        students=["1001"],
        questions=["q1"],
        aspects=["grade"],
        known_students=["1001", "2002"],
    )
    assert [(c.student_key, c.question_id, c.aspect) for c in plan.changes] == [
        ("1001", "q1", "grade")
    ]


def test_students_with_no_submission_file_are_reported_not_dropped() -> None:
    snapshot = replay_journal([_entry(minutes=0, sid="9999", new=_grade(5))])
    plan = plan_restore(snapshot, {}, known_students=["1001"])
    assert plan.changes == ()
    assert plan.unresolved == ("9999",)


def test_plan_orders_changes_by_student_then_question_then_aspect() -> None:
    entries = [
        _entry(minutes=0, sid="2", name="Zoe", qid="q2", old=_grade(None), new=_grade(1)),
        _entry(minutes=0, sid="1", name="Ann", qid="q2", old=_grade(None), new=_grade(1)),
        _entry(minutes=0, sid="1", name="Ann", qid="q1", old=_grade(None), new=_grade(1)),
    ]
    snapshot = replay_journal(entries)
    current = {key: _current() for key in [("1", "q1"), ("1", "q2"), ("2", "q2")]}
    plan = plan_restore(snapshot, current, known_students=["1", "2"])
    assert [(c.student_label, c.question_id) for c in plan.changes] == [
        ("Ann", "q1"),
        ("Ann", "q2"),
        ("Zoe", "q2"),
    ]


def test_student_key_prefers_id_then_name_then_nothing() -> None:
    assert student_key("1001", "Ada") == "1001"
    assert student_key("  ", " Ada  Lovelace ") == "name:ada lovelace"
    assert student_key("", "") == ""


@pytest.mark.parametrize(
    "text,expected_delta",
    [("90s", timedelta(seconds=90)), ("30m", timedelta(minutes=30)),
     ("2h", timedelta(hours=2)), ("1d", timedelta(days=1)), ("1w", timedelta(weeks=1))],
)
def test_relative_times_are_measured_back_from_now(text, expected_delta) -> None:  # noqa: ANN001
    assert parse_restore_time(text, now=BASE) == BASE - expected_delta


def test_absolute_times_parse_and_a_date_means_the_start_of_that_day() -> None:
    assert parse_restore_time("2026-08-02T14:30:00+00:00") == datetime(
        2026, 8, 2, 14, 30, tzinfo=timezone.utc
    )
    assert parse_restore_time("2026-08-02").date() in {
        datetime(2026, 8, 1).date(),
        datetime(2026, 8, 2).date(),
    }


def test_an_unreadable_time_says_what_it_accepts() -> None:
    with pytest.raises(RestoreError, match="relative age"):
        parse_restore_time("last tuesday")


def test_aspect_values_render_readably() -> None:
    assert format_aspect_value("grade", AspectValue(points=None)) == "ungraded"
    assert (
        format_aspect_value("grade", AspectValue(points=8.5, rubric=("Partial",)))
        == "8.5 [Partial]"
    )
    assert format_aspect_value("comment", AspectValue(text="  ")) == "(empty)"
    assert format_aspect_value("comment", AspectValue(text="a\nb")) == '"a b"'


def test_float_noise_does_not_count_as_a_change() -> None:
    snapshot = replay_journal([_entry(minutes=0, new=_grade(10.0))])
    plan = plan_restore(
        snapshot,
        {("1001", "q1"): _current(points=10.0 + 1e-9)},
        known_students=["1001"],
    )
    assert plan.changes == ()


def test_a_transcription_replay_carries_its_confidence_and_reader() -> None:
    entries = [
        _entry(
            minutes=0,
            kind="transcription",
            old={
                "response_text": "abc",
                "response_confidence": 42.0,
                "response_source": "tesseract",
            },
            new={
                "response_text": "xyz",
                "response_confidence": 100.0,
                "response_source": "human",
            },
        )
    ]

    value = replay_journal(entries).cell("1001", "q1").value("transcription")

    assert value.text == "xyz"
    assert value.confidence == 100.0
    assert value.source == "human"


def test_an_entry_from_before_provenance_existed_replays_unchanged() -> None:
    entries = [
        _entry(
            minutes=0,
            kind="transcription",
            old={"response_text": "abc"},
            new={"response_text": "xyz"},
        )
    ]

    value = replay_journal(entries).cell("1001", "q1").value("transcription")

    assert value.text == "xyz"
    assert value.confidence is None
    assert value.source is None


def test_provenance_is_not_part_of_what_a_restore_decides() -> None:
    left = AspectValue(text="abc", confidence=42.0, source="tesseract")
    right = AspectValue(text="abc", confidence=100.0, source="human")

    assert left.matches(right, "transcription")


def test_restoring_a_reading_puts_its_reader_back_too() -> None:
    update = merge_update_for_question(
        "q1",
        {
            "transcription": AspectValue(
                text="abc", confidence=42.0, source="tesseract"
            )
        },
    )

    assert update.response_text == "abc"
    assert update.response_confidence == 42.0
    assert update.response_source == "tesseract"
    assert update.clear_response_confidence is False


def test_restoring_a_model_reading_clears_the_confidence_rather_than_keeping_one() -> None:
    update = merge_update_for_question(
        "q1",
        {"transcription": AspectValue(text="abc", confidence=None, source="vision-model")},
    )

    assert update.response_source == "vision-model"
    assert update.clear_response_confidence is True


def test_restoring_a_reading_with_no_recorded_provenance_leaves_the_file_alone() -> None:
    update = merge_update_for_question("q1", {"transcription": AspectValue(text="abc")})

    assert update.response_text == "abc"
    assert update.response_source is None
    assert update.response_confidence is None
    assert update.clear_response_confidence is False


def test_the_journal_payload_omits_provenance_it_does_not_have() -> None:
    assert journal_payload("transcription", AspectValue(text="abc")) == {
        "response_text": "abc"
    }
    assert journal_payload(
        "transcription", AspectValue(text="abc", confidence=42.0, source="tesseract")
    ) == {
        "response_text": "abc",
        "response_confidence": 42.0,
        "response_source": "tesseract",
    }
