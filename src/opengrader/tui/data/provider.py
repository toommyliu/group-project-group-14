from __future__ import annotations

from typing import Protocol

from opengrader.tui.data.models import (
    AssignmentSummary,
    CourseSummary,
    QuestionSeed,
    StudentSummary,
)


class Provider(Protocol):
    def list_courses(self) -> list[CourseSummary]: ...

    def list_assignments(self, course_id: int | None) -> list[AssignmentSummary]: ...

    def list_students(self, course_id: int | None) -> list[StudentSummary]: ...

    def build_question_seeds(self) -> list[QuestionSeed]: ...

    def create_course(
        self,
        *,
        code: str,
        name: str,
        term: str,
        section: str,
    ) -> CourseSummary: ...

    def create_assignment(
        self,
        *,
        course_id: int,
        assignment_type: str,
        title: str,
        max_points: str,
    ) -> AssignmentSummary: ...

    def create_student(
        self,
        *,
        course_id: int,
        sid: str,
        first_name: str,
        last_name: str,
        middle_name: str | None,
        email: str | None,
    ) -> StudentSummary: ...
