"""Question context, answer-key version selection, and rubric options."""

from __future__ import annotations

import re
from dataclasses import dataclass

from opengrader.core.qa_file import (
    QADocument,
    QAQuestion,
    QARubricFlatOption,
    QARubricItem,
    flatten_rubric_options,
    qa_question_type,
)
from opengrader.core.question_types import FREE_RESPONSE, QuestionType
from opengrader.core.submissions import StudentSubmissionDocument

_VERSION_MARKER_RE = re.compile(r"\[([A-Za-z0-9]+)\]|\(([A-Za-z0-9]+)\)")


@dataclass(frozen=True)
class QuestionContext:
    question_id: str
    prompt: str
    points: float
    expected_answers: list[str]
    rubric: list[QARubricItem]
    question_type: QuestionType = FREE_RESPONSE


def questions_for_submission(
    qa: QADocument,
    document: StudentSubmissionDocument,
) -> tuple[str | None, list[QAQuestion]]:
    """Match local grading's explicit, inferred, then first-version fallback."""
    if not qa.version_questions:
        return None, qa.questions

    available = {version.strip().lower(): version for version in qa.version_questions}
    candidates: set[str] = set()
    for text in [document.assignment_title, *(q.prompt for q in document.questions)]:
        for match in _VERSION_MARKER_RE.finditer(text or ""):
            raw = match.group(1) or match.group(2)
            version = available.get(raw.strip().lower())
            if version is not None:
                candidates.add(version)
    inferred = next(iter(candidates)) if len(candidates) == 1 else ""
    explicit = (document.assignment_version or "").strip()
    selected = available.get((explicit or inferred).lower())
    if selected is None and explicit and inferred:
        selected = available.get(inferred.lower())
    if selected is None:
        selected = next(iter(qa.version_questions))
    return selected, qa.version_questions[selected]


def flatten_question_contexts(questions: list[QAQuestion]) -> list[QuestionContext]:
    """Use each multipart question's graded parts, as local grading does."""
    contexts: list[QuestionContext] = []
    for parent in questions:
        for question in parent.subquestions or [parent]:
            answers = list(question.correct_choice_labels)
            if not answers:
                answers = (
                    [question.correct_choice_label]
                    if question.correct_choice_label
                    else list(question.answers)
                )
            contexts.append(
                QuestionContext(
                    question_id=question.id,
                    prompt=question.prompt,
                    points=float(question.points),
                    expected_answers=answers,
                    rubric=question.rubric,
                    question_type=qa_question_type(question),
                )
            )
    return contexts


def rubric_options(context: QuestionContext) -> list[QARubricFlatOption]:
    return flatten_rubric_options(context.rubric) or [
        QARubricFlatOption("Correct", 0.0, "correct", "exclusive"),
        QARubricFlatOption("Incorrect", -context.points, "incorrect", "exclusive"),
    ]
