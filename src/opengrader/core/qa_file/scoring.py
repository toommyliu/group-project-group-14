from collections.abc import Iterable
from typing import Literal


EPSILON = 1e-6
ScoringMode = Literal["positive_awards", "negative_deductions"]
RubricOptionKind = Literal["correct", "partial", "incorrect"]
RubricSelectionMode = Literal["exclusive", "checklist"]


def clamp_points(points: float, *, max_points: float) -> float:
    """Clamp awarded points to the inclusive range for a question."""
    return max(0.0, min(points, max_points))


def rubric_scoring_mode(points_values: Iterable[float]) -> ScoringMode:
    """Infer whether a rubric awards from zero or deducts from full credit.

    QA rubrics support exactly one scoring mode per question:

    - Negative deductions start from full credit. `Correct (+0)` means no
      deduction, while `Incorrect (-10)` means remove all points for a
      10-point question.
    - Positive awards start from zero credit. `Correct (+10)` means award
      full credit, while `Incorrect (+0)` means award no points.

    Mixing positive and negative non-zero values makes the base score
    ambiguous, so callers should reject that rubric.
    """
    values = list(points_values)
    has_positive = any(value > EPSILON for value in values)
    has_negative = any(value < -EPSILON for value in values)
    if has_positive and has_negative:
        raise ValueError("rubric cannot mix positive awards and negative deductions")
    return "positive_awards" if has_positive else "negative_deductions"


def rubric_uses_positive_awards(points_values: Iterable[float]) -> bool:
    """Return whether the rubric awards points from zero."""
    return rubric_scoring_mode(points_values) == "positive_awards"


def awarded_points_for_rubric_selection(
    possible_points: float,
    selected_points_values: Iterable[float],
    rubric_points_values: Iterable[float],
) -> float:
    """Compute awarded points for a set of selected rubric values."""
    selected_total = sum(selected_points_values)
    # Award rubrics store earned points; deduction rubrics store adjustments from full credit.
    if rubric_uses_positive_awards(rubric_points_values):
        return clamp_points(selected_total, max_points=possible_points)
    return clamp_points(possible_points + selected_total, max_points=possible_points)


def score_adjustment_for_rubric_selection(
    possible_points: float,
    selected_points_values: Iterable[float],
    rubric_points_values: Iterable[float],
) -> float:
    """Compute the selected rubric values' contribution to total score."""
    awarded = awarded_points_for_rubric_selection(
        possible_points,
        selected_points_values,
        rubric_points_values,
    )
    return awarded - possible_points


def ungraded_score_adjustment(
    possible_points: float,
    rubric_points_values: Iterable[float],
) -> float:
    """Return the score adjustment for an ungraded question in this mode.

    Deduction rubrics start from full credit, so an ungraded question has no
    adjustment. Award rubrics start from zero, so an ungraded question must
    subtract its possible points until a rubric option is selected.
    """
    if rubric_uses_positive_awards(rubric_points_values):
        return -possible_points
    return 0.0
