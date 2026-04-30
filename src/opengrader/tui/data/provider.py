from __future__ import annotations

from typing import Protocol

from opengrader.tui.data.models import (
    QuestionSeed,
    RubricEditResult,
    RubricOptionSeed,
    StudentSummary,
)


class Provider(Protocol):
    def list_students(self, course_id: int | None) -> list[StudentSummary]: ...

    def build_question_seeds(
        self,
        *,
        assignment_id: int | None = None,
        assignment_title: str | None = None,
        student_item_id: str | None = None,
    ) -> list[QuestionSeed]: ...

    def save_rubric_options(
        self,
        *,
        student_item_id: str,
        question_id: str,
        options: list[RubricOptionSeed],
        original_options: list[RubricOptionSeed],
    ) -> RubricEditResult: ...
