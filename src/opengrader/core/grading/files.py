from __future__ import annotations

from pathlib import Path

from opengrader.core.concurrency import file_mtime_ns
from opengrader.core.curve import read_curve
from opengrader.core.history import (
    AspectValue,
    GradeJournal,
    journal_payload,
)
from opengrader.core.history.journal import JournalKind
from opengrader.core.pdf.annotations import (
    sync_feedback_summary_page,
    sync_question_comment_annotation,
    sync_total_score_annotation,
)
from opengrader.core.pdf.feedback import build_feedback_summary
from opengrader.core.submissions import (
    SOURCE_HUMAN,
    StudentSubmissionGradeUpdate,
    load_student_submission_markdown,
    update_student_submission_file,
)


class FileGradingSession:
    """Edit a submission file and maintain its grading history and feedback PDF."""

    def __init__(
        self,
        path: str | Path,
        *,
        submissions_path: str | Path,
        journal: GradeJournal,
    ) -> None:
        self.path = Path(path)
        self._submissions_path = Path(submissions_path)
        self._journal = journal
        self.reload()

    def reload(self) -> None:
        self.document = load_student_submission_markdown(self.path)
        self.questions_by_id = {
            question.question_id: question for question in self.document.questions
        }
        self.mtime_ns = file_mtime_ns(self.path)

    @property
    def source_pdf_path(self) -> Path:
        source = Path(self.document.source_pdf).expanduser()
        if not source.is_absolute():
            source = self.path.parent / source
        return source.resolve()

    def save_grade(
        self,
        *,
        question_id: str,
        possible_points: float,
        points_awarded: float | None,
        rubric_labels: tuple[str, ...],
        analysis: str | None = None,
    ) -> None:
        """Persist grade and feedback, record their history, then refresh the PDF."""
        old_question = self.questions_by_id.get(question_id)
        self._update(
            StudentSubmissionGradeUpdate(
                question_id=question_id,
                possible_points=possible_points,
                points_awarded=points_awarded,
                rubric_labels=rubric_labels,
                analysis=analysis,
            )
        )
        self._journal_event(
            kind="grade",
            question_id=question_id,
            summary=_format_grade_summary(points_awarded, possible_points, rubric_labels),
            old={
                "points": None if old_question is None else old_question.points_awarded,
                "rubric": [] if old_question is None else list(old_question.rubric_labels),
            },
            new={"points": points_awarded, "rubric": list(rubric_labels)},
        )

        previous_analysis = "" if old_question is None else old_question.analysis
        if analysis is not None and analysis != previous_analysis:
            self._journal_event(
                kind="comment", question_id=question_id, summary=_excerpt_comment(analysis),
                old={"analysis": previous_analysis}, new={"analysis": analysis},
            )
        # History must survive a failure while refreshing the derived PDF.
        try:
            self.sync_pdf_annotations(question_id)
        except FileNotFoundError:
            pass

    def stage_comment(self, *, question_id: str, analysis: str) -> None:
        """Persist draft text without rewriting the PDF or adding a history entry."""
        self._update(
            StudentSubmissionGradeUpdate(question_id=question_id, analysis=analysis)
        )

    def commit_comment(
        self,
        *,
        question_id: str,
        analysis: str,
        previous_analysis: str,
    ) -> None:
        """Record staged edits and refresh the PDF using the pre-edit baseline."""
        self._journal_event(
            kind="comment",
            question_id=question_id,
            summary=_excerpt_comment(analysis),
            old={"analysis": previous_analysis},
            new={"analysis": analysis},
        )
        try:
            self.sync_pdf_annotations(question_id, comment_override=analysis)
        except FileNotFoundError:
            pass

    def save_comment(self, *, question_id: str, analysis: str) -> None:
        """Persist a complete comment and update its history and PDF."""
        old_question = self.questions_by_id.get(question_id)
        previous_analysis = "" if old_question is None else old_question.analysis
        self.stage_comment(question_id=question_id, analysis=analysis)
        self.commit_comment(
            question_id=question_id,
            analysis=analysis,
            previous_analysis=previous_analysis,
        )

    def save_transcription(
        self,
        *,
        question_id: str,
        response_text: str,
        response_confidence: float | None = 100.0,
        response_source: str | None = SOURCE_HUMAN,
        response_provider: str | None = None,
    ) -> None:
        """Save a reading and its history; None clears confidence or reader metadata.

        `response_provider` rides on the `Read by` line with `response_source`
        (undo restores both); a human edit leaves it None, dropping the provider.
        """
        old_question = self.questions_by_id.get(question_id)
        self._update(
            StudentSubmissionGradeUpdate(
                question_id=question_id,
                response_text=response_text,
                response_confidence=response_confidence,
                clear_response_confidence=response_confidence is None,
                response_source=response_source,
                clear_response_source=response_source is None,
                response_provider=response_provider,
            )
        )
        old_value = AspectValue(
            text="" if old_question is None else old_question.response_text,
            confidence=None if old_question is None else old_question.response_confidence,
            source=None if old_question is None else old_question.response_source,
        )
        self._journal_event(
            kind="transcription",
            question_id=question_id,
            summary=_excerpt_comment(response_text),
            old=journal_payload("transcription", old_value),
            new=journal_payload(
                "transcription",
                AspectValue(
                    text=response_text,
                    confidence=response_confidence,
                    source=response_source,
                ),
            ),
        )

    def _update(self, update: StudentSubmissionGradeUpdate) -> None:
        update_student_submission_file(
            self.path, update, expected_mtime_ns=self.mtime_ns
        )
        self.reload()

    def sync_pdf_annotations(
        self, question_id: str, *, comment_override: str | None = None
    ) -> Path:
        """Update the derived PDF, leaving submission data and history untouched."""
        response = self.questions_by_id.get(question_id)
        if response is None:
            raise ValueError(f"question not found in submission: {question_id}")
        if comment_override is not None and not response.source_segments:
            raise ValueError(
                "Question source metadata missing; regenerate submission files to enable PDF annotations."
            )

        source_pdf_path = self.source_pdf_path
        annotated_pdf_path = (
            self.path.parent / "annotated-pdfs" / f"{source_pdf_path.stem}.annotated.pdf"
        )
        total_awarded = sum(
            q.points_awarded
            for q in self.document.questions
            if q.points_awarded is not None
        )
        total_possible = sum(
            q.possible_points
            for q in self.document.questions
            if q.possible_points is not None
        )
        wrote = sync_total_score_annotation(
            source_pdf_path=source_pdf_path,
            annotated_pdf_path=annotated_pdf_path,
            total_points=total_awarded,
            possible_points=total_possible,
        )
        if response.source_segments:
            wrote |= sync_question_comment_annotation(
                source_pdf_path=source_pdf_path,
                annotated_pdf_path=annotated_pdf_path,
                question_id=question_id,
                comment=(
                    comment_override
                    if comment_override is not None
                    else (response.analysis or "")
                ),
                points_awarded=response.points_awarded,
                possible_points=response.possible_points,
                source_segments=response.source_segments,
            )
        try:
            sync_feedback_summary_page(
                source_pdf_path=source_pdf_path,
                annotated_pdf_path=annotated_pdf_path,
                summary=build_feedback_summary(
                    self.document, curve=read_curve(self._submissions_path)
                ),
                force=wrote,
            )
        except Exception:
            pass
        return annotated_pdf_path

    def _journal_event(
        self,
        *,
        kind: JournalKind,
        question_id: str,
        summary: str,
        old: dict[str, object],
        new: dict[str, object],
    ) -> None:
        try:
            self._journal.append(
                kind=kind,
                student_id=self.document.student_id,
                student_name=self.document.student_name,
                question_id=question_id,
                summary=summary,
                old=old,
                new=new,
            )
        except OSError:
            pass


def _format_points(value: float | None) -> str:
    return "—" if value is None else f"{value:g}"


def _format_grade_summary(
    points_awarded: float | None,
    possible_points: float,
    rubric_labels: tuple[str, ...],
) -> str:
    if points_awarded is None and not rubric_labels:
        return "Grade cleared"
    summary = f"{_format_points(points_awarded)}/{_format_points(possible_points)} pts"
    if rubric_labels:
        summary += f" · {', '.join(rubric_labels)}"
    return summary


def _excerpt_comment(analysis: str, *, limit: int = 60) -> str:
    collapsed = " ".join(analysis.split())
    if not collapsed:
        return "Comment cleared"
    if len(collapsed) <= limit:
        return f"“{collapsed}”"
    return f"“{collapsed[: limit - 1].rstrip()}…”"
