"""Answers for an answer key, written as a plain list.

A drafted key (`draft_key.py`) has every question and no answers. Typing
them into the form is one way to fill it; this is the other, for a teacher
who already has them written down (or an AI assistant asked to write them):

    1: H2O; Water
    2) B
    3a. 101
    B 4: Paris | City of Light

Questions are numbered as the answer form numbers them (`1`, `3a`, and a
version letter first in a key with versions). Several accepted answers go
on one line separated by `;` or `|` — or a comma followed by a space when
a line has neither ("H2O, Water", while "1,000" stays one answer) — or on
lines of their own under the question:

    1. H2O
    Water

A line with no number adds to the question above it; a blank line ends the
question, so a stray line after one is reported rather than taken as an
answer. For multiple choice the answer is the letter (or letters). Lines
starting with `#` are ignored.

Applying goes through `answers.apply_answers`, so only the questions named
change, and a list that would leave the key invalid is refused whole.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .answers import answer_fields, apply_answers

__all__ = ["AnswerListResult", "apply_answer_list", "parse_answer_list"]

_LINE_RE = re.compile(
    r"""^\s*
    (?:(?P<version>[A-Za-z])\s*(?:·|:|-)?\s+(?=(?:q(?:uestion)?\s*)?\d))?   # B · 4  /  B 4
    (?:q(?:uestion)?\s*)?
    (?P<number>\d+)\s*\(?(?P<part>[a-z])?\)?
    \s*[.):\-]\s*            # the separator is what makes a line a question: "2" alone is an answer
    (?P<answers>.*?)\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


@dataclass
class AnswerListResult:
    markdown: str
    filled: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _split(answers: str) -> list[str]:
    if ";" in answers or "|" in answers:
        parts = re.split(r"[;|]", answers)
    else:
        parts = re.split(r",\s+", answers)
    return [part.strip() for part in parts if part.strip()]


def parse_answer_list(text: str) -> tuple[list[tuple[int, str, list[str]]], list[str]]:
    """(line number, label as the answer form writes it, answers), and the lines it could not read."""
    entries: list[tuple[int, str, list[str]]] = []
    problems: list[str] = []
    # The question a line without a number adds answers to; a blank line ends it.
    current: list[str] | None = None
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            current = None
            continue
        if line.startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if match is None:
            if current is not None:
                current.extend(_split(line))
            else:
                problems.append(f"line {number}: {line!r} is not 'question number: answer'")
            continue
        label = match.group("number") + (match.group("part") or "").lower()
        if match.group("version"):
            label = f"{match.group('version').upper()} · {label}"
        current = _split(match.group("answers"))
        entries.append((number, label, current))
    empty = [label for _line, label, answers in entries if not answers]
    problems += [f"question {label} has a number but no answer" for label in empty]
    return [entry for entry in entries if entry[2]], problems


def apply_answer_list(markdown: str, text: str) -> AnswerListResult:
    """The key with the list's answers in it. Raises ValueError when the
    result would not be a valid key (e.g. a letter that is not an option)."""
    entries, problems = parse_answer_list(text)
    fields = {field.label.casefold(): field for field in answer_fields(markdown)}
    result = AnswerListResult(markdown=markdown, problems=problems)
    wanted: dict[int, list[str]] = {}
    for line, label, answers in entries:
        target = fields.get(label.casefold())
        if target is None:
            result.problems.append(f"line {line}: the answer key has no question {label}")
            continue
        given = [answer.upper() for answer in answers] if target.choices else answers
        if tuple(given) == target.answers:
            result.unchanged.append(target.label)
            continue
        wanted[target.source_line] = answers
        result.filled.append(target.label)
    if wanted:
        result.markdown = apply_answers(markdown, wanted)
    return result
