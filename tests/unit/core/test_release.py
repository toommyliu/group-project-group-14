from __future__ import annotations

from datetime import datetime, timezone

import pytest

from opengrader.core.claims import WorkClaim, WorkSlice
from opengrader.core.curve import Curve
from opengrader.core.release import (
    ReadinessKind,
    ReleaseError,
    build_readiness_report,
    build_release,
    compare_release,
    latest_release,
    new_release_id,
    parse_release,
    read_release,
    read_releases,
    render_release,
    resolve_releases_dir,
    write_release,
)
from opengrader.core.submissions.consistency import build_consistency_flags
from opengrader.core.submissions.review import build_review_flags
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)


def _question(
    question_id: str = "q1",
    *,
    points_awarded: float | None = None,
    possible_points: float | None = 10,
    response_text: str = "an answer",
    rubric_labels: tuple[str, ...] = (),
    analysis: str = "",
) -> StudentSubmissionQuestion:
    return StudentSubmissionQuestion(
        question_id=question_id,
        prompt="",
        possible_points=possible_points,
        response_image_path=None,
        response_text=response_text,
        points_awarded=points_awarded,
        rubric_labels=rubric_labels,
        analysis=analysis,
    )


def _doc(
    student_id: str = "012345",
    student_name: str = "Tommy Liu",
    questions: list[StudentSubmissionQuestion] | None = None,
) -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name=student_name,
        student_id=student_id,
        course_code="CS146",
        assignment_title="Quiz 1",
        assignment_version="A",
        source_pdf=f"{student_id or 'unknown'}.pdf",
        student_score="",
        questions=questions if questions is not None else [_question(points_awarded=8)],
    )


def _kinds(report) -> set[ReadinessKind]:
    return {issue.kind for issue in report.issues}


def test_fully_graded_cohort_is_ready():
    report = build_readiness_report([_doc()])
    assert report.ready
    assert report.issues == ()
    assert report.graded_answers == 1
    assert report.total_answers == 1
    assert report.graded_fraction == 1.0


def test_ungraded_answers_block_the_release():
    doc = _doc(questions=[_question("q1", points_awarded=8), _question("q2")])
    report = build_readiness_report([doc])

    assert not report.ready
    issue = report.blocking[0]
    assert issue.kind is ReadinessKind.UNGRADED_ANSWERS
    assert issue.count == 1
    assert report.graded_answers == 1
    assert report.total_answers == 2


def test_ungraded_issue_counts_answers_but_lists_students():
    docs = [
        _doc(student_id=f"s{index}", student_name=f"Student {index}",
             questions=[_question(f"q{n}") for n in range(1, 12)])
        for index in range(2)
    ]
    report = build_readiness_report(docs)
    issue = report.blocking[0]

    assert issue.count == 22
    assert issue.example_total == 2
    assert "and" not in issue.describe().split("e.g.")[1]


def test_missing_student_id_blocks_the_release():
    doc = _doc(student_id="", student_name="Tommy Liu")
    report = build_readiness_report(
        [doc], review_flags=build_review_flags([doc])
    )

    assert not report.ready
    assert ReadinessKind.UNRESOLVED_IDENTITY in _kinds(report)


def test_open_regrade_and_missing_submission_are_warnings_not_blockers():
    from opengrader.core.roster import RosterStudent
    from opengrader.core.submissions.regrade import RegradeRequest

    doc = _doc()
    request = RegradeRequest(
        request_id="rg-1", student_id="012345", reason="Method is valid"
    )
    roster = [
        RosterStudent(student_id="012345", name="Tommy Liu"),
        RosterStudent(student_id="999999", name="Absent Student"),
    ]
    report = build_readiness_report(
        [doc],
        review_flags=build_review_flags(
            [doc], roster=roster, regrade_requests=[request]
        ),
    )

    assert report.ready
    assert ReadinessKind.OPEN_REGRADE_REQUESTS in _kinds(report)
    assert ReadinessKind.MISSING_SUBMISSIONS in _kinds(report)


def test_grades_predating_a_rubric_edit_warn_but_never_block():
    from opengrader.core.analytics.rubric_drift import detect_rubric_drift
    from opengrader.core.history.journal import GradeJournalEntry

    doc = _doc()
    entries = [
        GradeJournalEntry(
            timestamp="2026-08-01T09:00:00+00:00",
            actor="prof_kim",
            kind="grade",
            student_id="012345",
            student_name="Tommy Liu",
            question_id="q1",
            summary="8/10",
            old={"points": None, "rubric": []},
            new={"points": 8.0, "rubric": ["Correct"]},
        ),
        GradeJournalEntry(
            timestamp="2026-08-01T10:00:00+00:00",
            actor="prof_kim",
            kind="rubric",
            student_id="",
            student_name="",
            question_id="q1",
            summary="Rubric edited",
            old={"options": ["Correct", "Incorrect"]},
            new={"options": ["Correct", "Partial", "Incorrect"]},
        ),
    ]

    report = build_readiness_report(
        [doc],
        review_flags=build_review_flags(
            [doc], stale_grades=detect_rubric_drift(entries)
        ),
    )

    assert report.ready
    assert ReadinessKind.STALE_RUBRIC_GRADES in _kinds(report)
    issue = next(i for i in report.issues if i.kind is ReadinessKind.STALE_RUBRIC_GRADES)
    assert issue.severity == "warning"
    assert "q1" in issue.detail


def test_no_stale_grades_no_issue():
    doc = _doc()
    report = build_readiness_report([doc], review_flags=build_review_flags([doc], stale_grades=[]))

    assert ReadinessKind.STALE_RUBRIC_GRADES not in _kinds(report)


def test_low_confidence_ai_grades_warn():
    doc = _doc(
        questions=[_question(points_awarded=8, analysis="[AI · 42%] looks right")]
    )
    report = build_readiness_report(
        [doc], review_flags=build_review_flags([doc])
    )
    assert ReadinessKind.UNCONFIRMED_AI_GRADES in _kinds(report)
    assert report.ready


def test_inconsistent_points_warn_but_equal_points_do_not():
    same_answer = dict(response_text="the same words", possible_points=10)
    docs = [
        _doc("111", "A", [_question(points_awarded=8, **same_answer)]),
        _doc("222", "B", [_question(points_awarded=4, **same_answer)]),
    ]
    report = build_readiness_report(
        docs, consistency_flags=build_consistency_flags(docs)
    )
    assert ReadinessKind.INCONSISTENT_GRADING in _kinds(report)

    agreed = [
        _doc("111", "A", [_question(points_awarded=8, **same_answer)]),
        _doc("222", "B", [_question(points_awarded=8, **same_answer)]),
    ]
    quiet = build_readiness_report(
        agreed, consistency_flags=build_consistency_flags(agreed)
    )
    assert ReadinessKind.INCONSISTENT_GRADING not in _kinds(quiet)


def test_active_claims_and_curve_are_notes():
    claim = WorkClaim(
        claim_id="c1", actor="alice", work=WorkSlice(question_ids=("q1",))
    )
    report = build_readiness_report(
        [_doc()], claims=[claim], curve=Curve("bonus", 5.0)
    )
    assert report.ready
    assert _kinds(report) == {
        ReadinessKind.HELD_WORK_CLAIMS,
        ReadinessKind.CURVE_ACTIVE,
    }
    assert all(issue.severity == "note" for issue in report.issues)


def test_empty_cohort_reports_unknown_rather_than_complete():
    report = build_readiness_report([])
    assert report.graded_fraction is None
    assert report.ready


def test_issues_are_ordered_worst_first():
    doc = _doc(student_id="", questions=[_question("q1"), _question("q2")])
    report = build_readiness_report(
        [doc],
        review_flags=build_review_flags([doc]),
        curve=Curve("bonus", 1.0),
    )
    severities = [issue.severity for issue in report.issues]
    assert severities == sorted(
        severities, key=lambda s: {"blocking": 0, "warning": 1, "note": 2}[s]
    )


def test_release_stores_raw_and_curved_totals():
    doc = _doc(
        questions=[
            _question("q1", points_awarded=8, possible_points=10),
            _question("q2", points_awarded=5, possible_points=10),
        ]
    )
    release = build_release([doc], curve=Curve("bonus", 4.0), label="in class")

    student = release.students[0]
    assert student.total == 13
    assert student.curved_total == 17
    assert student.possible == 20
    assert release.label == "in class"
    assert {q.question_id for q in student.questions} == {"q1", "q2"}


def test_release_caps_the_curved_total_like_the_gradebook():
    doc = _doc(questions=[_question(points_awarded=9, possible_points=10)])
    release = build_release([doc], curve=Curve("bonus", 5.0))
    assert release.students[0].curved_total == 10


def test_release_skips_students_with_no_identity():
    anonymous = _doc(student_id="", student_name="")
    release = build_release([anonymous, _doc()])
    assert release.student_count == 1


def test_release_id_is_date_prefixed_and_unique():
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    first = new_release_id(now=now)
    second = new_release_id(now=now)
    assert first.startswith("rel-2026-08-05-")
    assert first != second


def test_release_round_trips_through_json():
    release = build_release(
        [_doc(questions=[_question("q1", points_awarded=8)])],
        curve=Curve("scale", 1.1, cap_at_max=False),
        label="returned",
    )
    assert parse_release(render_release(release)) == release


def test_parse_release_is_loud_about_unusable_input():
    with pytest.raises(ReleaseError):
        parse_release("{not json")
    with pytest.raises(ReleaseError):
        parse_release('{"release_id": "x"}')
    with pytest.raises(ReleaseError):
        parse_release('{"release_format": 99, "students": []}')


def test_comparison_is_quiet_when_nothing_moved():
    doc = _doc()
    release = build_release([doc])
    assert compare_release(release, [doc]).unchanged


def test_comparison_names_the_question_that_changed():
    before = _doc(questions=[_question("q1", points_awarded=4)])
    release = build_release([before])
    after = _doc(questions=[_question("q1", points_awarded=8)])

    comparison = compare_release(release, [after])
    change = comparison.changes[0]
    assert change.question_id == "q1"
    assert change.kind == "raised"
    assert (change.before, change.after) == (4, 8)
    assert change.delta == 4
    assert comparison.totals[0].delta == 4


def test_comparison_distinguishes_newly_graded_from_raised():
    release = build_release([_doc(questions=[_question("q1")])])
    after = _doc(questions=[_question("q1", points_awarded=3)])
    assert compare_release(release, [after]).changes[0].kind == "newly_graded"


def test_comparison_flags_a_grade_removed_since_release():
    release = build_release([_doc(questions=[_question("q1", points_awarded=3)])])
    after = _doc(questions=[_question("q1")])
    assert compare_release(release, [after]).changes[0].kind == "ungraded"


def test_a_recurve_moves_the_total_without_moving_a_grade():
    doc = _doc(questions=[_question("q1", points_awarded=5, possible_points=10)])
    release = build_release([doc], curve=Curve())

    comparison = compare_release(release, [doc], curve=Curve("bonus", 2.0))
    assert comparison.changes == ()
    assert comparison.totals[0].before == 5
    assert comparison.totals[0].after == 7
    assert comparison.curve_changed

    assert compare_release(release, [doc]).unchanged


def test_comparison_reports_missing_and_new_students_separately():
    release = build_release([_doc("111", "A"), _doc("222", "B")])
    comparison = compare_release(release, [_doc("111", "A"), _doc("333", "C")])

    assert comparison.missing_students == ("222",)
    assert comparison.new_students == ("333",)
    assert not any(change.kind == "lowered" for change in comparison.changes)


def test_comparison_survives_an_id_assigned_after_the_release():
    before = _doc(student_id="", student_name="Tommy Liu",
                  questions=[_question("q1", points_awarded=4)])
    release = build_release([before])
    assert release.student_count == 1

    after = _doc(student_id="", student_name="Tommy Liu",
                 questions=[_question("q1", points_awarded=6)])
    comparison = compare_release(release, [after])
    assert comparison.changes[0].kind == "raised"
    assert comparison.missing_students == ()


def test_write_then_read_a_release(tmp_path):
    release = build_release([_doc()], label="first")
    path = write_release(tmp_path, release)

    assert path.parent == resolve_releases_dir(tmp_path)
    assert read_release(tmp_path, release.release_id) == release
    assert latest_release(tmp_path) == release


def test_a_release_is_never_overwritten(tmp_path):
    release = build_release([_doc()])
    write_release(tmp_path, release)
    with pytest.raises(ReleaseError):
        write_release(tmp_path, release)


def test_read_releases_is_oldest_first_and_skips_damaged_files(tmp_path):
    first = build_release(
        [_doc()], now=datetime(2026, 8, 1, tzinfo=timezone.utc)
    )
    second = build_release(
        [_doc()], now=datetime(2026, 8, 4, tzinfo=timezone.utc)
    )
    write_release(tmp_path, first)
    write_release(tmp_path, second)
    (resolve_releases_dir(tmp_path) / "rel-broken.json").write_text("{oops")

    assert [r.release_id for r in read_releases(tmp_path)] == [
        first.release_id,
        second.release_id,
    ]
    assert latest_release(tmp_path) == second


def test_no_releases_reads_as_nothing_released(tmp_path):
    assert read_releases(tmp_path) == []
    assert latest_release(tmp_path) is None
    assert read_release(tmp_path, "rel-nope") is None


def test_previous_release_turns_into_a_changed_since_warning(tmp_path):
    before = _doc(questions=[_question("q1", points_awarded=4)])
    release = build_release([before])
    after = _doc(questions=[_question("q1", points_awarded=8)])

    report = build_readiness_report([after], previous_release=release)
    assert ReadinessKind.CHANGED_SINCE_RELEASE in _kinds(report)
    assert report.ready
