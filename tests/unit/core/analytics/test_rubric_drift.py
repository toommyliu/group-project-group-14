"""Rubric drift: grades awarded before the rubric under them last moved."""

from __future__ import annotations

from opengrader.core.analytics.rubric_drift import (
    detect_rubric_drift,
    find_rubric_changes,
)
from opengrader.core.history.journal import GradeJournalEntry
from opengrader.core.submissions.review import ReviewReason, flag_rubric_drift
from opengrader.core.submissions.student_file import StudentSubmissionDocument


def _grade(
    *,
    at: str,
    student_id: str = "s1",
    student_name: str = "Tommy Liu",
    question_id: str = "q1",
    actor: str = "prof_kim",
) -> GradeJournalEntry:
    return GradeJournalEntry(
        timestamp=at,
        actor=actor,
        kind="grade",
        student_id=student_id,
        student_name=student_name,
        question_id=question_id,
        summary="graded",
        old={"points": None, "rubric": []},
        new={"points": 8.0, "rubric": ["Correct"]},
    )


def _rubric(
    *,
    at: str,
    question_id: str = "q1",
    old: list[str] | None = None,
    new: list[str] | None = None,
    actor: str = "prof_kim",
) -> GradeJournalEntry:
    return GradeJournalEntry(
        timestamp=at,
        actor=actor,
        kind="rubric",
        student_id="",
        student_name="",
        question_id=question_id,
        summary="Rubric edited",
        old={"options": old if old is not None else ["Correct", "Incorrect"]},
        new={
            "options": new
            if new is not None
            else ["Correct", "Partial - method", "Incorrect"]
        },
    )


def test_grade_before_a_rubric_edit_is_stale():
    stale = detect_rubric_drift([_grade(at="2026-08-01T10:00:00+00:00"), _rubric(at="2026-08-01T11:00:00+00:00")])

    assert [(item.student_id, item.question_id) for item in stale] == [("s1", "q1")]
    assert stale[0].change.added == ("Partial - method",)


def test_grade_after_a_rubric_edit_is_not_stale():
    stale = detect_rubric_drift([_rubric(at="2026-08-01T10:00:00+00:00"), _grade(at="2026-08-01T11:00:00+00:00")])

    assert stale == []


def test_a_regrade_after_the_edit_clears_an_earlier_stale_grade():
    """Rule 3: the *latest* grade decides, not the first."""
    entries = [
        _grade(at="2026-08-01T09:00:00+00:00"),
        _rubric(at="2026-08-01T10:00:00+00:00"),
        _grade(at="2026-08-01T11:00:00+00:00"),
    ]

    assert detect_rubric_drift(entries) == []


def test_a_rubric_save_that_changed_nothing_is_not_a_change():
    """Rule 2: opening the editor and saving must not mark a cohort stale."""
    entries = [
        _grade(at="2026-08-01T09:00:00+00:00"),
        _rubric(
            at="2026-08-01T10:00:00+00:00",
            old=["Correct", "Incorrect"],
            new=["Correct", "Incorrect"],
        ),
    ]

    assert find_rubric_changes(entries) == {}
    assert detect_rubric_drift(entries) == []


def test_only_the_edited_question_is_examined():
    entries = [
        _grade(at="2026-08-01T09:00:00+00:00", question_id="q1"),
        _grade(at="2026-08-01T09:00:00+00:00", question_id="q2"),
        _rubric(at="2026-08-01T10:00:00+00:00", question_id="q1"),
    ]

    assert [item.question_id for item in detect_rubric_drift(entries)] == ["q1"]


def test_a_grade_with_no_journal_entry_is_never_flagged():
    """Rule 1: only on evidence. Nothing recorded ⇒ nothing asserted."""
    assert detect_rubric_drift([_rubric(at="2026-08-01T10:00:00+00:00")]) == []


def test_the_latest_of_several_edits_is_the_one_compared_against():
    entries = [
        _rubric(at="2026-08-01T09:00:00+00:00", new=["Correct", "Partial", "Incorrect"]),
        _grade(at="2026-08-01T10:00:00+00:00"),
        _rubric(
            at="2026-08-01T11:00:00+00:00",
            old=["Correct", "Partial", "Incorrect"],
            new=["Correct", "Partial", "Bonus", "Incorrect"],
        ),
    ]

    stale = detect_rubric_drift(entries)

    assert len(stale) == 1
    # Flagged against the *second* edit, which is the one it predates.
    assert stale[0].change.added == ("Bonus",)


def test_an_unattributable_grade_is_skipped():
    entries = [
        _grade(at="2026-08-01T09:00:00+00:00", student_id="", student_name=""),
        _rubric(at="2026-08-01T10:00:00+00:00"),
    ]

    assert detect_rubric_drift(entries) == []


def test_a_tool_actor_grade_is_still_stale():
    """A batch auto-grade is a grade: it was awarded under the old options too."""
    entries = [
        _grade(at="2026-08-01T09:00:00+00:00", actor="automated-grading:llm"),
        _rubric(at="2026-08-01T10:00:00+00:00"),
    ]

    assert len(detect_rubric_drift(entries)) == 1


def test_ordering_is_total_and_stable():
    entries = [
        _grade(at="2026-08-01T09:00:00+00:00", student_id="s2", student_name="Zoe", question_id="q2"),
        _grade(at="2026-08-01T09:00:00+00:00", student_id="s1", student_name="Andy", question_id="q2"),
        _grade(at="2026-08-01T09:00:00+00:00", student_id="s3", student_name="Bea", question_id="q1"),
        _rubric(at="2026-08-01T10:00:00+00:00", question_id="q1"),
        _rubric(at="2026-08-01T10:00:00+00:00", question_id="q2"),
    ]

    stale = detect_rubric_drift(entries)

    assert [(item.question_id, item.label) for item in stale] == [
        ("q1", "Bea"),
        ("q2", "Andy"),
        ("q2", "Zoe"),
    ]


def test_describe_names_what_moved():
    change = find_rubric_changes(
        [
            _rubric(
                at="2026-08-01T10:00:00+00:00",
                old=["Correct", "Incorrect"],
                new=["Correct", "Partial"],
            )
        ]
    )["q1"]

    described = change.describe()
    assert "added 'Partial'" in described
    assert "removed 'Incorrect'" in described


def test_describe_falls_back_when_only_points_moved():
    """The journal records labels only, so a points-only edit must still speak."""
    change = find_rubric_changes(
        [
            _rubric(
                at="2026-08-01T10:00:00+00:00",
                old=["Correct", "Incorrect"],
                new=["Incorrect", "Correct"],
            )
        ]
    )["q1"]

    assert change.describe() == "rubric options changed"


# ── The review-queue flag built from the drift ──────────────────────────────


def _document(student_id: str = "s1", name: str = "Tommy Liu") -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name=name,
        student_id=student_id,
        course_code="CS146",
        assignment_title="Quiz 1",
        assignment_version=None,
        source_pdf="submission-0001.pdf",
    )


def test_flag_carries_the_question_and_enriches_from_the_submission():
    stale = detect_rubric_drift(
        [_grade(at="2026-08-01T09:00:00+00:00"), _rubric(at="2026-08-01T10:00:00+00:00")]
    )

    flags = flag_rubric_drift(stale, [_document()])

    assert len(flags) == 1
    flag = flags[0]
    assert flag.reason is ReviewReason.GRADED_BEFORE_RUBRIC_CHANGE
    assert flag.severity == "medium"
    assert flag.question_id == "q1"
    assert flag.source_pdf == "submission-0001.pdf"
    assert "Partial - method" in flag.detail


def test_flag_survives_a_missing_submission():
    """A grade that was handed out is worth naming even if the file is gone."""
    stale = detect_rubric_drift(
        [_grade(at="2026-08-01T09:00:00+00:00"), _rubric(at="2026-08-01T10:00:00+00:00")]
    )

    flags = flag_rubric_drift(stale, [])

    assert len(flags) == 1
    assert flags[0].student_id == "s1"
    assert flags[0].source_pdf == ""
