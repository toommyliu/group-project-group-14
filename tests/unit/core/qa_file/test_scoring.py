from opengrader.core.grading.scoring import score_rubric_selection
from opengrader.core.qa_file import QARubricFlatOption
from opengrader.core.qa_file.scoring import (
    awarded_points_for_rubric_selection,
    rubric_scoring_mode,
    score_adjustment_for_rubric_selection,
    ungraded_score_adjustment,
)


def test_negative_deduction_rubric_starts_from_full_credit() -> None:
    rubric_values = [0.0, -3.0, -7.0, -10.0]

    assert rubric_scoring_mode(rubric_values) == "negative_deductions"
    assert awarded_points_for_rubric_selection(10, [-3, -4], rubric_values) == 3
    assert score_adjustment_for_rubric_selection(10, [-3], rubric_values) == -3
    assert ungraded_score_adjustment(10, rubric_values) == 0


def test_positive_award_rubric_starts_from_zero_credit() -> None:
    rubric_values = [10.0, 7.0, 3.0, 0.0]

    assert rubric_scoring_mode(rubric_values) == "positive_awards"
    assert awarded_points_for_rubric_selection(10, [7, 3], rubric_values) == 10
    assert score_adjustment_for_rubric_selection(10, [7], rubric_values) == -3
    assert ungraded_score_adjustment(10, rubric_values) == -10


def test_mixed_values_resolve_to_negative_deductions() -> None:
    assert rubric_scoring_mode([10.0, -3.0, 0.0]) == "negative_deductions"


def test_bonus_points_are_added_after_clamping() -> None:
    rubric_values = [0.0, -3.0, -10.0]

    assert (
        awarded_points_for_rubric_selection(
            10, [-3], rubric_values, bonus_points_values=[2.0]
        )
        == 9
    )
    assert (
        awarded_points_for_rubric_selection(
            10, [], rubric_values, bonus_points_values=[2.0]
        )
        == 12
    )
    options = [
        QARubricFlatOption("Correct", 0, "correct", "exclusive"),
        QARubricFlatOption("Missing step", -3, "partial", "checklist"),
        QARubricFlatOption("Incorrect", -10, "incorrect", "exclusive"),
        QARubricFlatOption("Bonus", 2, "bonus", "checklist"),
    ]
    assert score_rubric_selection(10, options, [options[1], options[3]]) == 9
    assert score_rubric_selection(10, options, [options[3]]) == 12
    assert score_rubric_selection(10, options, [options[2]]) == 0
    assert score_rubric_selection(10, options, []) is None
