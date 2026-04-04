from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from opengrader.core import resolve_database_path
from opengrader.core.courses import (
    CourseNotFoundError,
    StudentInput,
    build_sqlite_course_services,
)
from opengrader.tui.ui.widgets.nav_item import NavItem


class CoursesDataService:
    def __init__(
        self,
        # default_image_path: str,
        *,
        db_path: str | Path | None = None,
    ) -> None:
        super().__init__()
        self._services = build_sqlite_course_services(resolve_database_path(db_path))

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
            assignments = self._services.assignments.list_assignments(course_id)
        except CourseNotFoundError:
            return []
        return [
            NavItem(
                item_id=f"a{assignment.id}",
                label=assignment.title,
                meta=str(assignment.type),
            )
            for assignment in assignments
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
        assignment_type: str,
        title: str,
        max_points: str,
    ) -> NavItem:
        assignment = self._services.assignments.create_assignment(
            course_id=course_id,
            assignment_type=assignment_type,
            title=title,
            max_points=max_points,
        )
        return NavItem(
            item_id=f"a{assignment.id}",
            label=assignment.title,
            meta=str(assignment.type),
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
