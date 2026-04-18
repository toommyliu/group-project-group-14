from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from opengrader.core.courses.domain.entities import (
    Assignment,
    AssignmentTemplate,
    AssignmentType,
    Course,
    Enrollment,
    Grade,
    Student,
    StudentInput,
)


class CourseStore(Protocol):
    def create(self, code: str, name: str, term: str, section: str) -> Course: ...

    def get_by_id(self, course_id: int) -> Course | None: ...

    def get_by_key(self, code: str, term: str, section: str) -> Course | None: ...

    def list_all(self) -> list[Course]: ...


class StudentStore(Protocol):
    def upsert_students(self, students: list[StudentInput]) -> list[Student]: ...


class EnrollmentStore(Protocol):
    def enroll_students(
        self, course_id: int, student_ids: list[int]
    ) -> list[Enrollment]: ...

    def is_student_enrolled(self, course_id: int, student_id: int) -> bool: ...

    def list_students_for_course(self, course_id: int) -> list[Student]: ...


class AssignmentStore(Protocol):
    def create(
        self,
        course_id: int,
        assignment_type: AssignmentType,
        title: str,
        max_points: Decimal,
        due_at: datetime | None,
    ) -> Assignment: ...

    def get_by_id(self, assignment_id: int) -> Assignment | None: ...

    def list_for_course(self, course_id: int) -> list[Assignment]: ...


class AssignmentTemplateStore(Protocol):
    def create(
        self,
        course_id: int,
        name: str,
        template_pdf_relpath: str,
        template_sha256: str,
    ) -> AssignmentTemplate: ...

    def list_for_course(self, course_id: int) -> list[AssignmentTemplate]: ...

    def get_by_id(self, template_id: int) -> AssignmentTemplate | None: ...

    def update_paths(
        self,
        *,
        template_id: int,
        template_pdf_relpath: str,
    ) -> None: ...

    def set_assignment_template(
        self,
        assignment_id: int,
        template_id: int,
    ) -> None: ...

    def replace_assignment_templates(
        self,
        *,
        assignment_id: int,
        template_ids: list[int],
        current_template_id: int,
    ) -> None: ...

    def list_for_assignment(self, assignment_id: int) -> list[AssignmentTemplate]: ...

    def get_current_for_assignment(
        self,
        assignment_id: int,
    ) -> AssignmentTemplate | None: ...


class GradeStore(Protocol):
    def get(self, assignment_id: int, student_id: int) -> Grade | None: ...

    def upsert(
        self,
        assignment_id: int,
        student_id: int,
        points_awarded: Decimal,
        updated_at: datetime,
        expected_version: int | None,
    ) -> Grade: ...

    def list_for_course(self, course_id: int) -> list[Grade]: ...
