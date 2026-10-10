"""What the answer key says is printed on the pages, for finding it there.

A scan Opengrader printed carries its own labels ("3. (10 points)"), and those
are enough. An instructor's own assignment may number questions any way it
likes — "3)", "3a)", no points at all — and a student's page is full of other
numbers. The answer key already says which questions and parts exist, in what
order, and what each one's text is, so on such pages the reader looks for
exactly that sequence (`layout.outline_labels`) instead of trusting any number
it sees.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from opengrader.core.question_types import MULTIPLE_ANSWER, MULTIPLE_CHOICE

if TYPE_CHECKING:  # pragma: no cover
    from opengrader.core.qa_file.markdown import QADocument


@dataclass(frozen=True)
class OutlineItem:
    # The question id the answer key and the submission files share: "q3", "q3a".
    key: str
    number: int
    part: str | None
    points: float | None
    prompt: str
    # The question type the key settles (it lists choices), else None.
    kind: str | None = None
    # A question that is only the stem above its parts; never graded itself.
    stem: bool = False


Outline = tuple[OutlineItem, ...]


def outlines_from_qa(document: "QADocument") -> tuple[Outline, ...]:
    """One outline per version of the assignment (one, when it has none)."""
    if document.version_questions:
        versions = [list(questions) for questions in document.version_questions.values()]
    else:
        versions = [list(document.questions)]
    return tuple(outline for outline in (_outline(questions) for questions in versions) if outline)


def load_outlines(qa_file: str | Path) -> tuple[Outline, ...]:
    from opengrader.core.qa_file.markdown import parse_qa_markdown

    return outlines_from_qa(parse_qa_markdown(Path(qa_file).read_text(encoding="utf-8")))


def _kind(question) -> str | None:  # noqa: ANN001
    if not question.choices:
        return None
    return MULTIPLE_ANSWER if question.correct_choice_labels else MULTIPLE_CHOICE


def _outline(questions: list) -> Outline:
    items: list[OutlineItem] = []
    for number, question in enumerate(questions, start=1):
        key = f"q{number}"
        if not question.subquestions:
            items.append(
                OutlineItem(key, number, None, float(question.points), question.prompt, _kind(question))
            )
            continue
        items.append(OutlineItem(key, number, None, float(question.points), question.prompt, stem=True))
        for index, part in enumerate(question.subquestions):
            letter = (part.label or chr(ord("a") + index)).lower()
            items.append(
                OutlineItem(f"{key}{letter}", number, letter, float(part.points), part.prompt, _kind(part))
            )
    return tuple(items)


def regions_fingerprint(regions: dict[str, list[tuple[str, tuple[float, ...]]]]) -> str:
    """A digest of where each printed page's questions are: {page: [(key, box)]}."""
    body = {
        page: [[key, [round(value, 4) for value in box]] for key, box in found]
        for page, found in sorted(regions.items())
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
