from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from opengrader.core.courses.domain.entities import (
    Assignment,
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
