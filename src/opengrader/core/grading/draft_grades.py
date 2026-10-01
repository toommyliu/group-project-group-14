"""Generate draft grades for groups of student answers.

One model call per distinct answer, so identical answers always get the same
draft grade. Nothing is saved here — callers save the grades and tag them as
AI grades for human review.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from opengrader.core.grading.llm_grader import (
    GenerateFn,
    GradeSuggestion,
    RubricChoice,
    suggest_free_response_grade,
)
from opengrader.core.llm import LLMError
from opengrader.core.submissions.answer_groups import AnswerGroup


@dataclass(frozen=True)
class GroupDraftGrade:
    member_ids: tuple[str, ...]
    suggestion: GradeSuggestion


@dataclass(frozen=True)
class DraftGradeResult:
    applied: tuple[GroupDraftGrade, ...]
    low_confidence: tuple[GroupDraftGrade, ...]
    failed_groups: int

    @property
    def applied_student_count(self) -> int:
        return sum(len(grade.member_ids) for grade in self.applied)

    @property
    def low_confidence_student_count(self) -> int:
        return sum(len(grade.member_ids) for grade in self.low_confidence)


def draft_grades_for_groups(
    groups: Sequence[AnswerGroup],
    *,
    prompt: str,
    expected_answers: Sequence[str],
    rubric_options: Sequence[RubricChoice],
    possible_points: float,
    generate: GenerateFn,
    min_confidence: float,
    eligible_member_ids: Collection[str] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> DraftGradeResult:
    if not rubric_options:
        return DraftGradeResult(applied=(), low_confidence=(), failed_groups=0)

    eligible_member_ids_set = (
        set(eligible_member_ids) if eligible_member_ids is not None else None
    )
    groups_to_process: list[tuple[AnswerGroup, tuple[str, ...]]] = []

    for group in groups:
        if group.is_blank or not group.sample_response_text.strip():
            continue

        if eligible_member_ids_set is not None:
            eligible = tuple(
                mid for mid in group.member_ids if mid in eligible_member_ids_set
            )
            if not eligible:
                continue
        else:
            eligible = group.member_ids

        groups_to_process.append((group, eligible))

    total = len(groups_to_process)

    applied_list: list[GroupDraftGrade] = []
    low_confidence_list: list[GroupDraftGrade] = []
    failed_groups = 0

    for done, (group, eligible_members) in enumerate(groups_to_process):
        if on_progress is not None:
            on_progress(done, total)

        try:
            suggestion = suggest_free_response_grade(
                prompt=prompt,
                expected_answers=list(expected_answers),
                response_text=group.sample_response_text,
                rubric_options=list(rubric_options),
                possible_points=possible_points,
                generate=generate,
            )
        except LLMError:
            failed_groups += 1
            continue

        if suggestion is None:
            failed_groups += 1
            continue

        draft_grade = GroupDraftGrade(
            member_ids=eligible_members, suggestion=suggestion
        )
        if suggestion.confidence >= min_confidence:
            applied_list.append(draft_grade)
        else:
            low_confidence_list.append(draft_grade)

    return DraftGradeResult(
        applied=tuple(applied_list),
        low_confidence=tuple(low_confidence_list),
        failed_groups=failed_groups,
    )
