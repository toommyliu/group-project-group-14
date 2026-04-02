from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from local_grader.core.courses.contracts.transaction import TransactionFactory
from local_grader.core.courses.domain.entities import (
    CourseGradebook,
    Grade,
    GradeInput,
    StudentGradeSummary,
    normalize_points,
)
from local_grader.core.courses.domain.errors import (
    AssessmentNotFoundError,
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
        assessment_id: int,
        student_id: int,
        points_awarded: Decimal | float | int | str,
        expected_version: int | None = None,
    ) -> Grade:
        normalized_points = normalize_points(points_awarded)

        with self._transaction_factory() as transaction:
            assessment = transaction.assessments.get_by_id(assessment_id)
            if assessment is None:
                raise AssessmentNotFoundError(
                    f"assessment {assessment_id} does not exist"
                )

            if not transaction.enrollments.is_student_enrolled(
                assessment.course_id, student_id
            ):
                raise StudentNotEnrolledError(
                    f"student {student_id} is not enrolled in course {assessment.course_id}"
                )

            if normalized_points > assessment.max_points:
                raise InvalidPointsError(
                    f"points_awarded {normalized_points} exceeds max_points {assessment.max_points}"
                )

            now = datetime.now(timezone.utc)
            grade = transaction.grades.upsert(
                assessment_id=assessment_id,
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
                assessment = transaction.assessments.get_by_id(
                    grade_input.assessment_id
                )
                if assessment is None:
                    raise AssessmentNotFoundError(
                        f"assessment {grade_input.assessment_id} does not exist"
                    )
                if not transaction.enrollments.is_student_enrolled(
                    assessment.course_id, grade_input.student_id
                ):
                    raise StudentNotEnrolledError(
                        f"student {grade_input.student_id} is not enrolled in course {assessment.course_id}"
                    )

                normalized_points = normalize_points(grade_input.points_awarded)
                if normalized_points > assessment.max_points:
                    raise InvalidPointsError(
                        f"points_awarded {normalized_points} exceeds max_points {assessment.max_points}"
                    )

                grade = transaction.grades.upsert(
                    assessment_id=grade_input.assessment_id,
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

            assessments = transaction.assessments.list_for_course(course_id)
            students = transaction.enrollments.list_students_for_course(course_id)
            grades = transaction.grades.list_for_course(course_id)

            grades_by_student: dict[int, dict[int, Grade]] = defaultdict(dict)
            for grade in grades:
                grades_by_student[grade.student_id][grade.assessment_id] = grade

            total_possible = sum(
                (a.max_points for a in assessments), start=Decimal("0")
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
                        grades_by_assessment_id=student_grades,
                        total_awarded=total_awarded,
                        total_possible=total_possible,
                        percentage=percentage,
                    )
                )

            return CourseGradebook(course=course, assessments=assessments, rows=rows)
