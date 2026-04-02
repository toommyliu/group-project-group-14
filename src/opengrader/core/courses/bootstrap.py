from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from opengrader.core.courses.adapters.sqlite.migrations import (
    initialize_sqlite_database,
)
from opengrader.core.courses.adapters.sqlite.transaction import (
    SqliteCoursesTransaction,
)
from opengrader.core.courses.services.assessment_service import AssessmentService
from opengrader.core.courses.services.course_service import CourseService
from opengrader.core.courses.services.enrollment_service import EnrollmentService
from opengrader.core.courses.services.grading_service import GradingService


@dataclass(frozen=True)
class CourseServices:
    courses: CourseService
    enrollments: EnrollmentService
    assessments: AssessmentService
    grading: GradingService


def build_sqlite_course_services(db_path: str | Path) -> CourseServices:
    db_file = Path(db_path)
    initialize_sqlite_database(db_file)

    def transaction_factory() -> SqliteCoursesTransaction:
        return SqliteCoursesTransaction(db_file)

    return CourseServices(
        courses=CourseService(transaction_factory=transaction_factory),
        enrollments=EnrollmentService(transaction_factory=transaction_factory),
        assessments=AssessmentService(transaction_factory=transaction_factory),
        grading=GradingService(transaction_factory=transaction_factory),
    )
