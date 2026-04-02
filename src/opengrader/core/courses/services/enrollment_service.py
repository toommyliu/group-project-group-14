from __future__ import annotations

from opengrader.core.courses.contracts.transaction import TransactionFactory
from opengrader.core.courses.domain.entities import Student, StudentInput
from opengrader.core.courses.domain.errors import CourseNotFoundError


class EnrollmentService:
    def __init__(self, transaction_factory: TransactionFactory) -> None:
        self._transaction_factory = transaction_factory

    def enroll_students(
        self, course_id: int, students: list[StudentInput]
    ) -> list[Student]:
        with self._transaction_factory() as transaction:
            if transaction.courses.get_by_id(course_id) is None:
                raise CourseNotFoundError(f"course {course_id} does not exist")

            enrolled_students = transaction.students.upsert_students(students)
            transaction.enrollments.enroll_students(
                course_id=course_id,
                student_ids=[student.id for student in enrolled_students],
            )
            transaction.commit()
            return enrolled_students

    def list_roster(self, course_id: int) -> list[Student]:
        with self._transaction_factory() as transaction:
            if transaction.courses.get_by_id(course_id) is None:
                raise CourseNotFoundError(f"course {course_id} does not exist")
            return transaction.enrollments.list_students_for_course(course_id)
