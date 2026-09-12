import math
import random

import pytest

from opengrader.core.qa_file import parse_qa_markdown
from opengrader.core.question_types import FREE_RESPONSE, MULTIPLE_CHOICE, NUMERIC
from opengrader.core.submissions.answer_patterns import (
    DEFAULT_MIN_SHARED_WRONG,
    ExclusionReason,
    ObjectiveResponse,
    PatternKind,
    PatternMember,
    build_answer_pattern_report,
    build_answer_pattern_report_for_documents,
    match_probability,
    objective_responses_from_documents,
)
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)

_OPTIONS = ("A", "B", "C", "D")


def _member(index: int) -> PatternMember:
    return PatternMember(
        student_id=f"{index:03d}",
        student_name=f"Student {index:03d}",
        source_pdf=f"s{index}.pdf",
    )


def _responses(
    answers: dict[int, list[str]],
    *,
    correct: str = "A",
    version: str | None = None,
) -> list[ObjectiveResponse]:
    out: list[ObjectiveResponse] = []
    for index, given in answers.items():
        for position, answer in enumerate(given, start=1):
            if not answer:
                continue
            out.append(
                ObjectiveResponse(
                    member=_member(index),
                    question_id=f"q{position}",
                    assignment_version=version,
                    answer=answer,
                    correct=answer == correct,
                    expected_answer=correct,
                )
            )
    return out


def _cohort(*, questions: int, students: int, seed: int = 11) -> dict[int, list[str]]:
    rng = random.Random(seed)
    return {
        student: [
            "A" if rng.random() < 0.65 else rng.choice(_OPTIONS[1:])
            for _ in range(questions)
        ]
        for student in range(students)
    }


def test_match_probability_is_the_without_replacement_estimate() -> None:
    assert match_probability(["B", "B", "C", "D"]) == pytest.approx(1 / 6)


def test_match_probability_is_one_when_every_wrong_student_agrees() -> None:
    assert match_probability(["B", "B", "B"]) == 1.0


def test_match_probability_is_zero_when_nobody_agrees() -> None:
    assert match_probability(["B", "C", "D"]) == 0.0


def test_match_probability_needs_two_answers() -> None:
    assert match_probability(["B"]) == 0.0
    assert match_probability([]) == 0.0


def test_true_false_questions_are_excluded_as_single_distractor() -> None:
    answers = {index: ["FALSE" if index % 2 else "TRUE"] for index in range(10)}
    report = build_answer_pattern_report(_responses(answers, correct="TRUE"))

    assert report.analyzed_questions == ()
    assert not report.analyzed
    (excluded,) = report.excluded_questions
    assert excluded.question_id == "q1"
    assert excluded.reason is ExclusionReason.SINGLE_DISTRACTOR


def test_question_nobody_got_wrong_is_excluded() -> None:
    answers = {index: ["A"] for index in range(10)}
    report = build_answer_pattern_report(_responses(answers))

    (excluded,) = report.excluded_questions
    assert excluded.reason is ExclusionReason.NO_WRONG_ANSWERS
    assert excluded.wrong_count == 0


def test_question_with_too_few_wrong_answers_is_excluded() -> None:
    answers = {0: ["B"], 1: ["C"], 2: ["A"], 3: ["A"], 4: ["A"]}
    report = build_answer_pattern_report(_responses(answers))

    (excluded,) = report.excluded_questions
    assert excluded.reason is ExclusionReason.TOO_FEW_WRONG
    assert excluded.wrong_count == 2


def test_blank_and_unsettleable_answers_never_become_records() -> None:
    document = StudentSubmissionDocument(
        student_name="Ann",
        student_id="1",
        course_code="CS146",
        assignment_title="Quiz",
        assignment_version="A",
        source_pdf="a.pdf",
        questions=(
            StudentSubmissionQuestion(
                question_id="q1",
                prompt="",
                possible_points=1,
                response_image_path=None,
                response_text="",
                points_awarded=None,
                rubric_labels=(),
                question_type=MULTIPLE_CHOICE,
            ),
            StudentSubmissionQuestion(
                question_id="q2",
                prompt="",
                possible_points=1,
                response_image_path=None,
                response_text="an essay",
                points_awarded=None,
                rubric_labels=(),
                question_type=FREE_RESPONSE,
            ),
        ),
    )
    qa_doc = parse_qa_markdown(
        "\n".join(
            [
                "---",
                "title: Quiz",
                "---",
                "",
                "- (1 points) Pick one",
                "  - **Choices**",
                "    - A) right",
                "    - B) wrong",
                "  - **Correct**",
                "    - A",
                "",
                "- (1 points) Explain",
                "  - **Correct**",
                "    - something else entirely",
                "",
            ]
        )
    )

    assert objective_responses_from_documents([document], qa_doc) == []


def test_flags_a_pair_that_shares_every_wrong_answer() -> None:
    answers = _cohort(questions=40, students=40)
    answers[1] = list(answers[0])
    report = build_answer_pattern_report(_responses(answers))

    assert len(report.flags) == 1
    flag = report.flags[0]
    assert flag.kind is PatternKind.SHARED_WRONG_ANSWERS
    assert {flag.a.student_id, flag.b.student_id} == {"000", "001"}
    assert flag.shared_wrong_count == flag.both_wrong_count
    assert flag.z_score > report.threshold
    assert flag.expected_shared < flag.shared_wrong_count


def test_an_honest_cohort_produces_nothing() -> None:
    report = build_answer_pattern_report(_responses(_cohort(questions=40, students=40)))

    assert report.flags == ()
    assert report.analyzed_questions
    assert report.pairs_compared > 0


def test_min_shared_wrong_gates_an_otherwise_extreme_score() -> None:
    distractors = "BCDEFG"
    answers: dict[int, list[str]] = {}
    for student in range(2, 32):
        answers[student] = [
            distractors[(student + question) % len(distractors)]
            for question in range(20)
        ]
    for student in (0, 1):
        answers[student] = ["A"] * 20
        answers[student][0] = "H"
        answers[student][1] = "H"

    responses = _responses(answers)
    pair = {"000", "001"}

    relaxed = build_answer_pattern_report(responses, threshold=3.0, min_shared_wrong=2)
    scored = [flag for flag in relaxed.flags if {flag.a.student_id, flag.b.student_id} == pair]
    assert scored
    assert scored[0].shared_wrong_count == 2
    assert scored[0].z_score == pytest.approx(3.78, abs=0.05)

    strict = build_answer_pattern_report(responses, threshold=3.0, min_shared_wrong=3)
    assert not [
        flag for flag in strict.flags if {flag.a.student_id, flag.b.student_id} == pair
    ]


def test_versions_are_never_compared_against_each_other() -> None:
    answers = _cohort(questions=40, students=20)
    twin = {index + 100: list(row) for index, row in answers.items()}

    a = _responses(answers, version="A")
    b = _responses(twin, version="B")
    report = build_answer_pattern_report(a + b)

    assert report.flags == ()


def test_expected_by_chance_scales_with_the_pairs_tested() -> None:
    report = build_answer_pattern_report(_responses(_cohort(questions=40, students=40)))
    tail = 0.5 * math.erfc(report.threshold / math.sqrt(2.0))

    assert report.expected_by_chance == pytest.approx(report.pairs_compared * tail)


def test_severity_splits_extreme_from_notable() -> None:
    answers = _cohort(questions=60, students=40)
    answers[1] = list(answers[0])
    report = build_answer_pattern_report(_responses(answers))

    (flag,) = report.flags
    assert flag.severity == "high"
    assert flag.severity_rank == 0


def test_harpp_hogan_index_is_shared_over_differences() -> None:
    answers = _cohort(questions=40, students=40)
    answers[1] = list(answers[0])
    answers[1][2] = "B" if answers[0][2] != "B" else "C"
    answers[1][5] = "B" if answers[0][5] != "B" else "C"
    (flag,) = build_answer_pattern_report(_responses(answers)).flags

    assert flag.difference_count == 2
    assert flag.harpp_hogan_index == pytest.approx(flag.shared_wrong_count / 2)


def test_harpp_hogan_index_is_none_when_the_pair_never_differed() -> None:
    answers = _cohort(questions=40, students=40)
    answers[1] = list(answers[0])
    (flag,) = build_answer_pattern_report(_responses(answers)).flags

    assert flag.difference_count == 0
    assert flag.harpp_hogan_index is None


def test_ordering_is_total_and_deterministic() -> None:
    answers = _cohort(questions=50, students=40)
    answers[1] = list(answers[0])
    answers[3] = list(answers[2])
    responses = _responses(answers)

    first = build_answer_pattern_report(responses)
    second = build_answer_pattern_report(list(reversed(responses)))

    assert len(first.flags) == 2
    assert [(f.a.student_id, f.b.student_id) for f in first.flags] == [
        (f.a.student_id, f.b.student_id) for f in second.flags
    ]
    assert first.flags[0].z_score >= first.flags[1].z_score or (
        first.flags[0].severity_rank < first.flags[1].severity_rank
    )


def test_shared_answers_lead_with_the_rarest() -> None:
    answers = _cohort(questions=40, students=40)
    answers[1] = list(answers[0])
    (flag,) = build_answer_pattern_report(_responses(answers)).flags

    probabilities = [shared.match_probability for shared in flag.shared_wrong]
    assert probabilities == sorted(probabilities)


def test_empty_input_reports_nothing_analyzed() -> None:
    report = build_answer_pattern_report([])

    assert report.flags == ()
    assert not report.analyzed
    assert report.pairs_compared == 0
    assert report.expected_by_chance == 0.0


def _mc_qa(questions: int) -> str:
    lines = ["---", "title: Pattern", "---", ""]
    for number in range(1, questions + 1):
        lines += [
            f"- (1 points) Question {number}?",
            "  - **Choices**",
            *[f"    - {option}) option {option}" for option in _OPTIONS],
            "  - **Correct**",
            "    - A",
            "",
        ]
    return "\n".join(lines)


def _mc_document(index: int, answers: list[str]) -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name=f"Student {index:03d}",
        student_id=f"{index:03d}",
        course_code="CS146",
        assignment_title="Quiz",
        assignment_version="A",
        source_pdf=f"s{index}.pdf",
        questions=tuple(
            StudentSubmissionQuestion(
                question_id=f"q{position}",
                prompt="",
                possible_points=1,
                response_image_path=None,
                response_text=answer,
                points_awarded=None,
                rubric_labels=(),
                question_type=MULTIPLE_CHOICE,
            )
            for position, answer in enumerate(answers, start=1)
        ),
    )


def test_adapter_grades_by_the_same_rule_the_auto_grader_uses() -> None:
    qa_doc = parse_qa_markdown(_mc_qa(2))
    documents = [_mc_document(0, ["b.", "A"]), _mc_document(1, ["B)", "A"])]

    responses = objective_responses_from_documents(documents, qa_doc)
    q1 = [response for response in responses if response.question_id == "q1"]

    assert {response.answer for response in q1} == {"B"}
    assert all(response.correct is False for response in q1)
    assert all(response.expected_answer == "A" for response in q1)


def test_report_for_documents_flags_a_copied_paper() -> None:
    qa_doc = parse_qa_markdown(_mc_qa(40))
    answers = _cohort(questions=40, students=40)
    answers[1] = list(answers[0])
    documents = [_mc_document(index, row) for index, row in answers.items()]

    report = build_answer_pattern_report_for_documents(documents, qa_doc)

    assert len(report.flags) == 1
    assert {report.flags[0].a.student_id, report.flags[0].b.student_id} == {"000", "001"}


def test_numeric_answers_never_reach_the_statistic() -> None:
    qa_doc = parse_qa_markdown(
        "\n".join(
            ["---", "title: Quiz", "---", "", "- (1 points) How many?", "  - **Correct**", "    - 42", ""]
        )
    )
    documents = [
        StudentSubmissionDocument(
            student_name=f"S{index}",
            student_id=str(index),
            course_code="CS",
            assignment_title="Quiz",
            assignment_version="A",
            source_pdf=f"s{index}.pdf",
            questions=(
                StudentSubmissionQuestion(
                    question_id="q1",
                    prompt="",
                    possible_points=1,
                    response_image_path=None,
                    response_text="17",
                    points_awarded=None,
                    rubric_labels=(),
                    question_type=NUMERIC,
                ),
            ),
        )
        for index in range(6)
    ]

    assert objective_responses_from_documents(documents, qa_doc) == []


def test_default_min_shared_wrong_is_the_documented_gate() -> None:
    assert DEFAULT_MIN_SHARED_WRONG == 3
