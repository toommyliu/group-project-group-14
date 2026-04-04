from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from opengrader.core.courses.contracts.transaction import TransactionFactory
from opengrader.core.courses.domain.entities import (
    CourseGradebook,
    Grade,
    GradeInput,
    StudentGradeSummary,
    normalize_points,
)
from opengrader.core.courses.domain.errors import (
    AssignmentNotFoundError,
    CourseNotFoundError,
    InvalidPointsError,
    StudentNotEnrolledError,
)

_PERCENT_QUANTUM = Decimal("0.01")


class GradingService:
    def __init__(self, transaction_factory: TransactionFactory) -> None:
        self._transaction_factory = transaction_factory

    def assign_grade(
        self,
        assignment_id: int,
        student_id: int,
        points_awarded: Decimal | float | int | str,
        expected_version: int | None = None,
    ) -> Grade:
        normalized_points = normalize_points(points_awarded)

        with self._transaction_factory() as transaction:
            assignment = transaction.assignments.get_by_id(assignment_id)
            if assignment is None:
                raise AssignmentNotFoundError(
                    f"assignment {assignment_id} does not exist"
                )

            if not transaction.enrollments.is_student_enrolled(
                assignment.course_id, student_id
            ):
                raise StudentNotEnrolledError(
                    f"student {student_id} is not enrolled in course {assignment.course_id}"
                )

            if normalized_points > assignment.max_points:
                raise InvalidPointsError(
                    f"points_awarded {normalized_points} exceeds max_points {assignment.max_points}"
                )

            now = datetime.now(timezone.utc)
            grade = transaction.grades.upsert(
                assignment_id=assignment_id,
                student_id=student_id,
                points_awarded=normalized_points,
                updated_at=now,
                expected_version=expected_version,
            )
            transaction.commit()
            return grade

    def bulk_assign_grades(self, grade_inputs: list[GradeInput]) -> list[Grade]:
        results: list[Grade] = []
        with self._transaction_factory() as transaction:
            for grade_input in grade_inputs:
                assignment = transaction.assignments.get_by_id(
                    grade_input.assignment_id
                )
                if assignment is None:
                    raise AssignmentNotFoundError(
                        f"assignment {grade_input.assignment_id} does not exist"
                    )
                if not transaction.enrollments.is_student_enrolled(
                    assignment.course_id, grade_input.student_id
                ):
                    raise StudentNotEnrolledError(
                        f"student {grade_input.student_id} is not enrolled in course {assignment.course_id}"
                    )

                normalized_points = normalize_points(grade_input.points_awarded)
                if normalized_points > assignment.max_points:
                    raise InvalidPointsError(
                        f"points_awarded {normalized_points} exceeds max_points {assignment.max_points}"
                    )

                grade = transaction.grades.upsert(
                    assignment_id=grade_input.assignment_id,
                    student_id=grade_input.student_id,
                    points_awarded=normalized_points,
                    updated_at=datetime.now(timezone.utc),
                    expected_version=grade_input.expected_version,
                )
                results.append(grade)

            transaction.commit()
            return results

    def get_course_gradebook(self, course_id: int) -> CourseGradebook:
        with self._transaction_factory() as transaction:
            course = transaction.courses.get_by_id(course_id)
            if course is None:
                raise CourseNotFoundError(f"course {course_id} does not exist")

            assignments = transaction.assignments.list_for_course(course_id)
            students = transaction.enrollments.list_students_for_course(course_id)
            grades = transaction.grades.list_for_course(course_id)

            grades_by_student: dict[int, dict[int, Grade]] = defaultdict(dict)
            for grade in grades:
                grades_by_student[grade.student_id][grade.assignment_id] = grade

            total_possible = sum(
                (a.max_points for a in assignments), start=Decimal("0")
            )
            rows: list[StudentGradeSummary] = []
            for student in students:
                student_grades = grades_by_student.get(student.id, {})
                total_awarded = sum(
                    (g.points_awarded for g in student_grades.values()),
                    start=Decimal("0"),
                )
                percentage = None
                if total_possible > Decimal("0"):
                    percentage = (
                        (total_awarded / total_possible) * Decimal("100")
                    ).quantize(_PERCENT_QUANTUM, rounding=ROUND_HALF_UP)

                rows.append(
                    StudentGradeSummary(
                        student=student,
                        grades_by_assignment_id=student_grades,
                        total_awarded=total_awarded,
                        total_possible=total_possible,
                        percentage=percentage,
                    )
                )

            return CourseGradebook(course=course, assignments=assignments, rows=rows)
