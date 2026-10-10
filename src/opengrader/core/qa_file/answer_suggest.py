"""Ask an AI model for the answers an answer key is still missing.

Only ever a suggestion: the answers form (`tui/ui/screens/answer_fill_screen`)
puts what comes back into the boxes that were empty, marked as the AI's, and
nothing reaches the file until the teacher has looked and pressed Save. An
answer key decides every student's grade, so a model never writes one.

What is sent is the questions' own text and options — nothing a student
wrote, no name, no ID. The model is told to leave a question alone when it
cannot be answered from its text (it refers to a figure, a passage, the
lecture) or has no single right answer; an empty suggestion is the right
result there, not a failure.
"""

from __future__ import annotations

from typing import Callable, Sequence

from opengrader.core.llm import parse_json_object
from opengrader.core.qa_file.answers import AnswerField

ANSWER_SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "answers": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["question", "answers"],
            },
        },
    },
    "required": ["answers"],
}

# What one suggestion may hold: a few ways to write a short answer.
MAX_SUGGESTED_ANSWERS = 4
_MAX_ANSWER_CHARS = 200
_MAX_PROMPT_CHARS = 1200


def build_answer_prompt(fields: Sequence[AnswerField], *, title: str = "") -> str:
    lines = [
        "You are helping a teacher fill in the answer key for an assignment"
        + (f' titled "{title}".' if title else "."),
        "For each question below give the correct answer.",
        "",
        "Rules:",
        "- A multiple-choice question lists its options. Answer with the letter "
        "of the correct option only (several letters only if several are correct).",
        "- For any other question give the answer as a student should write it: "
        "short, no explanation. If it can be written in more than one usual way "
        f"(\"4\" and \"four\"), list each, at most {MAX_SUGGESTED_ANSWERS}.",
        "- If a question cannot be answered from its own text — it refers to a "
        "figure, a table, a passage or a class you cannot see — or it asks for "
        "an opinion, an essay or anything with no single correct answer, give "
        "an empty list for it. Never guess.",
        "",
        'Reply with JSON only: {"answers": [{"question": "<the question\'s '
        'label>", "answers": ["..."]}]}',
        "",
        "Questions:",
    ]
    for field in fields:
        prompt = " ".join(field.prompt.split())[:_MAX_PROMPT_CHARS]
        lines.append(f"[{field.label}] {prompt}")
        for letter, text in field.choices:
            lines.append(f"    {letter}) {' '.join(text.split())}")
    return "\n".join(lines)


def parse_answer_suggestions(
    raw: str, fields: Sequence[AnswerField]
) -> dict[int, tuple[str, ...]]:
    """What the model suggested, keyed by `AnswerField.source_line`.

    Only what can be used is kept: a question that was asked about, a letter
    that is one of its options, an answer of a sensible length. Anything else
    — and a question the model left empty — is simply not in the result.
    """
    try:
        data = parse_json_object(raw)
    except (ValueError, TypeError):
        return {}
    entries = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return {}
    by_label = {field.label: field for field in fields}

    suggestions: dict[int, tuple[str, ...]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        field = by_label.get(str(entry.get("question", "")).strip().strip("[]"))
        given = entry.get("answers")
        if field is None or not isinstance(given, list):
            continue
        kept: list[str] = []
        letters = {letter for letter, _text in field.choices}
        for value in given:
            answer = " ".join(str(value).split())
            if field.choices:
                answer = answer.rstrip(").:").upper()
                if answer not in letters:
                    continue
            if answer and len(answer) <= _MAX_ANSWER_CHARS and answer not in kept:
                kept.append(answer)
        if kept:
            suggestions[field.source_line] = tuple(kept[:MAX_SUGGESTED_ANSWERS])
    return suggestions


def suggest_answers(
    fields: Sequence[AnswerField],
    generate: Callable[[str], str],
    *,
    title: str = "",
) -> dict[int, tuple[str, ...]]:
    """One model call for all of `fields`; see `parse_answer_suggestions`."""
    if not fields:
        return {}
    return parse_answer_suggestions(generate(build_answer_prompt(fields, title=title)), fields)
