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

_FREE_RESPONSE_EDGE_CHARS = " \t\r\n.,;:!?\"'`"

_CHOICE_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def normalize_answer_key(response_text: str, question_type: QuestionType) -> str:
    text = response_text or ""

    if question_type in (MULTIPLE_CHOICE, MULTIPLE_ANSWER):
        tokens = _CHOICE_TOKEN_RE.findall(text.upper())
        return " ".join(sorted(tokens))

    if question_type == NUMERIC:
        value = response_value(text)
        return "" if value is None else f"{value:g}"

    collapsed = " ".join(text.split()).casefold()
    return collapsed.strip(_FREE_RESPONSE_EDGE_CHARS)


@dataclass(frozen=True)
class GroupableResponse:
    member_id: str
    response_text: str
    question_type: QuestionType


@dataclass(frozen=True)
class AnswerGroup:
    key: str
    member_ids: tuple[str, ...]
    sample_response_text: str
    question_type: QuestionType

    @property
    def is_blank(self) -> bool:
        return self.key == ""

    @property
    def size(self) -> int:
        return len(self.member_ids)


def group_responses(responses: Iterable[GroupableResponse]) -> list[AnswerGroup]:
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
        return (1 if group.is_blank else 0, -group.size, group.key)

    return sorted(groups, key=sort_key)
