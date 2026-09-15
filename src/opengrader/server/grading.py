"""Validate web grade edits against the same rubric scoring used by the TUI."""

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, FiniteFloat

from opengrader.core.qa_file.scoring import awarded_points_for_rubric_selection

if TYPE_CHECKING:
    from opengrader.server.app import Question


class GradeEdit(BaseModel):
    expected_revision: str
    rubric_labels: list[str]
    analysis: str = Field(max_length=50000)
    score_mode: Literal["rubric", "manual"] = "rubric"
    points_awarded: FiniteFloat | None = None


def grade_points(question: "Question", edit: GradeEdit) -> float | None:
    labels = edit.rubric_labels
    options = question.rubric
    if len(set(labels)) != len(labels) or any(
        label not in {option.label for option in options} for label in labels
    ):
        raise ValueError("Choose rubric items from the current question.")
    selected = [option for option in options if option.label in labels]
    base = [option for option in selected if option.kind != "bonus"]
    if any(option.selection_mode == "exclusive" for option in base) and len(base) > 1:
        raise ValueError("An exclusive rubric item cannot be combined with other base items.")
    if edit.score_mode == "manual":
        if labels:
            raise ValueError("Clear rubric selections before entering a manual score.")
        if edit.points_awarded is not None and edit.points_awarded < 0:
            raise ValueError("Enter a score of zero or more.")
        return edit.points_awarded
    if not selected:
        return None
    return awarded_points_for_rubric_selection(
        question.possible_points,
        [option.points_delta for option in base],
        [option.points_delta for option in options if option.kind != "bonus"],
        bonus_points_values=[option.points_delta for option in selected if option.kind == "bonus"],
    )
