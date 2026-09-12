from __future__ import annotations

from opengrader.core.analytics import build_outcome_report
from opengrader.core.submissions import parse_student_submission_markdown


def _doc(student_id: str, marks: list[tuple[str, float | None, float | None]]):  # noqa: ANN202
    blocks = ""
    for question_id, possible, awarded in marks:
        blocks += (
            f"- {question_id}:\n"
            "  - Question type: Free Response\n"
            "  - Prompt: p\n"
            "  - Response text: answer\n"
            f"  - Possible points: {'' if possible is None else possible:g}\n"
            if possible is not None
            else (
                f"- {question_id}:\n"
                "  - Question type: Free Response\n"
                "  - Prompt: p\n"
                "  - Response text: answer\n"
                "  - Possible points: \n"
            )
        )
        blocks += f"  - Points: {'' if awarded is None else f'{awarded:g}'}\n"
        blocks += "  - Rubric:\n  - Analysis:\n\n"
    return parse_student_submission_markdown(
        "---\n"
        f'student_name: "Student {student_id}"\n'
        f'student_id: "{student_id}"\n'
        "course_code: CS146\n"
        'assignment_title: "Quiz"\n'
        "assignment_version: null\n"
        f'source_pdf: "{student_id}.pdf"\n'
        'score: ""\n'
        "---\n\n\n" + blocks
    )


TAGS = {"q1": ("CLO1",), "q2": ("CLO1", "CLO2"), "q3": ("CLO2",)}


def test_mastery_is_points_earned_over_points_tagged() -> None:
    report = build_outcome_report(
        [_doc("s1", [("q1", 10, 9), ("q2", 10, 6), ("q3", 10, 10)])], TAGS
    )
    clo1 = report.outcome("CLO1")
    assert clo1 is not None
    assert clo1.students[0].mastery == 0.75
    clo2 = report.outcome("CLO2")
    assert clo2 is not None
    assert clo2.students[0].mastery == 0.8


def test_a_question_counts_toward_every_outcome_it_carries() -> None:
    report = build_outcome_report([_doc("s1", [("q2", 10, 5)])], TAGS)
    assert [o.outcome for o in report.outcomes] == ["CLO1", "CLO2"]
    assert report.outcome("CLO1").students[0].possible_points == 10.0
    assert report.outcome("CLO2").students[0].possible_points == 10.0


def test_points_not_questions_are_the_unit() -> None:
    tags = {"essay": ("CLO1",), "defn": ("CLO1",)}
    report = build_outcome_report(
        [_doc("s1", [("essay", 20, 10), ("defn", 2, 2)])], tags
    )
    assert round(report.outcome("CLO1").students[0].mastery, 4) == round(12 / 22, 4)


def test_untagged_questions_are_excluded_but_counted_as_coverage() -> None:
    report = build_outcome_report(
        [_doc("s1", [("q1", 10, 10), ("q9", 10, 0)])], {"q1": ("CLO1",)}
    )
    assert report.outcome("CLO1").students[0].possible_points == 10.0
    assert report.untagged_points == 10.0
    assert report.tagged_share == 0.5


def test_an_ungraded_question_adds_neither_earned_nor_possible() -> None:
    report = build_outcome_report(
        [_doc("s1", [("q1", 10, 8), ("q2", 10, None)])], TAGS
    )
    clo1 = report.outcome("CLO1")
    assert clo1.students[0].possible_points == 10.0
    assert clo1.students[0].mastery == 0.8
    assert clo1.students[0].questions_graded == 1


def test_a_student_with_nothing_graded_is_not_measured() -> None:
    report = build_outcome_report(
        [_doc("s1", [("q1", 10, 9)]), _doc("s2", [("q1", 10, None)])],
        {"q1": ("CLO1",)},
    )
    clo1 = report.outcome("CLO1")
    assert clo1.students_measured == 1
    assert clo1.mastery_rate == 1.0


def test_mastery_rate_counts_students_over_the_bar() -> None:
    docs = [
        _doc("s1", [("q1", 10, 10)]),
        _doc("s2", [("q1", 10, 7)]),
        _doc("s3", [("q1", 10, 6)]),
    ]
    report = build_outcome_report(docs, {"q1": ("CLO1",)})
    clo1 = report.outcome("CLO1")
    assert clo1.students_measured == 3
    assert clo1.students_mastered == 2
    assert clo1.mastery_rate == 2 / 3


def test_the_mastery_bar_is_a_parameter_not_a_policy() -> None:
    docs = [_doc("s1", [("q1", 10, 8)])]
    assert build_outcome_report(docs, {"q1": ("CLO1",)}).outcome("CLO1").students_mastered == 1
    strict = build_outcome_report(docs, {"q1": ("CLO1",)}, mastery_threshold=0.9)
    assert strict.outcome("CLO1").students_mastered == 0
    assert strict.mastery_threshold == 0.9


def test_coverage_reports_how_much_of_the_assignment_an_outcome_carries() -> None:
    report = build_outcome_report(
        [_doc("s1", [("q1", 2, 2), ("q2", 98, 50)])], {"q1": ("CLO1",)}
    )
    assert report.outcome("CLO1").assignment_weight == 0.02


def test_question_ids_record_what_measured_the_outcome() -> None:
    report = build_outcome_report(
        [_doc("s1", [("q10", 5, 5), ("q2", 5, 5)])],
        {"q2": ("CLO1",), "q10": ("CLO1",)},
    )
    assert report.outcome("CLO1").question_ids == ("q2", "q10")


def test_outcomes_are_ordered_naturally() -> None:
    report = build_outcome_report(
        [_doc("s1", [("q1", 5, 5), ("q2", 5, 5), ("q3", 5, 5)])],
        {"q1": ("CLO10",), "q2": ("CLO2",), "q3": ("CLO1",)},
    )
    assert [o.outcome for o in report.outcomes] == ["CLO1", "CLO2", "CLO10"]


def test_no_tags_means_an_empty_report_not_a_crash() -> None:
    report = build_outcome_report([_doc("s1", [("q1", 10, 5)])], {})
    assert report.outcomes == ()
    assert report.tagged_share == 0.0


def test_an_empty_class_is_safe() -> None:
    report = build_outcome_report([], {"q1": ("CLO1",)})
    assert report.outcomes == ()
    assert report.class_size == 0
    assert report.tagged_share is None


def test_missing_possible_points_fall_back_to_the_answer_key() -> None:
    report = build_outcome_report(
        [_doc("s1", [("q1", None, 8)])],
        {"q1": ("CLO1",)},
        possible_points_by_question={"q1": 10.0},
    )
    assert report.outcome("CLO1").students[0].mastery == 0.8
