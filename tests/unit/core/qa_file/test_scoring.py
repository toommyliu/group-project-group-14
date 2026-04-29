import pytest

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


def test_mixed_positive_awards_and_negative_deductions_are_rejected() -> None:
    with pytest.raises(ValueError, match="cannot mix positive awards"):
        rubric_scoring_mode([10.0, -3.0, 0.0])
