from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from local_grader.core.courses.domain.entities import (
    Assessment,
    AssessmentType,
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


class AssessmentStore(Protocol):
    def create(
        self,
        course_id: int,
        assessment_type: AssessmentType,
        title: str,
        max_points: Decimal,
        due_at: datetime | None,
    ) -> Assessment: ...

    def get_by_id(self, assessment_id: int) -> Assessment | None: ...

    def list_for_course(self, course_id: int) -> list[Assessment]: ...


class GradeStore(Protocol):
    def get(self, assessment_id: int, student_id: int) -> Grade | None: ...

    def upsert(
        self,
        assessment_id: int,
        student_id: int,
        points_awarded: Decimal,
        updated_at: datetime,
        expected_version: int | None,
    ) -> Grade: ...

    def list_for_course(self, course_id: int) -> list[Grade]: ...
