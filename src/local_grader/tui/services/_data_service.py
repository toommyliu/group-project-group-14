from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from local_grader.tui.state import QuestionGrade
from local_grader.tui.widgets.nav_item import NavItem


class TuiDataService(Protocol):
    def load_courses(self) -> list[NavItem]: ...

    def load_assignments(self, course_id: int | None) -> list[NavItem]: ...

    def load_students(
        self,
        course_id: int | None,
        status_lookup: Callable[[str], str],
    ) -> list[NavItem]: ...

    def build_question_grades(self) -> list[QuestionGrade]: ...

    def create_course(
        self,
        *,
        code: str,
        name: str,
        term: str,
        section: str,
    ) -> NavItem: ...

    def create_assignment(
        self,
        *,
        course_id: int,
        assessment_type: str,
        title: str,
        max_points: str,
    ) -> NavItem: ...

    def create_student(
        self,
        *,
        course_id: int,
        sid: str,
        first_name: str,
        last_name: str,
        middle_name: str | None,
        email: str | None,
    ) -> NavItem: ...
