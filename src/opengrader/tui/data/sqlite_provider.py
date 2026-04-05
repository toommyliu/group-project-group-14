from __future__ import annotations

from pathlib import Path

from opengrader.core import resolve_database_path
from opengrader.core.courses import (
    CourseNotFoundError,
    StudentInput,
    build_sqlite_course_services,
)
from opengrader.tui.data.models import (
    AssignmentSummary,
    CourseSummary,
    QuestionSeed,
    StudentSummary,
)
from opengrader.tui.data.stub_question_content import StubQuestionContent


class SqliteProvider:
    def __init__(
        self,
        default_image_path: str,
        *,
        db_path: str | Path | None = None,
    ) -> None:
        self._db_path = resolve_database_path(db_path)
        self._courses = build_sqlite_course_services(self._db_path)
        self._question_content = StubQuestionContent(default_image_path)

    def list_courses(self) -> list[CourseSummary]:
        courses = self._courses.courses.list_courses()
        return [
            CourseSummary(
                id=course.id,
                code=course.code,
                name=course.name,
                term=course.term,
                section=course.section,
            )
            for course in courses
        ]

    def list_assignments(self, course_id: int | None) -> list[AssignmentSummary]:
        if course_id is None:
            return []
        try:
            assignments = self._courses.assignments.list_assignments(course_id)
        except CourseNotFoundError:
            return []
        return [
            AssignmentSummary(
                id=assignment.id,
                title=assignment.title,
                assignment_type=str(assignment.type),
            )
            for assignment in assignments
        ]

    def list_students(self, course_id: int | None) -> list[StudentSummary]:
        if course_id is None:
            return []
        try:
            roster = self._courses.enrollments.list_roster(course_id)
        except CourseNotFoundError:
            return []
        return [
            StudentSummary(
                id=student.id,
                full_name=student.full_name,
            )
            for student in roster
        ]

    def build_question_seeds(self) -> list[QuestionSeed]:
        return self._question_content.build_question_seeds()

    def create_course(
        self,
        *,
        code: str,
        name: str,
        term: str,
        section: str,
    ) -> CourseSummary:
        course = self._courses.courses.create_course(
            code=code,
            name=name,
            term=term,
            section=section,
        )
        return CourseSummary(
            id=course.id,
            code=course.code,
            name=course.name,
            term=course.term,
            section=course.section,
        )

    def create_assignment(
        self,
        *,
        course_id: int,
        assignment_type: str,
        title: str,
        max_points: str,
    ) -> AssignmentSummary:
        assignment = self._courses.assignments.create_assignment(
            course_id=course_id,
            assignment_type=assignment_type,
            title=title,
            max_points=max_points,
        )
        return AssignmentSummary(
            id=assignment.id,
            title=assignment.title,
            assignment_type=str(assignment.type),
        )

    def create_student(
        self,
        *,
        course_id: int,
        sid: str,
        first_name: str,
        last_name: str,
        middle_name: str | None,
        email: str | None,
    ) -> StudentSummary:
        student = self._courses.enrollments.enroll_students(
            course_id,
            [
                StudentInput(
                    sid=sid,
                    first_name=first_name,
                    last_name=last_name,
                    middle_name=middle_name,
                    email=email,
                )
            ],
        )[0]
        return StudentSummary(id=student.id, full_name=student.full_name)
