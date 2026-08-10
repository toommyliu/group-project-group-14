"""Answer-key lookup and objective (rule-based) grading decisions.

This is the half of automated grading that needs no model: turning a QA
solutions document into a per-question ``SolutionEntry``, and deciding whether
a student's response can be graded by rule (multiple choice, True/False,
numeric-within-tolerance, an exact free-response match) or has to be judged.

It lives in ``core`` rather than in ``cli/automated_grading.py`` because two
callers must agree exactly on **which answers a model is allowed to grade**:

* ``cli/automated_grading.py`` — production grading, which sends those answers
  to the LLM and writes the result;
* ``cli/llm_eval.py`` — the evaluation harness, which sends the *same* answers
  to the LLM and compares the result against a human's grade.

If the two ever disagreed about the candidate set, the harness would measure a
population the real run never grades, and the ``--llm-min-confidence`` an
instructor picked from it would be calibrated against the wrong thing. That is
what ``needs_llm_grade`` exists to prevent — both callers route through it.
"""

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

# Below this OCR/transcription confidence (0-100) the recorded ``Response
# text`` is not solid enough to grade the *words* of, so it goes to a human
# instead of to a model. See ``needs_llm_grade`` for why, and
# ``core/submissions/review.py`` — whose ``DEFAULT_OCR_CONFIDENCE_FLOOR`` is
# this same number, imported rather than repeated, so "a transcription we do
# not trust" means one thing to the grader and to the review queue.
#
# It lives here, in the module both grading callers already share, because
# ``core.submissions`` imports *into* ``core.grading`` (for the AI tag) and the
# reverse would be a cycle.
MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE = 60.0

if TYPE_CHECKING:  # Types only — `core.submissions` imports back into `core.grading`
    # (review.py reads the AI analysis tag), and a runtime import here would
    # make that a cycle. Nothing below needs the classes at run time.
    from opengrader.core.submissions.student_file import (
        StudentSubmissionDocument,
        StudentSubmissionQuestion,
    )


@dataclass(frozen=True)
class SolutionEntry:
    """Everything the grader needs to score one question from the answer key."""

    points: float
    expected_answers: tuple[str, ...]
    is_multiple_choice: bool = False
    # "Select all that apply": expected_answers is the full set of correct choice
    # labels, graded all-or-nothing (the exact set earns full credit).
    is_multiple_answer: bool = False
    # Populated for numeric questions (answers all parse as numeric specs); the
    # response is then graded with tolerance instead of exact text matching.
    numeric_specs: tuple[NumericAnswerSpec, ...] = ()
    # ``True``/``False`` when this is a True/False question (a constrained
    # two-option multiple choice), so a word answer ("True"/"F"/"yes") grades
    # even when the OCR did not capture the A/B choice label.
    true_false_correct: bool | None = None
    prompt: str = ""
    rubric_options: tuple[RubricChoice, ...] = ()

    @property
    def is_numeric(self) -> bool:
        return bool(self.numeric_specs)


def rubric_choices(rubric: object, points: float) -> tuple[RubricChoice, ...]:
    """Flatten a QA rubric into gradable choices for the LLM grader.

    Falls back to a synthetic Correct/Incorrect pair when a question declares
    no rubric, matching how the TUI provider seeds rubric-less questions.
    """
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
    """Return the boolean answer when ``choices`` is a True/False pair, else None."""
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
) -> SolutionEntry:
    if correct_choice_labels:
        # Select all that apply: grade the exact set, all-or-nothing.
        return SolutionEntry(
            points=points,
            expected_answers=tuple(correct_choice_labels),
            is_multiple_answer=True,
            prompt=prompt,
            rubric_options=rubric_choices(rubric, points),
        )
    if correct_choice_label:
        return SolutionEntry(
            points=points,
            expected_answers=(correct_choice_label,),
            is_multiple_choice=True,
            true_false_correct=true_false_correct(choices, correct_choice_label),
            prompt=prompt,
            rubric_options=rubric_choices(rubric, points),
        )
    numeric_specs = parse_numeric_answers(answers)
    return SolutionEntry(
        points=points,
        expected_answers=tuple(answers),
        numeric_specs=tuple(numeric_specs) if numeric_specs is not None else (),
        prompt=prompt,
        rubric_options=rubric_choices(rubric, points),
    )


def normalize_choice_response(response: str) -> str:
    return response.strip().rstrip(".):").strip().upper()


def choice_label_set(response: str) -> frozenset[str]:
    """Parse a "select all that apply" response into its set of choice labels.

    Copes with the ways a marked-checkbox response gets transcribed — "A, C",
    "A and C", "AC", "a c" — by pulling out the single-letter choice labels.
    """
    without_and = re.sub(r"\band\b", " ", response, flags=re.IGNORECASE)
    return frozenset(re.sub(r"[^A-Za-z]", "", without_and).upper())


def flatten_solution_entries(
    questions: list[QAQuestion],
) -> dict[str, SolutionEntry]:
    """Map gradable question IDs to their points and correct answers.

    Questions with subquestions are graded per subquestion, matching how
    submission files record them (q2a, q2b, ...).
    """
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
        )
    return entries


def solution_version_for_submission(
    qa_doc: QADocument,
    document: StudentSubmissionDocument,
) -> str:
    """Name the answer-key version this submission is graded against.

    ``""`` for a single-version exam. The *resolved* name, not the frontmatter's
    — a submission whose version is missing or misread falls back to the first
    version, and callers grouping answers across students need the two to land
    in the same bucket rather than in one bucket each.
    """
    if not qa_doc.version_questions:
        return ""

    version = (document.assignment_version or "").strip()
    version_by_normalized = {
        candidate.strip().lower(): candidate for candidate in qa_doc.version_questions
    }
    selected = version_by_normalized.get(version.lower()) if version else None
    if selected is None:
        # A version is optional. A missing — or unmatched/misread — version falls
        # back to the first QA version so one bad version string never blocks
        # auto-grading the whole cohort.
        selected = next(iter(qa_doc.version_questions))
    return selected


def solution_entries_for_submission(
    qa_doc: QADocument,
    document: StudentSubmissionDocument,
) -> dict[str, SolutionEntry]:
    """Pick the answer key that applies to one submission's assignment version."""
    version = solution_version_for_submission(qa_doc, document)
    if not version:
        return flatten_solution_entries(qa_doc.questions)
    return flatten_solution_entries(qa_doc.version_questions[version])


def objective_correctness(
    entry: SolutionEntry,
    response: str,
    question: StudentSubmissionQuestion,
) -> bool | None:
    """Grade an objective response, or return ``None`` if it isn't objective.

    ``True``/``False`` means the response is definitively right/wrong (full or
    zero credit). ``None`` means the question can't be safely auto-graded here —
    a free response, or a numeric answer that did not match — so the caller
    leaves it for manual grading (or the LLM, for free responses).

    Numeric is deliberately award-only: a *match within tolerance* earns full
    credit, but a mismatch is never auto-zeroed. Numeric handwriting is easy to
    misread and a "wrong" number is often an OCR/transcription artifact or work
    shown alongside the answer, so a human confirms every numeric miss.
    """
    if entry.true_false_correct is not None:
        # Prefer the boolean the student wrote ("True"/"F"/"yes"); fall back to
        # the A/B choice label when the transcription is just the letter.
        parsed = normalize_boolean(response)
        if parsed is not None:
            return parsed == entry.true_false_correct
        return normalize_choice_response(response) in entry.expected_answers

    if entry.is_multiple_answer:
        # Select all that apply, graded all-or-nothing: full credit only when the
        # student's checked set exactly equals the correct set. A mismatch
        # returns None (left for manual review) rather than auto-zeroing —
        # multi-checkbox transcription is error-prone, so a human confirms, the
        # same conservative stance the numeric branch below takes.
        return (
            True
            if choice_label_set(response) == frozenset(entry.expected_answers)
            else None
        )

    if entry.is_multiple_choice:
        # Handwritten choice labels are transcribed with varying case and
        # punctuation ("b", "B.", "B)") — normalize before comparing.
        return normalize_choice_response(response) in entry.expected_answers

    if entry.is_numeric:
        # A number within tolerance earns full credit; a mismatch (or an
        # unreadable response) returns None so it is left for manual review
        # rather than auto-zeroed. The QA answer key is authoritative here, so
        # this precedes the extractor's classification.
        return (
            True if grade_numeric_response(response, list(entry.numeric_specs)) else None
        )

    if question.question_type == MULTIPLE_CHOICE:
        # No structural signal from the QA file, but the extractor classified
        # the crop as multiple choice — compare against the expected label(s).
        return normalize_choice_response(response) in entry.expected_answers

    # Free response: an exact match earns full credit; anything else is not
    # objectively wrong, so leave it for the LLM or a human.
    if response in entry.expected_answers:
        return True
    return None


def transcription_is_trusted(
    response_confidence: float | None,
    *,
    floor: float = MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE,
) -> bool:
    """Whether a transcription is solid enough to grade the *text* of.

    ``None`` is trusted, and that is the load-bearing part. It means the
    confidence was never measured — a vision-model read, or a file extracted
    before confidence was recorded — which is the convention the whole codebase
    keeps (``review.py`` deliberately does not flag a ``None``): **unmeasured,
    never "bad"**. Treating it as untrusted would withdraw LLM grading from
    essentially every handwritten answer, since the VLM path is what reads
    handwriting and it never reports a number.

    A ``floor`` of 0 disables the check, which is what the CLI flag's 0 means.
    """
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
    """Whether a *model*, rather than a rule, has to decide this answer's grade.

    ``objective_correct`` is the value ``objective_correctness`` just returned
    for the same response, passed in so callers don't grade twice. ``question``
    is the same one that call took — the two functions that decide an answer's
    fate deliberately read the same three inputs.

    Three exclusions, in order:

    * an answer graded by rule (``objective_correct is not None``) never reaches
      a model — no multiple choice, no True/False, no numeric match;
    * a numeric **miss** is excluded too. It is not objectively wrong (the
      transcription may be at fault) so it left the rule path unscored, but it
      goes to a human rather than to a model that grades prose, not numbers;
    * an answer whose **transcription** the OCR is not confident it read. The
      model grades ``Response text`` and never sees the image, so below the
      floor it would be grading a string nobody vouched for — confidently, and
      with its own confidence describing only how sure it is about the garbled
      text. This is the numeric branch's stance ("a wrong value is often a
      transcription artifact") applied to prose, which had no such protection.
      The floor is deliberately the review queue's
      ``LOW_CONFIDENCE_TRANSCRIPTION`` floor, so every answer this declines is
      already flagged for a human and the gate creates no silent orphans.

    Everything else is a judgement call: a free response — or a "select all that
    apply" whose checked set didn't match — that a rule cannot settle. This is
    the single definition of the model's job; see the module docstring for why
    the evaluation harness must share it.

    ``question`` defaults to ``None`` for the historical two-argument form,
    which simply skips the transcription check (there is no confidence to read).
    """
    if objective_correct is not None or entry.is_numeric:
        return False
    if question is not None and not transcription_is_trusted(
        question.response_confidence, floor=min_transcription_confidence
    ):
        return False
    return True
