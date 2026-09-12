from __future__ import annotations

from pathlib import Path

from opengrader.core.curve import Curve, apply_curve
from opengrader.core.pdf.annotations import (
    FeedbackQuestionSummary,
    FeedbackSummary,
    sync_feedback_summary_page,
    sync_question_comment_annotation,
    sync_total_score_annotation,
)
from opengrader.core.question_types import humanize_question_label
from opengrader.core.submissions.student_file import StudentSubmissionDocument


def student_label(document: StudentSubmissionDocument, *, anonymous: bool = False) -> str:
    name = document.student_name.strip()
    student_id = document.student_id.strip()
    if anonymous or not name:
        return f"Student ID: {student_id}" if student_id else "Unidentified student"
    if student_id:
        return f"{name} · ID: {student_id}"
    return name


def assignment_title(document: StudentSubmissionDocument) -> str:
    title = document.assignment_title or "Assignment"
    if document.assignment_version:
        return f"{title} ({document.assignment_version})"
    return title


def build_feedback_summary(
    document: StudentSubmissionDocument,
    *,
    curve: Curve = Curve(),
    anonymous: bool = False,
) -> FeedbackSummary:
    questions = tuple(
        FeedbackQuestionSummary(
            label=humanize_question_label(question.question_id),
            possible_points=question.possible_points,
            points_awarded=question.points_awarded,
            rubric_labels=tuple(question.rubric_labels),
            comment=question.analysis or "",
        )
        for question in document.questions
    )
    total_awarded = sum(
        q.points_awarded for q in document.questions if q.points_awarded is not None
    )
    total_possible = sum(
        q.possible_points for q in document.questions if q.possible_points is not None
    )
    curved_total = (
        apply_curve(total_awarded, total_possible, curve) if curve.is_active else None
    )
    return FeedbackSummary(
        title=assignment_title(document),
        student_label=student_label(document, anonymous=anonymous),
        total_awarded=total_awarded,
        total_possible=total_possible,
        questions=questions,
        curved_total=curved_total,
    )


def build_annotated_pdf(
    *,
    document: StudentSubmissionDocument,
    source_pdf_path: Path,
    annotated_pdf_path: Path,
    curve: Curve = Curve(),
    anonymous: bool = False,
) -> Path:
    if not source_pdf_path.exists():
        raise FileNotFoundError(f"source PDF not found: {source_pdf_path}")

    if annotated_pdf_path.exists():
        annotated_pdf_path.unlink()

    total_awarded = sum(
        q.points_awarded for q in document.questions if q.points_awarded is not None
    )
    total_possible = sum(
        q.possible_points for q in document.questions if q.possible_points is not None
    )
    wrote = sync_total_score_annotation(
        source_pdf_path=source_pdf_path,
        annotated_pdf_path=annotated_pdf_path,
        total_points=total_awarded,
        possible_points=total_possible,
    )

    for question in document.questions:
        if not question.source_segments:
            continue
        wrote |= sync_question_comment_annotation(
            source_pdf_path=source_pdf_path,
            annotated_pdf_path=annotated_pdf_path,
            question_id=question.question_id,
            comment=question.analysis or "",
            points_awarded=question.points_awarded,
            possible_points=question.possible_points,
            source_segments=question.source_segments,
        )

    sync_feedback_summary_page(
        source_pdf_path=source_pdf_path,
        annotated_pdf_path=annotated_pdf_path,
        summary=build_feedback_summary(
            document, curve=curve, anonymous=anonymous
        ),
        force=wrote,
    )
    return annotated_pdf_path
