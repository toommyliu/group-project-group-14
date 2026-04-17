from datetime import datetime, timezone
from decimal import Decimal

import pytest

from opengrader.core.courses import (
    AssignmentNotFoundError,
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
            StudentInput(
                sid="100000001",
                first_name="Ada",
                last_name="Lovelace",
                email="ada@example.com",
            ),
        ],
    )
    services.enrollments.enroll_students(
        course.id,
        [
            StudentInput(
                sid="100000001",
                first_name="Ada",
                last_name="L.",
                email="ada@example.com",
            ),
        ],
    )

    roster = services.enrollments.list_roster(course.id)
    assert len(roster) == 1
    assert roster[0].sid == "100000001"
    assert roster[0].full_name == "Ada L."


def test_assignment_sorting_uses_due_date_with_nulls_last(tmp_path) -> None:
    services = build_sqlite_course_services(tmp_path / "courses.db")
    course = services.courses.create_course(
        code="ENGR-10",
        name="Engineering Intro",
        term="Spring 2026",
        section="1",
    )

    no_due = services.assignments.create_assignment(
        course_id=course.id,
        assignment_type="assignment",
        title="Homework",
        max_points=10,
        due_at=None,
    )
    early_due = services.assignments.create_assignment(
        course_id=course.id,
        assignment_type="quiz",
        title="Quiz 1",
        max_points=15,
        due_at=datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc),
    )
    late_due = services.assignments.create_assignment(
        course_id=course.id,
        assignment_type="exam",
        title="Midterm",
        max_points=100,
        due_at=datetime(2026, 3, 21, 9, 0, tzinfo=timezone.utc),
    )

    listed = services.assignments.list_assignments(course.id)
    assert [a.id for a in listed] == [early_due.id, late_due.id, no_due.id]


def test_assign_grade_requires_enrollment_and_honors_max_points(tmp_path) -> None:
    services = build_sqlite_course_services(tmp_path / "courses.db")
    course = services.courses.create_course(
        code="BIO-20",
        name="Biology",
        term="Fall 2026",
        section="2",
    )
    assignment = services.assignments.create_assignment(
        course_id=course.id,
        assignment_type="quiz",
        title="Quiz A",
        max_points=10,
    )

    with pytest.raises(StudentNotEnrolledError):
        services.grading.assign_grade(
            assignment_id=assignment.id,
            student_id=999,
            points_awarded=5,
        )

    student = services.enrollments.enroll_students(
        course.id,
        [
            StudentInput(
                sid="100000002",
                first_name="Grace",
                last_name="Hopper",
                email=None,
            )
        ],
    )[0]

    with pytest.raises(InvalidPointsError):
        services.grading.assign_grade(
            assignment_id=assignment.id,
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
        [
            StudentInput(
                sid="100000003",
                first_name="Alan",
                last_name="Turing",
                email=None,
            )
        ],
    )[0]
    assignment = services.assignments.create_assignment(
        course_id=course.id,
        assignment_type="assignment",
        title="Homework 1",
        max_points=20,
    )

    first = services.grading.assign_grade(
        assignment_id=assignment.id,
        student_id=student.id,
        points_awarded=18,
    )
    assert first.version == 1

    second = services.grading.assign_grade(
        assignment_id=assignment.id,
        student_id=student.id,
        points_awarded=19,
        expected_version=1,
    )
    assert second.version == 2

    with pytest.raises(GradeConflictError):
        services.grading.assign_grade(
            assignment_id=assignment.id,
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
            StudentInput(
                sid="100000004",
                first_name="Student",
                last_name="A",
                email=None,
            ),
            StudentInput(
                sid="100000005",
                first_name="Student",
                last_name="B",
                email=None,
            ),
        ],
    )
    assignments = [
        services.assignments.create_assignment(
            course_id=course.id,
            assignment_type="quiz",
            title="Quiz",
            max_points=10,
        ),
        services.assignments.create_assignment(
            course_id=course.id,
            assignment_type="exam",
            title="Final",
            max_points=20,
        ),
    ]

    services.grading.assign_grade(assignments[0].id, students[0].id, 8)
    services.grading.assign_grade(assignments[1].id, students[0].id, 10)

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


def test_assign_grade_rejects_unknown_assignment(tmp_path) -> None:
    services = build_sqlite_course_services(tmp_path / "courses.db")

    with pytest.raises(AssignmentNotFoundError):
        services.grading.assign_grade(
            assignment_id=999,
            student_id=1,
            points_awarded=1,
        )


def test_assignment_templates_can_be_created_linked_and_loaded(tmp_path) -> None:
    services = build_sqlite_course_services(tmp_path / "courses.db")
    course = services.courses.create_course(
        code="CMPE-195",
        name="Senior Design",
        term="Spring 2026",
        section="01",
    )
    assignment = services.assignments.create_assignment(
        course_id=course.id,
        assignment_type="assignment",
        title="HW 1",
        max_points=100,
    )

    source_pdf = tmp_path / "sample-template.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n% template")
    created = services.assignment_templates.create_template(
        assignment_id=assignment.id,
        name="Template A",
        source_template_pdf=source_pdf,
    )

    listed = services.assignment_templates.list_templates_for_course(course.id)
    assert [t.id for t in listed] == [created.id]
    assert listed[0].name == "Template A"

    services.assignment_templates.set_assignment_template(
        assignment_id=assignment.id,
        template_id=created.id,
    )
    current = services.assignment_templates.get_assignment_template(assignment.id)
    assert current is not None
    assert current.id == created.id
    assert current.template_pdf_relpath == (
        "courses/cmpe-195-senior-design-spring-2026-01/"
        f"assignments/{assignment.id}-hw-1/templates/template-a-t{created.id}.pdf"
    )
