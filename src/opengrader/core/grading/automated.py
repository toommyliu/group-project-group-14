"""Shared automatic grading and journal writes for local and server clients."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from opengrader.core import app_config
from opengrader.core.grading import suggest_free_response_grade
from opengrader.core.grading.answer_cache import AnswerGradeCache, answer_grade_key
from opengrader.core.grading.ownership import AutoGrades, Owner, same_points
from opengrader.core.grading.llm_grader import (
    GenerateFn,
    GradeSuggestion,
    format_ai_analysis,
)
from opengrader.core.grading.solutions import (
    MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE,
    SolutionEntry,
    needs_llm_grade,
    objective_correctness,
    solution_entries_for_submission,
    solution_version_for_submission,
)
from opengrader.core.llm import LLMError
from opengrader.core.submissions.printed_text import response_without_printed_text
from opengrader.core.qa_file import QADocument
from opengrader.core.history import GradeJournal, current_actor, resolve_history_dir
from opengrader.core.submissions import (
    StudentSubmissionDocument,
    StudentSubmissionGradeUpdate,
    StudentSubmissionQuestion,
    load_student_submission_markdown,
    update_student_submission_markdown,
)

DEFAULT_LLM_MIN_CONFIDENCE = app_config.DEFAULT_MIN_CONFIDENCE

JOURNAL_ACTOR_AUTO = "automated-grading"
JOURNAL_ACTOR_LLM = "automated-grading:llm"


class RunJournal:
    def __init__(self, submissions_path: Path, *, operator: str | None = None) -> None:
        self._dir = resolve_history_dir(submissions_path)
        self._operator = operator or current_actor()
        self._journals: dict[str, GradeJournal] = {}

    @property
    def operator(self) -> str:
        return self._operator

    def _journal_for(self, actor: str) -> GradeJournal:
        journal = self._journals.get(actor)
        if journal is None:
            journal = GradeJournal(self._dir, actor=actor)
            self._journals[actor] = journal
        return journal

    def record_grade(
        self,
        *,
        actor: str,
        document: StudentSubmissionDocument,
        question_id: str,
        summary: str,
        old_points: float | None,
        old_rubric: tuple[str, ...],
        new_points: float | None,
        new_rubric: tuple[str, ...],
    ) -> None:
        try:
            self._journal_for(actor).append(
                kind="grade",
                student_id=document.student_id,
                student_name=document.student_name,
                question_id=question_id,
                summary=f"{summary} (run by {self._operator})",
                old={"points": old_points, "rubric": list(old_rubric)},
                new={"points": new_points, "rubric": list(new_rubric)},
            )
        except OSError:
            pass

    def record_comment(
        self,
        *,
        actor: str,
        document: StudentSubmissionDocument,
        question_id: str,
        old_analysis: str,
        new_analysis: str,
    ) -> None:
        try:
            self._journal_for(actor).append(
                kind="comment",
                student_id=document.student_id,
                student_name=document.student_name,
                question_id=question_id,
                summary=f"auto analysis (run by {self._operator})",
                old={"analysis": old_analysis},
                new={"analysis": new_analysis},
            )
        except OSError:
            pass


def grade_student_file(
    student_path: Path,
    qa_doc: QADocument,
    *,
    llm_generate: GenerateFn | None = None,
    llm_min_confidence: float = DEFAULT_LLM_MIN_CONFIDENCE,
    llm_model: str = "",
    llm_provider: str = "",
    min_transcription_confidence: float = MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE,
    answer_cache: AnswerGradeCache | None = None,
    dry_run: bool = False,
    question_callback: Callable[[str], None] | None = None,
    journal: "RunJournal | None" = None,
    auto_grades: AutoGrades | None = None,
    overwrite_grades: bool = False,
) -> tuple[str, ...]:
    """Grade one submission file in place. Returns the questions where a
    person's grade was kept although auto-grading would score them otherwise.

    With `auto_grades`, a grade a person gave is never scored over (see
    `ownership`); `overwrite_grades` re-scores those too. The caller saves
    `auto_grades` once its run is over.
    """
    document = load_student_submission_markdown(student_path)
    if qa_doc.version_questions:
        from opengrader.core.grading.versions import match_version

        match = match_version(qa_doc, document)
        if not match.known:
            # Grading it against a guessed version would look right and be
            # wrong; the review queue asks a person instead.
            print(
                f"Skipped {student_path.name}: {match.describe()} "
                "(review queue: press R in the grading app)"
            )
            return ()
    solutions = solution_entries_for_submission(qa_doc, document)
    version = solution_version_for_submission(qa_doc, document)
    cache = answer_cache if answer_cache is not None else AnswerGradeCache()

    content = student_path.read_text()
    updated = content
    ai_applied: list[str] = []
    ai_skipped: list[str] = []
    unreadable_skipped: list[str] = []
    kept: list[str] = []
    pending_journal: list[_PendingRecord] = []
    written: list[tuple[str, float | None, tuple[str, ...], str]] = []
    for question in document.questions:
        entry = solutions.get(question.question_id)
        if entry is None:
            continue

        response = response_without_printed_text(
            question.response_text,
            source=question.response_source,
            printed=entry.printed_text,
        ).strip()
        if not response:
            continue

        if question_callback is not None:
            question_callback(question.question_id)

        objective_correct = objective_correctness(entry, response, question)

        points_awarded: float | None = None
        rubric_labels: tuple[str, ...] | None = None
        analysis: str | None = "Correct" if objective_correct else None

        if objective_correct is not None:
            points_awarded = entry.points if objective_correct else 0.0
        elif not needs_llm_grade(
            entry,
            objective_correct,
            question,
            min_transcription_confidence=min_transcription_confidence,
        ):
            if entry.is_numeric:
                pass
            elif question.response_confidence is not None:
                unreadable_skipped.append(
                    f"{question.question_id} "
                    f"(transcription {question.response_confidence:.0f}% < "
                    f"{min_transcription_confidence:g}%)"
                )
        elif llm_generate is not None and question.points_awarded is None:
            suggestion = _suggest_or_warn(
                question_id=question.question_id,
                response=response,
                entry=entry,
                llm_generate=llm_generate,
                cache=cache,
                key=answer_grade_key(
                    question_id=question.question_id,
                    assignment_version=version,
                    response_text=response,
                    question_type=question.question_type,
                ),
            )
            if suggestion is None:
                pass
            elif suggestion.confidence >= llm_min_confidence:
                points_awarded = suggestion.points_awarded
                rubric_labels = suggestion.rubric_labels
                analysis = format_ai_analysis(
                    suggestion, model=llm_model, provider=llm_provider
                )
                ai_applied.append(
                    f"{question.question_id}: {', '.join(rubric_labels)} "
                    f"= {points_awarded:g}/{entry.points:g} "
                    f"({round(suggestion.confidence * 100)}%)"
                )
            else:
                ai_skipped.append(
                    f"{question.question_id} "
                    f"({round(suggestion.confidence * 100)}% < "
                    f"{round(llm_min_confidence * 100)}%)"
                )

        if auto_grades is not None and not overwrite_grades and points_awarded is not None:
            if auto_grades.grade_owner(student_path, document, question) is Owner.PERSON:
                new_rubric = tuple(question.rubric_labels if rubric_labels is None else rubric_labels)
                if not same_points(points_awarded, question.points_awarded) or new_rubric != tuple(
                    question.rubric_labels
                ):
                    kept.append(
                        f"{question.question_id} (auto-grade gives {points_awarded:g}/{entry.points:g})"
                    )
                points_awarded, rubric_labels, analysis = None, None, None
            else:
                comment = auto_grades.comment_owner(student_path, document, question)
                if comment is Owner.PERSON:
                    analysis = None
                elif analysis is None and comment is Owner.AUTO:
                    # Its own "Correct" from before, on an answer it now scores otherwise.
                    analysis = ""

        updated = update_student_submission_markdown(
            updated,
            StudentSubmissionGradeUpdate(
                question_id=question.question_id,
                possible_points=entry.points,
                points_awarded=points_awarded,
                rubric_labels=rubric_labels,
                analysis=analysis,
            ),
        )
        if points_awarded is not None:
            written.append(
                (
                    question.question_id,
                    points_awarded,
                    tuple(question.rubric_labels if rubric_labels is None else rubric_labels),
                    question.analysis if analysis is None else analysis,
                )
            )

        if journal is not None:
            pending_journal.extend(
                _pending_records_for_question(
                    question=question,
                    ai_applied=rubric_labels is not None,
                    points_awarded=points_awarded,
                    rubric_labels=rubric_labels,
                    analysis=analysis,
                )
            )

    if dry_run:
        print(f"[dry run] {student_path.name}: no changes written.")
    else:
        if updated != content:
            student_path.write_text(updated)
            if journal is not None:
                _flush_journal(journal, document, pending_journal)
        if auto_grades is not None:
            for question_id, points, rubric, analysis in written:
                auto_grades.remember(
                    student_path, question_id, points=points, rubric=rubric, analysis=analysis
                )
        print(f"Successfully graded and updated {student_path.name}")

    for line in ai_applied:
        print(f"  AI graded {line}")
    for line in ai_skipped:
        print(f"  AI low confidence, left for manual review: {line}")
    for line in unreadable_skipped:
        print(f"  Transcription too weak to grade, left for manual review: {line}")
    for line in kept:
        print(f"  Kept a grade a person gave: {line}")
    return tuple(kept)



@dataclass(frozen=True)
class _PendingRecord:
    actor: str
    kind: str
    question_id: str
    summary: str
    old_points: float | None = None
    old_rubric: tuple[str, ...] = ()
    new_points: float | None = None
    new_rubric: tuple[str, ...] = ()
    old_analysis: str = ""
    new_analysis: str = ""


def _pending_records_for_question(
    *,
    question: "StudentSubmissionQuestion",
    ai_applied: bool,
    points_awarded: float | None,
    rubric_labels: tuple[str, ...] | None,
    analysis: str | None,
) -> list[_PendingRecord]:
    actor = JOURNAL_ACTOR_LLM if ai_applied else JOURNAL_ACTOR_AUTO
    records: list[_PendingRecord] = []

    if points_awarded is not None:
        new_rubric = tuple(rubric_labels or ())
        old_points = question.points_awarded
        old_rubric = tuple(question.rubric_labels)
        changed = old_points != points_awarded or old_rubric != new_rubric
        if changed:
            records.append(
                _PendingRecord(
                    actor=actor,
                    kind="grade",
                    question_id=question.question_id,
                    summary=f"auto graded {points_awarded:g}",
                    old_points=old_points,
                    old_rubric=old_rubric,
                    new_points=points_awarded,
                    new_rubric=new_rubric,
                )
            )

    if analysis is not None and analysis != (question.analysis or ""):
        records.append(
            _PendingRecord(
                actor=actor,
                kind="comment",
                question_id=question.question_id,
                summary="auto analysis",
                old_analysis=question.analysis or "",
                new_analysis=analysis,
            )
        )
    return records


def _flush_journal(
    journal: "RunJournal",
    document: StudentSubmissionDocument,
    records: list[_PendingRecord],
) -> None:
    for record in records:
        if record.kind == "grade":
            journal.record_grade(
                actor=record.actor,
                document=document,
                question_id=record.question_id,
                summary=record.summary,
                old_points=record.old_points,
                old_rubric=record.old_rubric,
                new_points=record.new_points,
                new_rubric=record.new_rubric,
            )
        else:
            journal.record_comment(
                actor=record.actor,
                document=document,
                question_id=record.question_id,
                old_analysis=record.old_analysis,
                new_analysis=record.new_analysis,
            )


def _suggest_or_warn(
    *,
    question_id: str,
    response: str,
    entry: SolutionEntry,
    llm_generate: GenerateFn,
    cache: AnswerGradeCache,
    key,  # noqa: ANN001
) -> GradeSuggestion | None:
    try:
        return cache.suggest(
            key,
            lambda: suggest_free_response_grade(
                prompt=entry.prompt,
                expected_answers=list(entry.expected_answers),
                response_text=response,
                rubric_options=list(entry.rubric_options),
                possible_points=entry.points,
                generate=llm_generate,
            ),
        )
    except LLMError as exc:
        print(f"  LLM grading failed for {question_id}: {exc}", file=sys.stderr)
        return None


@dataclass(frozen=True)
class GradingCoverage:
    """How much of a folder auto-grading scored, and why the rest is left."""

    scored: int
    total: int
    # No response text: nothing was read off the page (handwriting with no
    # AI reader, usually), so not even multiple choice can be scored.
    unread: int

    @property
    def left(self) -> int:
        return self.total - self.scored


def grading_coverage(submissions_dir: str | Path) -> GradingCoverage:
    """Every answer across a folder's submissions: scored, or left for a
    person and whether it was read at all. An unreadable file counts for
    nothing."""
    scored = total = unread = 0
    for path in sorted(Path(submissions_dir).rglob("*.submission.md")):
        if path.name.startswith("._"):
            continue
        try:
            document = load_student_submission_markdown(path)
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        for question in document.questions:
            total += 1
            if question.points_awarded is not None:
                scored += 1
            elif not (question.response_text or "").strip():
                unread += 1
    return GradingCoverage(scored=scored, total=total, unread=unread)
