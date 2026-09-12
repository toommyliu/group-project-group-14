from collections.abc import Iterable
from typing import Literal


EPSILON = 1e-6
ScoringMode = Literal["positive_awards", "negative_deductions"]
RubricOptionKind = Literal["correct", "partial", "incorrect", "bonus"]
RubricSelectionMode = Literal["exclusive", "checklist"]


def clamp_points(points: float, *, max_points: float) -> float:
    return max(0.0, min(points, max_points))


def rubric_scoring_mode(points_values: Iterable[float]) -> ScoringMode:
    values = list(points_values)
    has_positive = any(value > EPSILON for value in values)
    has_negative = any(value < -EPSILON for value in values)

    if has_negative:
        return "negative_deductions"
    if has_positive:
        return "positive_awards"
    return "negative_deductions"


def rubric_uses_positive_awards(points_values: Iterable[float]) -> bool:
    return rubric_scoring_mode(points_values) == "positive_awards"


def awarded_points_for_rubric_selection(
    possible_points: float,
    selected_points_values: Iterable[float],
    rubric_points_values: Iterable[float],
    *,
    bonus_points_values: Iterable[float] = (),
) -> float:
    base_points = possible_points if possible_points is not None else 0.0
    selected_total = sum(selected_points_values)
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
    if rubric_uses_positive_awards(rubric_points_values):
        return -possible_points
    return 0.0
