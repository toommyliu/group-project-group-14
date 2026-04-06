from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from opengrader.core.courses.adapters.sqlite.migrations import initialize_sqlite_database
from opengrader.core.courses.adapters.sqlite.transaction import SqliteCoursesTransaction
from opengrader.core.courses.services.assignment_service import AssignmentService
from opengrader.core.courses.services.assignment_template_service import (
    AssignmentTemplateService,
)
from opengrader.core.courses.services.course_service import CourseService
from opengrader.core.courses.services.enrollment_service import EnrollmentService
from opengrader.core.courses.services.grading_service import GradingService


@dataclass(frozen=True)
class CourseServices:
    courses: CourseService
    enrollments: EnrollmentService
    assignments: AssignmentService
    assignment_templates: AssignmentTemplateService
    grading: GradingService


def build_sqlite_course_services(db_path: str | Path) -> CourseServices:
    db_file = Path(db_path)
    initialize_sqlite_database(db_file)

    def transaction_factory() -> SqliteCoursesTransaction:
        return SqliteCoursesTransaction(db_file)

    return CourseServices(
        courses=CourseService(transaction_factory=transaction_factory),
        enrollments=EnrollmentService(transaction_factory=transaction_factory),
        assignments=AssignmentService(transaction_factory=transaction_factory),
        assignment_templates=AssignmentTemplateService(
            transaction_factory=transaction_factory
        ),
        grading=GradingService(transaction_factory=transaction_factory),
    )
