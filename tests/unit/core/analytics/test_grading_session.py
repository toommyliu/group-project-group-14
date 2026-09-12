from datetime import datetime, timedelta, timezone

from opengrader.core.analytics import build_grading_session_report
from opengrader.core.history.journal import GradeJournalEntry

_T0 = datetime(2026, 7, 27, 10, 0, 0, tzinfo=timezone.utc)


def _entry(
    offset_seconds: float,
    *,
    actor: str = "ta1",
    kind: str = "grade",
    student_id: str = "s1",
    question_id: str = "q1",
) -> GradeJournalEntry:
    return GradeJournalEntry(
        timestamp=(_T0 + timedelta(seconds=offset_seconds)).isoformat(),
        actor=actor,
        kind=kind,
        student_id=student_id,
        student_name="",
        question_id=question_id,
        summary="",
    )


def test_empty_journal_is_all_zeros() -> None:
    report = build_grading_session_report([])
    assert report.total_events == 0
    assert report.sessions == 0
    assert report.active_seconds == 0.0
    assert report.grades_per_active_hour is None
    assert report.median_seconds_between_events is None
    assert report.actors == ()


def test_active_time_sessions_and_cadence() -> None:
    entries = [
        _entry(0, kind="grade", student_id="s1", question_id="q1"),
        _entry(60, kind="grade", student_id="s1", question_id="q2"),
        _entry(120, kind="comment", student_id="s2", question_id="q1"),
        _entry(720, kind="grade", student_id="s2", question_id="q2"),
        _entry(750, kind="rubric", student_id="s2", question_id="q2"),
    ]
    report = build_grading_session_report(entries, idle_gap_seconds=300.0)

    assert report.total_events == 5
    assert report.grades == 3
    assert report.comments == 1
    assert report.rubric_edits == 1
    assert report.distinct_actors == 1
    assert report.distinct_students == 2

    actor = report.actors[0]
    assert actor.sessions == 2
    assert actor.active_seconds == 150.0
    assert actor.median_seconds_between_events == 60.0
    assert actor.grades_per_active_hour == 72.0


def test_multiple_actors_pool_and_order_by_effort() -> None:
    entries = [
        _entry(0, actor="ta1", kind="grade"),
        _entry(60, actor="ta1", kind="grade"),
        _entry(120, actor="ta1", kind="grade"),
        _entry(30, actor="ta2", kind="grade"),
        _entry(130, actor="ta2", kind="grade"),
    ]
    report = build_grading_session_report(entries, idle_gap_seconds=300.0)

    assert report.distinct_actors == 2
    assert report.total_events == 5
    assert report.sessions == 2
    assert report.active_seconds == 220.0
    assert [a.actor for a in report.actors] == ["ta1", "ta2"]
    assert report.actors[0].active_seconds == 120.0
    assert report.actors[1].active_seconds == 100.0


def test_unparseable_timestamps_do_not_crash_timing() -> None:
    entries = [
        GradeJournalEntry(
            timestamp="not-a-timestamp",
            actor="ta1",
            kind="grade",
            student_id="s1",
            student_name="",
            question_id="q1",
            summary="",
        ),
        _entry(0, actor="ta1", kind="grade"),
    ]
    report = build_grading_session_report(entries)
    assert report.total_events == 2
    assert report.grades == 2
    assert report.active_seconds == 0.0
