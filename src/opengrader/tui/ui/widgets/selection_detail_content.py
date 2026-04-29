from __future__ import annotations

from textual.widgets import Static

from opengrader.tui.data.models import StudentSubmissionSummary

from .nav_item import NavItem


class CourseDetail(Static):
    def __init__(
        self,
        course: NavItem,
        *,
        assignment_count: int,
        student_count: int,
        id: str | None = None,
    ) -> None:
        super().__init__(
            f"Term/Section: {course.meta or 'N/A'}\n"
            f"Assignments: {assignment_count}\n"
            f"Students: {student_count}\n\n"
            "Pick an assignment to grade, or browse students and submissions.",
            id=id,
        )


class AssignmentDetail(Static):
    def __init__(
        self,
        assignment: NavItem,
        *,
        template_hint: str,
        id: str | None = None,
    ) -> None:
        super().__init__(
            f"Type: {assignment.meta or 'N/A'}\n"
            f"Template: {template_hint}\n\n"
            "Press [g] to start grading the current assignment.",
            id=id,
        )


class StudentDetail(Static):
    def __init__(
        self,
        student: NavItem,
        *,
        submission_count: int,
        id: str | None = None,
    ) -> None:
        super().__init__(
            f"Status: {student.meta or 'N/A'}\n"
            f"Submissions available: {submission_count}\n\n"
            "Select a submission assignment to start grading this student.",
            id=id,
        )


class StudentSubmissionDetail(Static):
    def __init__(
        self,
        student: NavItem,
        assignment: NavItem,
        *,
        submission: StudentSubmissionSummary | None = None,
        id: str | None = None,
    ) -> None:
        lines = [
            f"Student: {student.label}",
            f"Assignment: {assignment.label}",
        ]
        if submission is not None:
            lines.extend(
                [
                    f"Submission name: {submission.name or 'N/A'}",
                    f"Student ID: {submission.student_id or 'N/A'}",
                    f"Course code: {submission.course_code or 'N/A'}",
                    f"Assignment title: {submission.assignment_title or 'N/A'}",
                    f"Assignment version: {submission.assignment_version or 'N/A'}",
                    f"Source PDF: {submission.source_pdf or 'N/A'}",
                    f"Score: {submission.score or 'N/A'}",
                ]
            )
        super().__init__(
            "\n".join(lines) + "\n\nPress [g] to grade this submission.",
            id=id,
        )
