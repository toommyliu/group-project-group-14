from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from opengrader.core.courses import (
    CourseNotFoundError,
    StudentInput,
    build_sqlite_course_services,
)
from opengrader.tui.widgets.nav_item import NavItem


class CoursesDataService:
    def __init__(
        self,
        # default_image_path: str,
        *,
        db_path: str | Path = "courses.db",
    ) -> None:
        super().__init__()
        self._services = build_sqlite_course_services(db_path)

    def load_courses(self) -> list[NavItem]:
        courses = self._services.courses.list_courses()
        return [
            NavItem(
                item_id=f"c{course.id}",
                label=f"{course.code} {course.name}",
                meta=f"{course.term} {course.section}",
            )
            for course in courses
        ]

    def load_assignments(self, course_id: int | None) -> list[NavItem]:
        if course_id is None:
            return []
        try:
            assessments = self._services.assessments.list_assessments(course_id)
        except CourseNotFoundError:
            return []
        return [
            NavItem(
                item_id=f"a{assessment.id}",
                label=assessment.title,
                meta=str(assessment.type),
            )
            for assessment in assessments
        ]

    def load_students(
        self,
        course_id: int | None,
        status_lookup: Callable[[str], str],
    ) -> list[NavItem]:
        if course_id is None:
            return []
        try:
            roster = self._services.enrollments.list_roster(course_id)
        except CourseNotFoundError:
            return []
        return [
            NavItem(
                item_id=f"s{student.id}",
                label=student.full_name,
                meta=status_lookup(f"s{student.id}"),
            )
            for student in roster
        ]

    def create_course(
        self,
        *,
        code: str,
        name: str,
        term: str,
        section: str,
    ) -> NavItem:
        course = self._services.courses.create_course(
            code=code,
            name=name,
            term=term,
            section=section,
        )
        return NavItem(
            item_id=f"c{course.id}",
            label=f"{course.code} {course.name}",
            meta=f"{course.term} {course.section}",
        )

    def create_assignment(
        self,
        *,
        course_id: int,
        assessment_type: str,
        title: str,
        max_points: str,
    ) -> NavItem:
        assessment = self._services.assessments.create_assessment(
            course_id=course_id,
            assessment_type=assessment_type,
            title=title,
            max_points=max_points,
        )
        return NavItem(
            item_id=f"a{assessment.id}",
            label=assessment.title,
            meta=str(assessment.type),
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
    ) -> NavItem:
        student = self._services.enrollments.enroll_students(
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
        return NavItem(item_id=f"s{student.id}", label=student.full_name)
