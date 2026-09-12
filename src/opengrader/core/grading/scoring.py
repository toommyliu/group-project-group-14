from collections.abc import Sequence

from opengrader.core.qa_file import QARubricFlatOption
from opengrader.core.qa_file.scoring import awarded_points_for_rubric_selection


def score_rubric_selection(
    possible_points: float,
    options: Sequence[QARubricFlatOption],
    selected: Sequence[QARubricFlatOption],
) -> float | None:
    """Score a selection, distinguishing an ungraded answer from a zero score."""
    if not selected:
        return None
    return awarded_points_for_rubric_selection(
        possible_points,
        [option.points_delta for option in selected if option.kind != "bonus"],
        [option.points_delta for option in options if option.kind != "bonus"],
        bonus_points_values=[
            option.points_delta for option in selected if option.kind == "bonus"
        ],
    )
