from opengrader.core.question_types import FREE_RESPONSE, MULTIPLE_CHOICE
from opengrader.core.submissions import (
    ConsistencyKind,
    build_consistency_flags,
)
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)


def _question(
    question_id: str = "q1",
    *,
    question_type: str = FREE_RESPONSE,
    response_text: str = "an answer",
    points_awarded: float | None = None,
    possible_points: float | None = 10,
    rubric_labels: tuple[str, ...] = (),
) -> StudentSubmissionQuestion:
    return StudentSubmissionQuestion(
        question_id=question_id,
        prompt="",
        possible_points=possible_points,
        response_image_path=None,
        response_text=response_text,
        points_awarded=points_awarded,
        rubric_labels=rubric_labels,
        question_type=question_type,
    )


def _doc(
    student_id: str,
    student_name: str,
    questions: list[StudentSubmissionQuestion],
    *,
    assignment_version: str | None = "A",
) -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name=student_name,
        student_id=student_id,
        course_code="CS146",
        assignment_title="Quiz 1",
        assignment_version=assignment_version,
        source_pdf=f"submission-{student_id}.pdf",
        questions=questions,
    )


def test_identical_grades_are_consistent():
    docs = [
        _doc("1", "Ann", [_question(response_text="42", points_awarded=10)]),
        _doc("2", "Bob", [_question(response_text="42", points_awarded=10)]),
    ]
    assert build_consistency_flags(docs) == []


def test_points_divergence_flagged_as_high():
    docs = [
        _doc("1", "Ann", [_question(response_text="42", points_awarded=10)]),
        _doc("2", "Bob", [_question(response_text="42", points_awarded=5)]),
    ]
    flags = build_consistency_flags(docs)
    assert len(flags) == 1
    flag = flags[0]
    assert flag.kind == ConsistencyKind.POINTS_DIVERGENCE
    assert flag.severity == "high"
    assert flag.question_id == "q1"
    assert flag.member_count == 2
    assert flag.point_spread == 5
    # Two distinct outcomes, each with its student.
    assert len(flag.variants) == 2
    outcomes = {(v.points_awarded, v.members[0].student_name) for v in flag.variants}
    assert outcomes == {(10, "Ann"), (5, "Bob")}


def test_cosmetic_answer_differences_still_group():
    # "42." and "  42 " normalize to the same free-response key.
    docs = [
        _doc("1", "Ann", [_question(response_text="42.", points_awarded=10)]),
        _doc("2", "Bob", [_question(response_text="  42 ", points_awarded=4)]),
    ]
    flags = build_consistency_flags(docs)
    assert len(flags) == 1
    assert flags[0].kind == ConsistencyKind.POINTS_DIVERGENCE


def test_genuinely_different_answers_are_not_compared():
    docs = [
        _doc("1", "Ann", [_question(response_text="42", points_awarded=10)]),
        _doc("2", "Bob", [_question(response_text="43", points_awarded=5)]),
    ]
    assert build_consistency_flags(docs) == []


def test_rubric_only_divergence_flagged_as_low():
    # Same points, different rubric selection → low severity.
    docs = [
        _doc(
            "1",
            "Ann",
            [_question(response_text="42", points_awarded=5, rubric_labels=("Partial A",))],
        ),
        _doc(
            "2",
            "Bob",
            [_question(response_text="42", points_awarded=5, rubric_labels=("Partial B",))],
        ),
    ]
    flags = build_consistency_flags(docs)
    assert len(flags) == 1
    assert flags[0].kind == ConsistencyKind.RUBRIC_DIVERGENCE
    assert flags[0].severity == "low"
    assert flags[0].point_spread == 0


def test_rubric_label_order_is_not_significant():
    docs = [
        _doc(
            "1",
            "Ann",
            [_question(response_text="42", points_awarded=8, rubric_labels=("A", "B"))],
        ),
        _doc(
            "2",
            "Bob",
            [_question(response_text="42", points_awarded=8, rubric_labels=("B", "A"))],
        ),
    ]
    assert build_consistency_flags(docs) == []


def test_versions_are_partitioned():
    # Same answer text, same score-conflict, but different versions: no flag,
    # because "42" may be correct on A and wrong on B.
    docs = [
        _doc("1", "Ann", [_question(response_text="42", points_awarded=10)], assignment_version="A"),
        _doc("2", "Bob", [_question(response_text="42", points_awarded=0)], assignment_version="B"),
    ]
    assert build_consistency_flags(docs) == []


def test_same_version_conflict_reported_with_version():
    docs = [
        _doc("1", "Ann", [_question(response_text="42", points_awarded=10)], assignment_version="B"),
        _doc("2", "Bob", [_question(response_text="42", points_awarded=0)], assignment_version="B"),
    ]
    flags = build_consistency_flags(docs)
    assert len(flags) == 1
    assert flags[0].assignment_version == "B"


def test_ungraded_members_are_ignored():
    # Only one graded member — nothing to compare.
    docs = [
        _doc("1", "Ann", [_question(response_text="42", points_awarded=10)]),
        _doc("2", "Bob", [_question(response_text="42", points_awarded=None)]),
    ]
    assert build_consistency_flags(docs) == []


def test_ungraded_third_member_does_not_break_a_real_conflict():
    docs = [
        _doc("1", "Ann", [_question(response_text="42", points_awarded=10)]),
        _doc("2", "Bob", [_question(response_text="42", points_awarded=3)]),
        _doc("3", "Cid", [_question(response_text="42", points_awarded=None)]),
    ]
    flags = build_consistency_flags(docs)
    assert len(flags) == 1
    assert flags[0].member_count == 2


def test_blank_group_skipped_by_default_but_included_on_request():
    docs = [
        _doc("1", "Ann", [_question(response_text="", points_awarded=0)]),
        _doc("2", "Bob", [_question(response_text="   ", points_awarded=5)]),
    ]
    assert build_consistency_flags(docs) == []

    flags = build_consistency_flags(docs, include_blank=True)
    assert len(flags) == 1
    assert flags[0].is_blank is True
    assert flags[0].kind == ConsistencyKind.POINTS_DIVERGENCE


def test_multiple_choice_answers_group_by_choice():
    docs = [
        _doc(
            "1",
            "Ann",
            [_question(question_type=MULTIPLE_CHOICE, response_text="B", points_awarded=10)],
        ),
        _doc(
            "2",
            "Bob",
            [_question(question_type=MULTIPLE_CHOICE, response_text="b)", points_awarded=0)],
        ),
    ]
    flags = build_consistency_flags(docs)
    assert len(flags) == 1
    assert flags[0].kind == ConsistencyKind.POINTS_DIVERGENCE


def test_float_noise_does_not_create_a_false_conflict():
    docs = [
        _doc("1", "Ann", [_question(response_text="42", points_awarded=3.3333333)]),
        _doc("2", "Bob", [_question(response_text="42", points_awarded=3.3333334)]),
    ]
    assert build_consistency_flags(docs) == []


def test_ordering_is_high_severity_and_widest_spread_first():
    docs = [
        # q1: rubric-only divergence (low)
        _doc("1", "Ann", [_question("q1", response_text="x", points_awarded=5, rubric_labels=("A",))]),
        _doc("2", "Bob", [_question("q1", response_text="x", points_awarded=5, rubric_labels=("B",))]),
        # q2: small point spread (high)
        _doc("3", "Cid", [_question("q2", response_text="y", points_awarded=10)]),
        _doc("4", "Dan", [_question("q2", response_text="y", points_awarded=9)]),
        # q3: large point spread (high) — should sort first
        _doc("5", "Eve", [_question("q3", response_text="z", points_awarded=10)]),
        _doc("6", "Fay", [_question("q3", response_text="z", points_awarded=0)]),
    ]
    flags = build_consistency_flags(docs)
    assert [flag.question_id for flag in flags] == ["q3", "q2", "q1"]
    assert flags[0].severity == "high"
    assert flags[-1].severity == "low"


def test_three_way_split_keeps_largest_variant_first():
    docs = [
        _doc("1", "Ann", [_question(response_text="42", points_awarded=10)]),
        _doc("2", "Bob", [_question(response_text="42", points_awarded=10)]),
        _doc("3", "Cid", [_question(response_text="42", points_awarded=5)]),
        _doc("4", "Dan", [_question(response_text="42", points_awarded=0)]),
    ]
    flags = build_consistency_flags(docs)
    assert len(flags) == 1
    variants = flags[0].variants
    assert variants[0].size == 2  # the two 10-pt students lead
    assert variants[0].points_awarded == 10
    assert flags[0].member_count == 4


def test_determinism_across_input_order():
    docs = [
        _doc("2", "Bob", [_question(response_text="42", points_awarded=5)]),
        _doc("1", "Ann", [_question(response_text="42", points_awarded=10)]),
    ]
    first = build_consistency_flags(docs)
    second = build_consistency_flags(list(reversed(docs)))
    assert first == second
