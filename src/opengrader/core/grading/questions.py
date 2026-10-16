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
from opengrader.core.submissions.printed_text import printed_question_text

@dataclass(frozen=True)
class QuestionContext:
    question_id: str
    prompt: str
    points: float
    expected_answers: list[str]
    rubric: list[QARubricItem]
    question_type: QuestionType = FREE_RESPONSE
    # Printed question text Tesseract may have read into the response.
    printed_text: tuple[str, ...] = ()


def questions_for_submission(
    qa: QADocument,
    document: StudentSubmissionDocument,
) -> tuple[str | None, list[QAQuestion]]:
    """The version this paper is graded against, and its questions.

    `versions.match_version` decides. A paper whose version is unknown gets
    (None, the first version's questions): the questions to show, since
    every version has the same question IDs, but no version to grade by —
    the grading app refuses to save on it until a version is picked.
    """
    from opengrader.core.grading.versions import match_version

    if not qa.version_questions:
        return None, qa.questions
    match = match_version(qa, document)
    if match.version is None:
        return None, next(iter(qa.version_questions.values()))
    return match.version, qa.version_questions[match.version]


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
                    printed_text=printed_question_text(parent),
                )
            )
    return contexts


def rubric_options(context: QuestionContext) -> list[QARubricFlatOption]:
    return flatten_rubric_options(context.rubric) or [
        QARubricFlatOption("Correct", 0.0, "correct", "exclusive"),
        QARubricFlatOption("Incorrect", -context.points, "incorrect", "exclusive"),
    ]
