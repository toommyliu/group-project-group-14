from datetime import datetime, timedelta, timezone

from opengrader.core.analytics.grader_agreement import (
    GradedAnswer,
    answers_from_graded_copies,
    answers_from_journal,
    build_grader_agreement_report,
)
from opengrader.core.history.journal import GradeJournalEntry
from opengrader.core.question_types import FREE_RESPONSE
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)

_START = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)


def _answer(
    actor: str,
    student: str,
    question: str,
    points: float,
    *,
    possible: float | None = 10.0,
    answer_key: str = "",
    minute: int | None = None,
) -> GradedAnswer:
    return GradedAnswer(
        actor=actor,
        student_key=student,
        question_id=question,
        points_awarded=points,
        possible_points=possible,
        answer_key=answer_key,
        at=None if minute is None else _START + timedelta(minutes=minute),
    )


def _question(
    question_id: str = "q1",
    *,
    points_awarded: float | None = None,
    possible_points: float = 10.0,
    response_text: str = "an answer",
) -> StudentSubmissionQuestion:
    return StudentSubmissionQuestion(
        question_id=question_id,
        prompt="Prompt",
        possible_points=possible_points,
        response_image_path="",
        response_text=response_text,
        points_awarded=points_awarded,
        rubric_labels=(),
        analysis="",
        question_type=FREE_RESPONSE,
    )


def _document(
    student_id: str,
    *,
    name: str = "",
    questions: tuple[StudentSubmissionQuestion, ...] = (),
) -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name=name or f"Student {student_id}",
        student_id=student_id,
        course_code="CS146",
        assignment_title="Quiz 1",
        assignment_version="",
        source_pdf=f"{student_id}.pdf",
        student_score=None,
        questions=questions or (_question(),),
    )


# --------------------------------------------------------------------------- #
# Leniency
# --------------------------------------------------------------------------- #


def test_leniency_is_measured_against_each_question_not_raw_means():
    """A grader given the hard question must not look harsh for it.

    `alice` marks only q1 (which everyone marks low) and `bob` only q2 (which
    everyone marks high). Their raw means differ by 40 points, but neither is
    lenient — they were handed different questions, and the residual says so.
    """
    answers = [
        _answer("alice", "s1", "q1", 4.0),
        _answer("alice", "s2", "q1", 4.0),
        _answer("carol", "s3", "q1", 4.0),
        _answer("bob", "s4", "q2", 8.0),
        _answer("bob", "s5", "q2", 8.0),
        _answer("carol", "s6", "q2", 8.0),
    ]

    report = build_grader_agreement_report(answers)
    by_actor = {grader.actor: grader for grader in report.graders}

    assert by_actor["alice"].mean_percent == 40.0
    assert by_actor["bob"].mean_percent == 80.0
    # Neither deviates from how their question was marked overall.
    assert by_actor["alice"].mean_residual == 0.0
    assert by_actor["bob"].mean_residual == 0.0


def test_a_genuinely_harsh_grader_shows_a_negative_residual():
    answers = [
        _answer("alice", "s1", "q1", 9.0),
        _answer("alice", "s2", "q1", 9.0),
        _answer("alice", "s3", "q1", 9.0),
        _answer("bob", "s4", "q1", 5.0),
    ]

    report = build_grader_agreement_report(answers)
    by_actor = {grader.actor: grader for grader in report.graders}

    assert by_actor["bob"].mean_residual < 0
    assert by_actor["alice"].mean_residual > 0


def test_zero_point_questions_are_excluded_rather_than_counted_as_zero():
    answers = [
        _answer("alice", "s1", "q1", 5.0),
        _answer("alice", "s2", "q2", 0.0, possible=0.0),
    ]

    report = build_grader_agreement_report(answers)

    assert report.total_answers == 2
    assert report.unweighted_answers == 1
    assert report.graders[0].answers == 1
    assert report.graders[0].mean_percent == 50.0


def test_report_is_safe_on_no_input():
    report = build_grader_agreement_report([])

    assert report.graders == ()
    assert report.total_answers == 0
    assert report.has_overlap is False


# --------------------------------------------------------------------------- #
# Pairwise agreement
# --------------------------------------------------------------------------- #


def test_same_work_pairs_compare_graders_on_identical_submissions():
    """The calibration shape: every grader marks the same students."""
    answers = []
    for index in range(4):
        student = f"s{index}"
        answers.append(_answer("alice", student, "q1", 8.0))
        answers.append(_answer("bob", student, "q1", 6.0))

    report = build_grader_agreement_report(answers, min_pair_samples=4)

    assert report.has_overlap
    assert len(report.same_work_pairs) == 1
    pair = report.same_work_pairs[0]
    assert (pair.a, pair.b) == ("alice", "bob")
    assert pair.samples == 4
    assert pair.sufficient is True
    assert pair.exact_agreement == 0.0
    assert pair.mean_absolute_gap == 20.0
    # alice awarded 8/10 where bob awarded 6/10, so alice is the generous one.
    assert pair.mean_signed_gap == 20.0
    assert pair.more_generous == "alice"


def test_identical_grading_reports_full_agreement_and_no_generosity():
    answers = []
    for index in range(3):
        answers.append(_answer("alice", f"s{index}", "q1", 7.0))
        answers.append(_answer("bob", f"s{index}", "q1", 7.0))

    pair = build_grader_agreement_report(answers).same_work_pairs[0]

    assert pair.exact_agreement == 1.0
    assert pair.mean_absolute_gap == 0.0
    assert pair.more_generous == ""


def test_undersized_pairs_are_reported_but_marked_provisional():
    """Two answers in common is not a finding about a colleague."""
    answers = [
        _answer("alice", "s1", "q1", 10.0),
        _answer("bob", "s1", "q1", 0.0),
    ]

    pair = build_grader_agreement_report(answers, min_pair_samples=12).same_work_pairs[0]

    assert pair.samples == 1
    assert pair.sufficient is False


def test_sufficient_pairs_outrank_a_noisier_wider_gap():
    answers = [
        # A big gap on a single shared answer.
        _answer("alice", "s1", "q1", 10.0),
        _answer("bob", "s1", "q1", 0.0),
    ]
    # A smaller but well-evidenced gap between two other graders.
    for index in range(6):
        answers.append(_answer("carol", f"s{index + 2}", "q1", 6.0))
        answers.append(_answer("dave", f"s{index + 2}", "q1", 5.0))

    pairs = build_grader_agreement_report(answers, min_pair_samples=5).same_work_pairs

    assert pairs[0].sufficient is True
    assert {pairs[0].a, pairs[0].b} == {"carol", "dave"}


def test_same_answer_overlap_works_on_a_divided_cohort():
    """Two graders never share a student, but do share an answer."""
    answers = [
        _answer("alice", "s1", "q1", 8.0, answer_key="binary search halves it"),
        _answer("alice", "s2", "q1", 8.0, answer_key="binary search halves it"),
        _answer("bob", "s3", "q1", 4.0, answer_key="binary search halves it"),
        _answer("bob", "s4", "q1", 4.0, answer_key="binary search halves it"),
    ]

    report = build_grader_agreement_report(answers, min_pair_samples=2)

    # No student was marked twice, so the strong basis finds nothing...
    assert report.same_work_pairs == ()
    # ...but the same answer was, and that is comparable.
    assert len(report.same_answer_pairs) == 1
    pair = report.same_answer_pairs[0]
    assert pair.basis == "same_answer"
    assert pair.more_generous == "alice"
    assert pair.mean_signed_gap == 40.0


def test_blank_answers_never_manufacture_same_answer_overlap():
    """Everyone who wrote nothing wrote the same nothing — that is not overlap."""
    answers = [
        _answer("alice", "s1", "q1", 0.0, answer_key=""),
        _answer("bob", "s2", "q1", 5.0, answer_key=""),
    ]

    assert build_grader_agreement_report(answers).same_answer_pairs == ()


def test_a_regrade_compares_the_grade_that_stands():
    """Two entries for one answer must not be read as two separate decisions."""
    answers = [
        _answer("alice", "s1", "q1", 2.0, minute=0),
        _answer("alice", "s1", "q1", 8.0, minute=5),  # alice corrected herself
        _answer("bob", "s1", "q1", 8.0, minute=1),
    ]

    pair = build_grader_agreement_report(answers).same_work_pairs[0]

    assert pair.samples == 1
    assert pair.exact_agreement == 1.0


# --------------------------------------------------------------------------- #
# Drift
# --------------------------------------------------------------------------- #


def test_drift_detects_a_grader_loosening_over_the_session():
    answers = []
    # A second grader keeps every question's norm honest.
    for index in range(12):
        answers.append(_answer("bob", f"b{index}", "q1", 5.0))
    for index in range(12):
        # alice starts harsh and ends generous.
        points = 3.0 if index < 6 else 9.0
        answers.append(_answer("alice", f"s{index}", "q1", points, minute=index))

    report = build_grader_agreement_report(answers, min_drift_samples=10)
    alice = next(g for g in report.graders if g.actor == "alice")

    assert alice.drift is not None
    assert alice.drift.samples == 12
    assert alice.drift.early_residual < alice.drift.late_residual
    assert alice.drift.delta > 0


def test_drift_controls_for_which_question_was_graded_when():
    """Grading an easy question late must not read as loosening.

    Graders work question by question, so the late half is nearly always a
    different question from the early half. Residuals are what stop that from
    being reported as drift.
    """
    answers = []
    for index in range(10):
        answers.append(_answer("bob", f"b{index}", "q1", 3.0))
        answers.append(_answer("bob", f"c{index}", "q2", 9.0))
    for index in range(10):
        answers.append(_answer("alice", f"s{index}", "q1", 3.0, minute=index))
    for index in range(10):
        answers.append(_answer("alice", f"t{index}", "q2", 9.0, minute=20 + index))

    report = build_grader_agreement_report(answers, min_drift_samples=10)
    alice = next(g for g in report.graders if g.actor == "alice")

    assert alice.drift is not None
    assert abs(alice.drift.delta) < 1e-6


def test_drift_is_none_rather_than_zero_when_it_cannot_be_measured():
    """An unmeasurable drift must stay visibly unknown, not read as 'steady'."""
    untimed = [_answer("alice", f"s{i}", "q1", 5.0) for i in range(40)]
    too_few = [_answer("bob", f"s{i}", "q1", 5.0, minute=i) for i in range(3)]

    report = build_grader_agreement_report(untimed + too_few, min_drift_samples=20)
    by_actor = {grader.actor: grader for grader in report.graders}

    assert by_actor["alice"].drift is None
    assert by_actor["bob"].drift is None


# --------------------------------------------------------------------------- #
# Adapters
# --------------------------------------------------------------------------- #


def _entry(
    actor: str,
    student_id: str,
    question_id: str,
    points: float | None,
    *,
    kind: str = "grade",
    minute: int = 0,
) -> GradeJournalEntry:
    return GradeJournalEntry(
        timestamp=(_START + timedelta(minutes=minute)).isoformat(),
        actor=actor,
        kind=kind,
        student_id=student_id,
        student_name=f"Student {student_id}",
        question_id=question_id,
        summary="",
        old={},
        new={"points": points, "rubric": ["Correct"]},
    )


def test_journal_adapter_reads_grade_events_with_the_questions_weight():
    documents = [
        _document("001", questions=(_question("q1", possible_points=20.0),)),
        _document("002", questions=(_question("q1", possible_points=20.0),)),
    ]
    entries = [
        _entry("alice", "001", "q1", 15.0),
        _entry("bob", "002", "q1", 10.0, minute=1),
        # Not a mark: no severity to read from either of these.
        _entry("alice", "001", "q1", None, kind="comment"),
        _entry("alice", "001", "q1", None),
    ]

    adaptation = answers_from_journal(entries, documents)

    assert adaptation.unmatched == 0
    assert len(adaptation.answers) == 2
    first = adaptation.answers[0]
    assert first.actor == "alice"
    assert first.possible_points == 20.0
    assert first.percent == 75.0
    assert first.rubric_labels == ("Correct",)
    assert first.at is not None


def test_journal_adapter_counts_entries_whose_submission_is_gone():
    adaptation = answers_from_journal([_entry("alice", "404", "q1", 5.0)], [])

    assert adaptation.answers == ()
    assert adaptation.unmatched == 1


def test_graded_copies_adapter_turns_one_copy_per_grader_into_decisions():
    graded = _question("q1", points_awarded=7.0, possible_points=10.0)
    ungraded = _question("q2", points_awarded=None)

    answers = answers_from_graded_copies(
        [
            ("alice", {"001": _document("001", questions=(graded, ungraded))}),
            ("bob", {"001": _document("001", questions=(graded, ungraded))}),
        ]
    )

    # Only the graded question is a decision; the untouched one is simply absent.
    assert len(answers) == 2
    assert {answer.actor for answer in answers} == {"alice", "bob"}
    assert all(answer.question_id == "q1" for answer in answers)
    assert all(answer.student_key == "001" for answer in answers)
    # A returned copy records the grade, not when it was made.
    assert all(answer.at is None for answer in answers)


def test_calibration_shape_end_to_end():
    """Two graders, same sample, one clearly harsher — the norming result."""

    def copy_for(points: float):
        return {
            f"00{index}": _document(
                f"00{index}",
                questions=(_question("q1", points_awarded=points, possible_points=10.0),),
            )
            for index in range(1, 6)
        }

    answers = answers_from_graded_copies(
        [("alice", copy_for(9.0)), ("bob", copy_for(6.0))]
    )
    report = build_grader_agreement_report(answers, min_pair_samples=5)

    assert report.distinct_graders == 2
    pair = report.same_work_pairs[0]
    assert pair.samples == 5
    assert pair.sufficient is True
    assert pair.more_generous == "alice"
    assert pair.mean_absolute_gap == 30.0
    # No timestamps in a returned copy, so drift stays unknown.
    assert all(grader.drift is None for grader in report.graders)
