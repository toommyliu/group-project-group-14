from __future__ import annotations

import re
from typing import Literal

FREE_RESPONSE = "free_response"
MULTIPLE_CHOICE = "multiple_choice"
# "Select all that apply" — one question with several correct options the
# student must all check. Distinguished from MULTIPLE_CHOICE (exactly one
# correct) purely by having more than one correct label in the answer key.
MULTIPLE_ANSWER = "multiple_answer"
NUMERIC = "numeric"

QuestionType = Literal[FREE_RESPONSE, MULTIPLE_CHOICE, MULTIPLE_ANSWER, NUMERIC]

# Canonical labels for the two options a True/False question expands into. A
# True/False question is authored as a shortcut but stored (and rendered,
# OCR'd, and graded) as a constrained two-option multiple-choice question, so
# it reuses the whole multiple-choice pipeline rather than adding a parallel
# question type.
TRUE_CHOICE_TEXT = "True"
FALSE_CHOICE_TEXT = "False"

_TRUE_TOKENS = {"true", "t", "yes", "y"}
_FALSE_TOKENS = {"false", "f", "no", "n"}


def normalize_boolean(text: str) -> bool | None:
    """Interpret a handwritten/typed True-False answer as a bool.

    Accepts the common ways a student writes a boolean answer — the word, its
    initial, yes/no, or 1/0 — case-insensitively and ignoring surrounding
    punctuation/whitespace. Returns ``None`` when the text is not a recognizable
    boolean so callers can fall back to manual grading instead of guessing.
    """
    cleaned = text.strip().strip(".):(,").strip().lower()
    if not cleaned:
        return None
    if cleaned in _TRUE_TOKENS or cleaned == "1":
        return True
    if cleaned in _FALSE_TOKENS or cleaned == "0":
        return False
    return None


def is_boolean_answer(text: str) -> bool:
    """Whether ``text`` names a boolean value (the True/False authoring shortcut)."""
    return text.strip().lower() in _TRUE_TOKENS | _FALSE_TOKENS


_QUESTION_ID_RE = re.compile(r"^[Qq](\d+[A-Za-z]*)$")


def humanize_question_label(question_id: str) -> str:
    """Humanize a QA question id for display ("q5a" -> "Question 5a").

    Ids that don't follow the ``q<number>[suffix]`` shape are shown unchanged, so
    custom question ids still display as authored. The single source of truth for
    this cosmetic label — the TUI (``tui/formatting.format_question_label``) and
    the headless PDF/report generators both delegate here so a returned document
    reads identically whether it was produced from the TUI or the CLI.
    """
    match = _QUESTION_ID_RE.match(question_id.strip())
    if match:
        return f"Question {match.group(1)}"
    return question_id
