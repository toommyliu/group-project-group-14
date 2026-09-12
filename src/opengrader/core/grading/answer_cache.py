from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from opengrader.core.grading.llm_grader import GradeSuggestion
from opengrader.core.question_types import QuestionType
from opengrader.core.submissions.answer_groups import normalize_answer_key


@dataclass(frozen=True)
class AnswerGradeKey:
    question_id: str
    assignment_version: str
    answer_key: str


def answer_grade_key(
    *,
    question_id: str,
    assignment_version: str,
    response_text: str,
    question_type: QuestionType,
) -> AnswerGradeKey:
    return AnswerGradeKey(
        question_id=question_id,
        assignment_version=assignment_version,
        answer_key=normalize_answer_key(response_text, question_type),
    )


@dataclass
class AnswerGradeCache:
    _decisions: dict[AnswerGradeKey, GradeSuggestion | None] = field(
        default_factory=dict
    )
    model_calls: int = 0
    reused: int = 0

    @property
    def answers_decided(self) -> int:
        return self.model_calls + self.reused

    def suggest(
        self,
        key: AnswerGradeKey,
        factory: Callable[[], GradeSuggestion | None],
    ) -> GradeSuggestion | None:
        """Caches None results; exceptions propagate without caching."""
        if key in self._decisions:
            self.reused += 1
            return self._decisions[key]
        suggestion = factory()
        self._decisions[key] = suggestion
        self.model_calls += 1
        return suggestion

    def summary_line(self) -> str:
        if not self.reused:
            return ""
        return (
            f"Graded {self.answers_decided} answers with {self.model_calls} model "
            f"{'call' if self.model_calls == 1 else 'calls'} "
            f"({self.reused} shared an answer already decided)."
        )
