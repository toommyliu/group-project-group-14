from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from opengrader.core.courses.contracts.transaction import TransactionFactory
from opengrader.core.courses.domain.entities import Assignment, normalize_assignment_type, normalize_points
from opengrader.core.courses.domain.errors import CourseNotFoundError


class AssignmentService:
    def __init__(self, transaction_factory: TransactionFactory) -> None:
        self._transaction_factory = transaction_factory

    def create_assignment(
        self,
        course_id: int,
        assignment_type: str,
        title: str,
        max_points: Decimal | float | int | str,
        due_at: datetime | None = None,
    ) -> Assignment:
        normalized_type = normalize_assignment_type(assignment_type)
        normalized_points = normalize_points(max_points)

        with self._transaction_factory() as transaction:
            if transaction.courses.get_by_id(course_id) is None:
                raise CourseNotFoundError(f"course {course_id} does not exist")
            assignment = transaction.assignments.create(
                course_id=course_id,
                assignment_type=normalized_type,
                title=title,
                max_points=normalized_points,
                due_at=due_at,
            )
            transaction.commit()
            return assignment

    def list_assignments(self, course_id: int) -> list[Assignment]:
        with self._transaction_factory() as transaction:
            if transaction.courses.get_by_id(course_id) is None:
                raise CourseNotFoundError(f"course {course_id} does not exist")
            return transaction.assignments.list_for_course(course_id)
