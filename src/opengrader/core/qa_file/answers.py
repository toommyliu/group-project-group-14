"""The answers of an answer key, as a form to fill in.

A drafted key (`draft_key.py`) has every question and no answers, and its
teacher should not have to learn the file's format to add them. This lists
what can be answered — each question, or each part of one — and writes the
answers back into the file, touching only the questions that changed
(`editing.edit_qa_markdown`): rubrics, comments and everything else in the
file stay byte for byte as they were.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from .editing import QuestionEdit, edit_qa_markdown
from .markdown import QAChoice, parse_qa_markdown

# An option a draft kept as a line of the question's text: "A) Sodium".
_OPTION_LINE_RE = re.compile(r"^\s*([A-Z])\)\s+(.+?)\s*$")


@dataclass(frozen=True)
class AnswerField:
    # Where the question starts in the file: what an edit is addressed to.
    source_line: int
    # "1", "3a" — and "B · 1" in a key with versions.
    label: str
    prompt: str
    points: int
    # (letter, text) when the question is multiple choice, else empty.
    choices: tuple[tuple[str, str], ...]
    # The accepted answers; for multiple choice, the correct letters.
    answers: tuple[str, ...]
    # True when the choices are still lines of the prompt (a draft's way of
    # keeping them): saving a correct one makes them real choices.
    choices_in_prompt: bool = False
    # The question's id ("q1", "q3a") and its version ("" without versions):
    # how the browser, which never sees line numbers, names it.
    key: str = ""
    version: str = ""


def _options_in_prompt(prompt: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    """(the question's own text, its options) for a prompt that ends in
    "A) … B) …" lines; no options when it does not."""
    lines = prompt.splitlines()
    options: list[tuple[str, str]] = []
    while lines and (match := _OPTION_LINE_RE.match(lines[-1])):
        options.insert(0, (match.group(1), match.group(2)))
        lines.pop()
    letters = [letter for letter, _text in options]
    if len(options) < 2 or letters != [chr(ord("A") + index) for index in range(len(options))]:
        return prompt, ()
    return "\n".join(lines).strip(), tuple(options)


def _versions(document) -> list[tuple[str, list]]:  # noqa: ANN001
    if document.version_questions:
        return [(str(name), list(questions)) for name, questions in document.version_questions.items()]
    return [("", list(document.questions))]


def answer_fields(markdown: str) -> list[AnswerField]:
    """Every question (or part) of the key that takes an answer, in order.
    Raises ValueError for a file that is not a valid answer key."""
    fields: list[AnswerField] = []
    for version, questions in _versions(parse_qa_markdown(markdown)):
        for number, question in enumerate(questions, start=1):
            parts = question.subquestions or [question]
            for index, item in enumerate(parts):
                label = str(number)
                if question.subquestions:
                    label += (item.label or chr(ord("a") + index)).lower()
                if version:
                    label = f"{version} · {label}"
                prompt, choices_in_prompt = item.prompt, False
                choices = tuple((choice.label, choice.text) for choice in item.choices)
                if choices:
                    answers = tuple(
                        item.correct_choice_labels
                        or ((item.correct_choice_label,) if item.correct_choice_label else ())
                    )
                else:
                    answers = tuple(item.answers)
                    if not answers:
                        prompt, choices = _options_in_prompt(item.prompt)
                        choices_in_prompt = bool(choices)
                fields.append(
                    AnswerField(
                        source_line=item.source_line,
                        key=item.id,
                        version=version,
                        label=label,
                        prompt=prompt,
                        points=item.points,
                        choices=choices,
                        answers=answers,
                        choices_in_prompt=choices_in_prompt,
                    )
                )
    return fields


def unanswered_count(markdown: str) -> int:
    """How many questions (or parts) of the key have no answer yet; 0 for a
    file that is not a valid answer key."""
    try:
        return sum(1 for field in answer_fields(markdown) if not field.answers)
    except ValueError:
        return 0


def clean_answers(field: AnswerField, given: Sequence[str]) -> tuple[str, ...]:
    """Answers as they are stored: trimmed, blanks dropped, and — for a
    multiple-choice question — capital letters that are among its options.
    Raises ValueError for a letter that is not."""
    wanted = tuple(value.strip() for value in given if value.strip())
    if not field.choices:
        return wanted
    wanted = tuple(value.upper() for value in wanted)
    unknown = [value for value in wanted if value not in {letter for letter, _ in field.choices}]
    if unknown:
        raise ValueError(f"Question {field.label} has no option {', '.join(unknown)}.")
    return wanted


def answer_edit(field: AnswerField, original, given: Sequence[str]) -> QuestionEdit | None:  # noqa: ANN001
    """The edit that gives `original` (the parsed question behind `field`)
    these answers; None when it already has them."""
    wanted = clean_answers(field, given)
    if wanted == field.answers:
        return None
    if field.choices_in_prompt:
        if not wanted:
            return None
        # The draft's option lines become real choices now one is correct.
        return QuestionEdit(
            source_line=field.source_line,
            prompt=field.prompt,
            points=original.points,
            answers=[],
            rubric=original.rubric,
            choices=[QAChoice(letter, text) for letter, text in field.choices],
            correct_choice_labels=list(wanted),
        )
    return QuestionEdit(
        source_line=field.source_line,
        prompt=original.prompt,
        points=original.points,
        answers=[] if field.choices else list(wanted),
        rubric=original.rubric,
        correct_choice_labels=list(wanted) if field.choices else None,
    )


def apply_answers(markdown: str, answers: Mapping[int, Sequence[str]]) -> str:
    """The answer key with these answers, keyed by `AnswerField.source_line`.

    A question left out, or given what it already has, is not touched. An
    empty answer clears one. For a multiple-choice question the answers are
    its correct letters. Raises ValueError when the result would not be a
    valid answer key (a letter that is not an option, say), so nothing
    half-right is ever written.
    """
    document = parse_qa_markdown(markdown)
    originals = {
        item.source_line: item
        for _version, questions in _versions(document)
        for question in questions
        for item in [question, *question.subquestions]
    }
    current = {field.source_line: field for field in answer_fields(markdown)}

    edits: list[QuestionEdit] = []
    for source_line, given in answers.items():
        field = current.get(source_line)
        if field is None:
            raise ValueError("That question is no longer in the answer key.")
        change = answer_edit(field, originals[source_line], given)
        if change is not None:
            edits.append(change)
    if not edits:
        return markdown
    result = edit_qa_markdown(markdown, edits)
    parse_qa_markdown(result)
    return result
