from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from local_grader.core.courses.contracts.transaction import TransactionFactory
from local_grader.core.courses.domain.entities import (
    Assessment,
    normalize_assessment_type,
    normalize_points,
)
from local_grader.core.courses.domain.errors import CourseNotFoundError


class AssessmentService:
    def __init__(self, transaction_factory: TransactionFactory) -> None:
        self._transaction_factory = transaction_factory

    def create_assessment(
        self,
        course_id: int,
        assessment_type: str,
        title: str,
        max_points: Decimal | float | int | str,
        due_at: datetime | None = None,
    ) -> Assessment:
        normalized_type = normalize_assessment_type(assessment_type)
        normalized_points = normalize_points(max_points)

        with self._transaction_factory() as transaction:
            if transaction.courses.get_by_id(course_id) is None:
                raise CourseNotFoundError(f"course {course_id} does not exist")
            assessment = transaction.assessments.create(
                course_id=course_id,
                assessment_type=normalized_type,
                title=title,
                max_points=normalized_points,
                due_at=due_at,
            )
            transaction.commit()
            return assessment

    def list_assessments(self, course_id: int) -> list[Assessment]:
        with self._transaction_factory() as transaction:
            if transaction.courses.get_by_id(course_id) is None:
                raise CourseNotFoundError(f"course {course_id} does not exist")
            return transaction.assessments.list_for_course(course_id)
