"""Cluster student responses to one question into identical-answer groups.

This is the engine behind "grade the whole group at once": rather than
grading every student individually, an instructor grades each *distinct*
answer once and the same rubric selection applies to everyone who gave it
(GradeScope's answer-grouping workflow).

The module is deliberately pure — it takes response text and a question
type, and returns groups. It never touches files or grading state, so it is
reused by the TUI controller and covered by fast unit tests. Callers decide
how to persist a group grade; see ``GradeController.apply_grade_to_answer_group``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from opengrader.core.question_types import (
    MULTIPLE_ANSWER,
    MULTIPLE_CHOICE,
    NUMERIC,
    QuestionType,
)
from opengrader.core.qa_file.numeric import response_value

# Punctuation trimmed from the ends of a free-response answer so that "42."
# and "42" group together — matching how a grader reads them as the same
# answer. Inner punctuation is preserved so "f(x)=x^2" stays distinct.
_FREE_RESPONSE_EDGE_CHARS = " \t\r\n.,;:!?\"'`"

# Choice labels are single alphanumeric tokens ("A", "b)", "(3)"). We keep the
# alphanumerics and drop everything else, so equivalent renderings collapse.
_CHOICE_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def normalize_answer_key(response_text: str, question_type: QuestionType) -> str:
    """Return the canonical grouping key for a single response.

    Two responses belong to the same group exactly when this function returns
    the same non-empty key for both. A blank or whitespace-only response (and,
    for multiple choice, one with no recognizable choice token) returns ``""``
    — the sentinel for "no answer", handled as its own group by the caller.

    Normalization is intentionally conservative: it collapses only differences
    a grader would consider cosmetic (case, surrounding whitespace, choice
    punctuation, trailing sentence punctuation). It never attempts semantic or
    fuzzy matching, so it can never silently merge two genuinely different
    answers.
    """
    text = response_text or ""

    if question_type in (MULTIPLE_CHOICE, MULTIPLE_ANSWER):
        tokens = _CHOICE_TOKEN_RE.findall(text.upper())
        # Sort so multi-select answers ("A, C" and "C, A") group together.
        return " ".join(sorted(tokens))

    if question_type == NUMERIC:
        # Group by numeric value so "42", "42.0", and "42 mL" collapse together;
        # a response with no readable number falls through to the blank bucket.
        value = response_value(text)
        return "" if value is None else f"{value:g}"

    # Free response: collapse internal whitespace, casefold, trim cosmetic
    # edge punctuation.
    collapsed = " ".join(text.split()).casefold()
    return collapsed.strip(_FREE_RESPONSE_EDGE_CHARS)


@dataclass(frozen=True)
class GroupableResponse:
    """One student's response to the question being grouped."""

    member_id: str
    response_text: str
    question_type: QuestionType


@dataclass(frozen=True)
class AnswerGroup:
    """A set of students who gave the same (normalized) answer."""

    key: str
    member_ids: tuple[str, ...]
    sample_response_text: str
    question_type: QuestionType

    @property
    def is_blank(self) -> bool:
        """Whether this group is the "no answer" bucket."""
        return self.key == ""

    @property
    def size(self) -> int:
        return len(self.member_ids)


def group_responses(responses: Iterable[GroupableResponse]) -> list[AnswerGroup]:
    """Cluster responses into answer groups.

    Members keep first-seen order within a group, and groups are returned
    largest-first (ties broken by key) so the highest-leverage answers surface
    first; the blank group always sorts last regardless of size, since it is
    the least useful to batch-grade blind. The result is fully deterministic.
    """
    order: list[str] = []
    members_by_key: dict[str, list[str]] = {}
    sample_by_key: dict[str, str] = {}
    type_by_key: dict[str, QuestionType] = {}

    for response in responses:
        key = normalize_answer_key(response.response_text, response.question_type)
        if key not in members_by_key:
            order.append(key)
            members_by_key[key] = []
            sample_by_key[key] = response.response_text.strip()
            type_by_key[key] = response.question_type
        members_by_key[key].append(response.member_id)
        # Prefer a non-empty sample so the UI can echo the shared answer even
        # if the first member for a key happened to have odd whitespace.
        if not sample_by_key[key] and response.response_text.strip():
            sample_by_key[key] = response.response_text.strip()

    groups = [
        AnswerGroup(
            key=key,
            member_ids=tuple(members_by_key[key]),
            sample_response_text=sample_by_key[key],
            question_type=type_by_key[key],
        )
        for key in order
    ]

    def sort_key(group: AnswerGroup) -> tuple[int, int, str]:
        # Blank last; then largest first; then key for stable ordering.
        return (1 if group.is_blank else 0, -group.size, group.key)

    return sorted(groups, key=sort_key)
