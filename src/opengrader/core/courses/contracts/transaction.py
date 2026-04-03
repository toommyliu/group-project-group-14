from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .stores import (
    AssignmentStore,
    AssignmentTemplateStore,
    CourseStore,
    EnrollmentStore,
    GradeStore,
    StudentStore,
)


class CoursesTransaction(Protocol):
    courses: CourseStore
    students: StudentStore
    enrollments: EnrollmentStore
    assignments: AssignmentStore
    assignment_templates: AssignmentTemplateStore
    grades: GradeStore

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def __enter__(self) -> "CoursesTransaction": ...

    def __exit__(self, exc_type, exc, tb) -> None: ...


TransactionFactory = Callable[[], CoursesTransaction]
