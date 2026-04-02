from __future__ import annotations

from opengrader.core.courses.domain.entities import Course
from opengrader.core.courses.domain.errors import (
    CourseAlreadyExistsError,
    CourseNotFoundError,
)
from opengrader.core.courses.contracts.transaction import TransactionFactory


class CourseService:
    def __init__(self, transaction_factory: TransactionFactory) -> None:
        self._transaction_factory = transaction_factory

    def create_course(self, code: str, name: str, term: str, section: str) -> Course:
        with self._transaction_factory() as transaction:
            existing = transaction.courses.get_by_key(
                code=code, term=term, section=section
            )
            if existing is not None:
                raise CourseAlreadyExistsError(
                    f"course already exists for ({code}, {term}, {section})"
                )
            created = transaction.courses.create(
                code=code, name=name, term=term, section=section
            )
            transaction.commit()
            return created

    def list_courses(self) -> list[Course]:
        with self._transaction_factory() as transaction:
            return transaction.courses.list_all()

    def get_course(self, course_id: int) -> Course:
        with self._transaction_factory() as transaction:
            course = transaction.courses.get_by_id(course_id)
            if course is None:
                raise CourseNotFoundError(f"course {course_id} does not exist")
            return course
