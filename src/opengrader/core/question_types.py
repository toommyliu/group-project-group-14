from __future__ import annotations

import re
from typing import Literal

FREE_RESPONSE = "free_response"
MULTIPLE_CHOICE = "multiple_choice"
MULTIPLE_ANSWER = "multiple_answer"
NUMERIC = "numeric"

QuestionType = Literal[FREE_RESPONSE, MULTIPLE_CHOICE, MULTIPLE_ANSWER, NUMERIC]

TRUE_CHOICE_TEXT = "True"
FALSE_CHOICE_TEXT = "False"

_TRUE_TOKENS = {"true", "t", "yes", "y"}
_FALSE_TOKENS = {"false", "f", "no", "n"}


def normalize_boolean(text: str) -> bool | None:
    cleaned = text.strip().strip(".):(,").strip().lower()
    if not cleaned:
        return None
    if cleaned in _TRUE_TOKENS or cleaned == "1":
        return True
    if cleaned in _FALSE_TOKENS or cleaned == "0":
        return False
    return None


def is_boolean_answer(text: str) -> bool:
    return text.strip().lower() in _TRUE_TOKENS | _FALSE_TOKENS


_QUESTION_ID_RE = re.compile(r"^[Qq](\d+[A-Za-z]*)$")


def humanize_question_label(question_id: str) -> str:
    match = _QUESTION_ID_RE.match(question_id.strip())
    if match:
        return f"Question {match.group(1)}"
    return question_id
