from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from opengrader.core.grading.llm_grader import RubricChoice
from opengrader.core.qa_file import (
    NumericAnswerSpec,
    QADocument,
    QAQuestion,
    flatten_rubric_options,
    grade_numeric_response,
    parse_numeric_answers,
)
from opengrader.core.qa_file.markdown import QAChoice
from opengrader.core.question_types import MULTIPLE_CHOICE, normalize_boolean
from opengrader.core.submissions.printed_text import printed_question_text

MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE = 60.0

if TYPE_CHECKING:
    from opengrader.core.submissions.student_file import (
        StudentSubmissionDocument,
        StudentSubmissionQuestion,
    )


@dataclass(frozen=True)
class SolutionEntry:
    points: float
    expected_answers: tuple[str, ...]
    is_multiple_choice: bool = False
    is_multiple_answer: bool = False
    numeric_specs: tuple[NumericAnswerSpec, ...] = ()
    true_false_correct: bool | None = None
    prompt: str = ""
    rubric_options: tuple[RubricChoice, ...] = ()
    # Printed question text Tesseract may have read into the response.
    printed_text: tuple[str, ...] = ()

    @property
    def is_numeric(self) -> bool:
        return bool(self.numeric_specs)


def rubric_choices(rubric: object, points: float) -> tuple[RubricChoice, ...]:
    options = [
        RubricChoice(
            label=option.label,
            points_delta=option.points_delta,
            kind=option.kind,
            selection_mode=option.selection_mode,
        )
        for option in flatten_rubric_options(rubric)  # type: ignore[arg-type]
    ]
    if options:
        return tuple(options)
    return (
        RubricChoice("Correct", 0.0, "correct", "exclusive"),
        RubricChoice("Incorrect", -points, "incorrect", "exclusive"),
    )


def true_false_correct(
    choices: list[QAChoice], correct_choice_label: str | None
) -> bool | None:
    if len(choices) != 2:
        return None
    if {choice.text.strip().lower() for choice in choices} != {"true", "false"}:
        return None
    correct = next(
        (choice for choice in choices if choice.label == correct_choice_label), None
    )
    if correct is None:
        return None
    return normalize_boolean(correct.text)


def make_solution_entry(
    points: float,
    answers: list[str],
    choices: list[QAChoice],
    correct_choice_label: str | None,
    correct_choice_labels: tuple[str, ...] = (),
    *,
    prompt: str = "",
    rubric: object = (),
    printed_text: tuple[str, ...] = (),
) -> SolutionEntry:
    if correct_choice_labels:
        return SolutionEntry(
            points=points,
            expected_answers=tuple(correct_choice_labels),
            is_multiple_answer=True,
            prompt=prompt,
            rubric_options=rubric_choices(rubric, points),
            printed_text=printed_text,
        )
    if correct_choice_label:
        return SolutionEntry(
            points=points,
            expected_answers=(correct_choice_label,),
            is_multiple_choice=True,
            true_false_correct=true_false_correct(choices, correct_choice_label),
            prompt=prompt,
            rubric_options=rubric_choices(rubric, points),
            printed_text=printed_text,
        )
    numeric_specs = parse_numeric_answers(answers)
    return SolutionEntry(
        points=points,
        expected_answers=tuple(answers),
        numeric_specs=tuple(numeric_specs) if numeric_specs is not None else (),
        prompt=prompt,
        rubric_options=rubric_choices(rubric, points),
        printed_text=printed_text,
    )


def normalize_choice_response(response: str) -> str:
    return response.strip().rstrip(".):").strip().upper()


def choice_label_set(response: str) -> frozenset[str]:
    without_and = re.sub(r"\band\b", " ", response, flags=re.IGNORECASE)
    return frozenset(re.sub(r"[^A-Za-z]", "", without_and).upper())


def flatten_solution_entries(
    questions: list[QAQuestion],
) -> dict[str, SolutionEntry]:
    entries: dict[str, SolutionEntry] = {}
    for question in questions:
        if question.subquestions:
            for subquestion in question.subquestions:
                entries[subquestion.id] = make_solution_entry(
                    float(subquestion.points),
                    subquestion.answers,
                    subquestion.choices,
                    subquestion.correct_choice_label,
                    subquestion.correct_choice_labels,
                    prompt=subquestion.prompt,
                    rubric=subquestion.rubric,
                    printed_text=printed_question_text(question),
                )
            continue
        entries[question.id] = make_solution_entry(
            float(question.points),
            question.answers,
            question.choices,
            question.correct_choice_label,
            question.correct_choice_labels,
            prompt=question.prompt,
            rubric=question.rubric,
            printed_text=printed_question_text(question),
        )
    return entries


def solution_version_for_submission(
    qa_doc: QADocument,
    document: StudentSubmissionDocument,
) -> str:
    if not qa_doc.version_questions:
        return ""

    version = (document.assignment_version or "").strip()
    version_by_normalized = {
        candidate.strip().lower(): candidate for candidate in qa_doc.version_questions
    }
    selected = version_by_normalized.get(version.lower()) if version else None
    if selected is None:
        selected = next(iter(qa_doc.version_questions))
    return selected


def solution_entries_for_submission(
    qa_doc: QADocument,
    document: StudentSubmissionDocument,
) -> dict[str, SolutionEntry]:
    version = solution_version_for_submission(qa_doc, document)
    if not version:
        return flatten_solution_entries(qa_doc.questions)
    return flatten_solution_entries(qa_doc.version_questions[version])


def objective_correctness(
    entry: SolutionEntry,
    response: str,
    question: StudentSubmissionQuestion,
) -> bool | None:
    """None means ungraded, including numeric and select-all misses."""
    if entry.true_false_correct is not None:
        parsed = normalize_boolean(response)
        if parsed is not None:
            return parsed == entry.true_false_correct
        return normalize_choice_response(response) in entry.expected_answers

    if entry.is_multiple_answer:
        return (
            True
            if choice_label_set(response) == frozenset(entry.expected_answers)
            else None
        )

    if entry.is_multiple_choice:
        return normalize_choice_response(response) in entry.expected_answers

    if entry.is_numeric:
        return (
            True if grade_numeric_response(response, list(entry.numeric_specs)) else None
        )

    if question.question_type == MULTIPLE_CHOICE:
        return normalize_choice_response(response) in entry.expected_answers

    if response in entry.expected_answers:
        return True
    return None


def transcription_is_trusted(
    response_confidence: float | None,
    *,
    floor: float = MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE,
) -> bool:
    if response_confidence is None:
        return True
    return response_confidence >= floor


def needs_llm_grade(
    entry: SolutionEntry,
    objective_correct: bool | None,
    question: StudentSubmissionQuestion | None = None,
    *,
    min_transcription_confidence: float = MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE,
) -> bool:
    """objective_correct must describe the same response. Omitting question skips the
    transcription-confidence check.
    """
    if objective_correct is not None or entry.is_numeric:
        return False
    if question is not None and not transcription_is_trusted(
        question.response_confidence, floor=min_transcription_confidence
    ):
        return False
    return True
