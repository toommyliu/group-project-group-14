from datetime import datetime, timezone
from decimal import Decimal

import pytest

from local_grader.core.courses import (
    AssessmentNotFoundError,
    CourseAlreadyExistsError,
    GradeConflictError,
    InvalidPointsError,
    StudentInput,
    StudentNotEnrolledError,
    build_sqlite_course_services,
)


def test_create_course_and_prevent_duplicates(tmp_path) -> None:
    services = build_sqlite_course_services(tmp_path / "courses.db")

    created = services.courses.create_course(
        code="CMPE-195",
        name="Senior Design",
        term="Spring 2026",
        section="01",
    )

    courses = services.courses.list_courses()
    assert len(courses) == 1
    assert courses[0].id == created.id

    with pytest.raises(CourseAlreadyExistsError):
        services.courses.create_course(
            code="CMPE-195",
            name="Senior Design",
            term="Spring 2026",
            section="01",
        )


def test_enroll_students_is_idempotent_by_sid(tmp_path) -> None:
    services = build_sqlite_course_services(tmp_path / "courses.db")
    course = services.courses.create_course(
        code="MATH-101",
        name="Calculus",
        term="Fall 2026",
        section="A",
    )

    services.enrollments.enroll_students(
        course.id,
        [
            StudentInput(sid="100000001", name="Ada Lovelace", email="ada@example.com"),
        ],
    )
    services.enrollments.enroll_students(
        course.id,
        [
            StudentInput(
                sid="100000001",
                name="Ada L.",
                email="ada@example.com",
            ),
        ],
    )

    roster = services.enrollments.list_roster(course.id)
    assert len(roster) == 1
    assert roster[0].sid == "100000001"
    assert roster[0].name == "Ada L."


def test_assessment_sorting_uses_due_date_with_nulls_last(tmp_path) -> None:
    services = build_sqlite_course_services(tmp_path / "courses.db")
    course = services.courses.create_course(
        code="ENGR-10",
        name="Engineering Intro",
        term="Spring 2026",
        section="1",
    )

    no_due = services.assessments.create_assessment(
        course_id=course.id,
        assessment_type="assignment",
        title="Homework",
        max_points=10,
        due_at=None,
    )
    early_due = services.assessments.create_assessment(
        course_id=course.id,
        assessment_type="quiz",
        title="Quiz 1",
        max_points=15,
        due_at=datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc),
    )
    late_due = services.assessments.create_assessment(
        course_id=course.id,
        assessment_type="exam",
        title="Midterm",
        max_points=100,
        due_at=datetime(2026, 3, 21, 9, 0, tzinfo=timezone.utc),
    )

    listed = services.assessments.list_assessments(course.id)
    assert [a.id for a in listed] == [early_due.id, late_due.id, no_due.id]


def test_assign_grade_requires_enrollment_and_honors_max_points(tmp_path) -> None:
    services = build_sqlite_course_services(tmp_path / "courses.db")
    course = services.courses.create_course(
        code="BIO-20",
        name="Biology",
        term="Fall 2026",
        section="2",
    )
    assessment = services.assessments.create_assessment(
        course_id=course.id,
        assessment_type="quiz",
        title="Quiz A",
        max_points=10,
    )

    with pytest.raises(StudentNotEnrolledError):
        services.grading.assign_grade(
            assessment_id=assessment.id,
            student_id=999,
            points_awarded=5,
        )

    student = services.enrollments.enroll_students(
        course.id,
        [StudentInput(sid="100000002", name="Grace Hopper", email=None)],
    )[0]

    with pytest.raises(InvalidPointsError):
        services.grading.assign_grade(
            assessment_id=assessment.id,
            student_id=student.id,
            points_awarded=10.5,
        )


def test_assign_grade_uses_optimistic_locking_version(tmp_path) -> None:
    services = build_sqlite_course_services(tmp_path / "courses.db")
    course = services.courses.create_course(
        code="CS-101",
        name="Intro to CS",
        term="Spring 2026",
        section="01",
    )
    student = services.enrollments.enroll_students(
        course.id,
        [StudentInput(sid="100000003", name="Alan Turing", email=None)],
    )[0]
    assessment = services.assessments.create_assessment(
        course_id=course.id,
        assessment_type="assignment",
        title="Homework 1",
        max_points=20,
    )

    first = services.grading.assign_grade(
        assessment_id=assessment.id,
        student_id=student.id,
        points_awarded=18,
    )
    assert first.version == 1

    second = services.grading.assign_grade(
        assessment_id=assessment.id,
        student_id=student.id,
        points_awarded=19,
        expected_version=1,
    )
    assert second.version == 2

    with pytest.raises(GradeConflictError):
        services.grading.assign_grade(
            assessment_id=assessment.id,
            student_id=student.id,
            points_awarded=20,
            expected_version=1,
        )


def test_get_course_gradebook_returns_totals_and_percentage(tmp_path) -> None:
    services = build_sqlite_course_services(tmp_path / "courses.db")
    course = services.courses.create_course(
        code="CHEM-1",
        name="Chemistry",
        term="Spring 2026",
        section="03",
    )
    students = services.enrollments.enroll_students(
        course.id,
        [
            StudentInput(sid="100000004", name="Student A", email=None),
            StudentInput(sid="100000005", name="Student B", email=None),
        ],
    )
    assessments = [
        services.assessments.create_assessment(
            course_id=course.id,
            assessment_type="quiz",
            title="Quiz",
            max_points=10,
        ),
        services.assessments.create_assessment(
            course_id=course.id,
            assessment_type="exam",
            title="Final",
            max_points=20,
        ),
    ]

    services.grading.assign_grade(assessments[0].id, students[0].id, 8)
    services.grading.assign_grade(assessments[1].id, students[0].id, 10)

    gradebook = services.grading.get_course_gradebook(course.id)
    rows_by_sid = {row.student.sid: row for row in gradebook.rows}

    first = rows_by_sid["100000004"]
    assert first.total_awarded == Decimal("18.00")
    assert first.total_possible == Decimal("30.00")
    assert first.percentage == Decimal("60.00")

    second = rows_by_sid["100000005"]
    assert second.total_awarded == Decimal("0")
    assert second.total_possible == Decimal("30.00")
    assert second.percentage == Decimal("0.00")


def test_assign_grade_rejects_unknown_assessment(tmp_path) -> None:
    services = build_sqlite_course_services(tmp_path / "courses.db")

    with pytest.raises(AssessmentNotFoundError):
        services.grading.assign_grade(
            assessment_id=999,
            student_id=1,
            points_awarded=1,
        )
