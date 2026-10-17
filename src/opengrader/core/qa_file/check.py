"""Checking an answer key: what stops it loading, and what is probably a mistake.

`parse_qa_markdown` already refuses a key it cannot read (and says where).
This adds the problems a key can load with and still be wrong about: two
questions with the same text, the same rubric label twice, a question with
no answer and nothing to grade it by, versions that do not have the same
questions. They are warnings, never refusals: a teacher may mean any of
them. `qa-file validate` prints them, and so does `agent check-key` for an
AI assistant writing a key.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import QAParseError
from .markdown import QADocument, flatten_rubric_options, parse_qa_markdown

__all__ = ["KeyCheck", "check_answer_key"]


@dataclass
class KeyCheck:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def lines(self) -> list[str]:
        return [f"error: {text}" for text in self.errors] + [
            f"warning: {text}" for text in self.warnings
        ]


def _where(question, version: str) -> str:  # noqa: ANN001
    line = f" (line {question.source_line})" if question.source_line else ""
    prefix = f"version {version}, " if version else ""
    return f"{prefix}{question.id}{line}"


def _versions(document: QADocument) -> list[tuple[str, list]]:
    if document.version_questions:
        return list(document.version_questions.items())
    return [("", document.questions)]


def _has_answer(question) -> bool:  # noqa: ANN001
    return bool(
        question.answers
        or getattr(question, "correct_choice_label", None)
        or getattr(question, "correct_choice_labels", ())
    )


def check_answer_key(text: str) -> KeyCheck:
    result = KeyCheck()
    try:
        document = parse_qa_markdown(text)
    except QAParseError as exc:
        line = exc.location.line
        result.errors.append(f"line {line}: {exc}" if line else str(exc))
        return result
    except ValueError as exc:
        result.errors.append(str(exc))
        return result

    if not any(questions for _version, questions in _versions(document)):
        result.errors.append("the answer key has no questions")
        return result

    shapes: dict[str, list[str]] = {}
    for version, questions in _versions(document):
        seen_prompts: dict[str, str] = {}
        for question in questions:
            # A question with parts is graded by its parts; the parent carries no rubric.
            items = list(question.subquestions) or [question]
            for item in items:
                where = _where(item, version)
                prompt = " ".join(item.prompt.split()).casefold()
                if prompt and prompt in seen_prompts:
                    result.warnings.append(
                        f"{where} has the same text as {seen_prompts[prompt]}: two copies of one question?"
                    )
                elif prompt:
                    seen_prompts[prompt] = item.id
                if not item.points:
                    result.warnings.append(f"{where} is worth 0 points")
                labels = [option.label.casefold() for option in flatten_rubric_options(item.rubric)]
                duplicates = sorted({label for label in labels if labels.count(label) > 1})
                if duplicates:
                    result.warnings.append(
                        f"{where} has the rubric option(s) {', '.join(repr(d) for d in duplicates)} more than once"
                    )
                if not _has_answer(item) and not item.rubric:
                    result.warnings.append(
                        f"{where} has no correct answer and no rubric, so nothing can grade it but you"
                    )
        shapes[version] = [question.id for question in questions]

    if len(shapes) > 1:
        reference_version, reference = next(iter(shapes.items()))
        for version, ids in shapes.items():
            if len(ids) != len(reference):
                result.warnings.append(
                    f"version {version} has {len(ids)} question(s) and version "
                    f"{reference_version} has {len(reference)}"
                )
    return result
