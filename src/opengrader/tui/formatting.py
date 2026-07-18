"""Display-formatting helpers shared across TUI screens."""

import re

_QUESTION_ID_RE = re.compile(r"^[Qq](\d+[A-Za-z]*)$")


def format_question_label(question_id: str) -> str:
    """Humanize a QA question id for display ("q5a" -> "Question 5a").

    Ids that don't follow the q<number>[suffix] shape are shown unchanged,
    so custom question ids still display as authored.
    """
    match = _QUESTION_ID_RE.match(question_id.strip())
    if match:
        return f"Question {match.group(1)}"
    return question_id


_QUESTION_TYPE_LABELS = {
    "free_response": "Free Response",
    "multiple_choice": "Multiple Choice",
}


def format_question_type(question_type: str) -> str:
    """Humanize a QuestionType literal ("multiple_choice" -> "Multiple Choice").

    Unknown or empty values return "" so callers can simply skip the label.
    """
    return _QUESTION_TYPE_LABELS.get(question_type.strip().lower(), "")
