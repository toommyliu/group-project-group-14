from collections.abc import Iterable
from typing import Literal


EPSILON = 1e-6
ScoringMode = Literal["positive_awards", "negative_deductions"]
RubricOptionKind = Literal["correct", "partial", "incorrect", "bonus"]
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

    Positive values alongside deductions are bonus rows, so any rubric that
    contains a deduction resolves to negative deductions. Callers that need
    pure base-rubric inference should filter bonus deltas out first.
    """
    values = list(points_values)
    has_positive = any(value > EPSILON for value in values)
    has_negative = any(value < -EPSILON for value in values)

    if has_negative:
        # If there are deductions, it's a deduction rubric (bonuses are allowed).
        return "negative_deductions"
    if has_positive:
        return "positive_awards"
    return "negative_deductions"


def rubric_uses_positive_awards(points_values: Iterable[float]) -> bool:
    """Return whether the rubric awards points from zero."""
    return rubric_scoring_mode(points_values) == "positive_awards"


def awarded_points_for_rubric_selection(
    possible_points: float,
    selected_points_values: Iterable[float],
    rubric_points_values: Iterable[float],
    *,
    bonus_points_values: Iterable[float] = (),
) -> float:
    """Compute awarded points for a set of selected rubric values."""
    base_points = possible_points if possible_points is not None else 0.0
    selected_total = sum(selected_points_values)
    # Award rubrics store earned points; deduction rubrics store adjustments from full credit.
    # Bonus deltas are always added after clamping the base score.
    if rubric_uses_positive_awards(rubric_points_values):
        base_score = clamp_points(selected_total, max_points=base_points)
    else:
        base_score = clamp_points(
            base_points + selected_total, max_points=base_points
        )
    return base_score + sum(bonus_points_values)


def score_adjustment_for_rubric_selection(
    possible_points: float,
    selected_points_values: Iterable[float],
    rubric_points_values: Iterable[float],
    *,
    bonus_points_values: Iterable[float] = (),
) -> float:
    """Compute the selected rubric values' contribution to total score."""
    awarded = awarded_points_for_rubric_selection(
        possible_points,
        selected_points_values,
        rubric_points_values,
        bonus_points_values=bonus_points_values,
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
