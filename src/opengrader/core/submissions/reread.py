"""Reading a submission again without losing what a person did to it.

A `.submission.md` is both the scan's reading and, in a class folder, the
grading: the Points, Rubric and Analysis of every answer, a reading a
person typed in (`Read by: human`), and a name or ID a person set (both
drop the machine's confidence). The extractor writes the file whole, so
reading the scan again used to write over all of that.

Two merges, for the two reasons a scan is read again:

- `keep_teacher_work`: something about the reading changed (a blank
  template, a setting, the answer key's regions). The new reading is taken
  and the person's work is laid back over it, question by question. Where a
  graded answer now reads differently its grade is kept and the file says
  so, because the grade was given against the old reading.
- `ai_reading_upgrades`: nothing changed but that an AI reader is available
  now and was not when the scan was first read. Only answers nobody has
  graded or typed in are re-read; everything else stays exactly as it was.

Both only ever run on a re-read of the *same* PDF (the caller checks the
ledger's fingerprint). When the split changed, `submission-0002` may now be
a different student, and moving grades between files is what the merge and
split tools do explicitly.

A folder read before the ledger recorded fingerprints has nothing to check
against. There `same_student` decides instead: the merge goes ahead only
when the new reading names the same student the file does, and a graded
file that cannot be matched is left as it was.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .student_file import (
    StudentSubmissionDocument,
    StudentSubmissionGradeUpdate,
    StudentSubmissionQuestion,
    set_student_identity_markdown,
    set_submission_frontmatter_markdown,
    update_student_submission_markdown,
)
from .transcription import SOURCE_HUMAN


def is_graded(question: StudentSubmissionQuestion) -> bool:
    return (
        question.points_awarded is not None
        or bool(question.rubric_labels)
        or bool(question.analysis.strip())
    )


def _typed_in(question: StudentSubmissionQuestion) -> bool:
    return question.response_source == SOURCE_HUMAN


def _id_set_by_person(document: StudentSubmissionDocument) -> bool:
    return bool(document.student_id) and document.id_confidence is None and not document.id_candidates


def _name_set_by_person(document: StudentSubmissionDocument) -> bool:
    return bool(document.student_name) and document.name_confidence is None


def has_teacher_work(document: StudentSubmissionDocument) -> bool:
    """Whether reading this file again could lose something a person did."""
    return (
        any(is_graded(question) or _typed_in(question) for question in document.questions)
        or _id_set_by_person(document)
        or _name_set_by_person(document)
    )


def _same_text(left: str, right: str) -> bool:
    left, right = " ".join(left.split()).casefold(), " ".join(right.split()).casefold()
    return bool(left) and left == right


def same_student(existing: StudentSubmissionDocument, fresh: StudentSubmissionDocument) -> bool:
    """Whether a new reading is of the student a file already names: the
    same ID, or, where either has no ID, the same name. Two blank
    identities are not a match: nothing says who either one is."""
    if existing.student_id.strip() and fresh.student_id.strip():
        return _same_text(existing.student_id, fresh.student_id)
    return _same_text(existing.student_name, fresh.student_name)


@dataclass(frozen=True)
class KeptWork:
    # The new reading's file, with the person's work written back into it.
    markdown: str
    # Answers whose grade was kept although they now read differently.
    regraded_ids: tuple[str, ...] = ()
    # Graded answers the new reading no longer has (the answer key changed),
    # with the points they had, so the file still says what was given.
    dropped: tuple[tuple[str, float | None], ...] = ()
    kept_grades: int = 0

    @property
    def warnings(self) -> tuple[str, ...]:
        notes: list[str] = []
        if self.regraded_ids:
            notes.append(
                f"Read again after grading: {', '.join(self.regraded_ids)} now "
                "read differently; their grades were kept, so check them."
            )
        if self.dropped:
            given = ", ".join(
                question_id if points is None else f"{question_id} ({points:g} points)"
                for question_id, points in self.dropped
            )
            notes.append(f"Graded {given} no longer on this assignment; those grades were dropped.")
        return tuple(notes)


def keep_teacher_work(
    existing: StudentSubmissionDocument,
    fresh: StudentSubmissionDocument,
    fresh_markdown: str,
) -> KeptWork:
    """`fresh_markdown` (the new reading, whose parsed form is `fresh`) with
    the grades, typed-in readings and identity a person gave `existing`
    written back into it.

    The text is edited field by field, the way grading edits a file, rather
    than re-rendered from a parsed document: that round trip is not exact.
    """
    before = {question.question_id: question for question in existing.questions}
    markdown = fresh_markdown
    regraded: list[str] = []
    kept = 0
    for question in fresh.questions:
        old = before.get(question.question_id)
        if old is None:
            continue
        reading = question.response_text
        if _typed_in(old):
            reading = old.response_text
            markdown = update_student_submission_markdown(
                markdown,
                StudentSubmissionGradeUpdate(
                    question_id=question.question_id,
                    response_text=old.response_text,
                    response_confidence=old.response_confidence,
                    clear_response_confidence=old.response_confidence is None,
                    response_source=old.response_source,
                    response_provider=old.response_provider,
                ),
            )
        if is_graded(old):
            kept += 1
            if reading.strip() != old.response_text.strip():
                regraded.append(question.question_id)
            markdown = update_student_submission_markdown(
                markdown,
                StudentSubmissionGradeUpdate(
                    question_id=question.question_id,
                    points_awarded=old.points_awarded,
                    rubric_labels=old.rubric_labels,
                    analysis=old.analysis,
                ),
            )

    still_there = {question.question_id for question in fresh.questions}
    dropped = tuple(
        (question.question_id, question.points_awarded)
        for question in existing.questions
        if is_graded(question) and question.question_id not in still_there
    )

    if _id_set_by_person(existing):
        markdown = set_student_identity_markdown(markdown, student_id=existing.student_id)
    result = KeptWork(markdown, tuple(regraded), dropped, kept)
    warnings = tuple(dict.fromkeys((*fresh.scan_warnings, *result.warnings)))
    markdown = set_submission_frontmatter_markdown(
        markdown,
        student_name=existing.student_name if _name_set_by_person(existing) else None,
        student_score=existing.student_score if kept and existing.student_score else None,
        scan_warnings=warnings if result.warnings else None,
    )
    return replace(result, markdown=markdown)


@dataclass(frozen=True)
class AIReading:
    question_id: str
    old_text: str
    new_text: str
    old_confidence: float | None
    new_confidence: float | None
    old_source: str | None
    new_source: str | None
    new_provider: str | None


def ai_reading_upgrades(
    existing: StudentSubmissionDocument, fresh: StudentSubmissionDocument
) -> tuple[AIReading, ...]:
    """The new readings to adopt when an AI reader is newly available: every
    answer nobody graded or typed in, where the AI's reading differs."""
    after = {question.question_id: question for question in fresh.questions}
    upgrades: list[AIReading] = []
    for old in existing.questions:
        new = after.get(old.question_id)
        if new is None or is_graded(old) or _typed_in(old):
            continue
        text = new.response_text.strip()
        if not text or text == old.response_text.strip():
            continue
        upgrades.append(
            AIReading(
                question_id=old.question_id,
                old_text=old.response_text,
                new_text=text,
                old_confidence=old.response_confidence,
                new_confidence=new.response_confidence,
                old_source=old.response_source,
                new_source=new.response_source,
                new_provider=new.response_provider,
            )
        )
    return tuple(upgrades)
