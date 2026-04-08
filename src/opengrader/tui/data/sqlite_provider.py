from __future__ import annotations

from pathlib import Path

from opengrader.core import resolve_data_dir, resolve_database_path
from opengrader.core.courses import (
    CourseNotFoundError,
    StudentInput,
    build_sqlite_course_services,
)
from opengrader.tui.data.models import (
    AssignmentSummary,
    AssignmentTemplateSummary,
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
        self._data_dir = resolve_data_dir(self._db_path)
        self._courses = None
        self._question_content = StubQuestionContent(default_image_path)

    @property
    def _services(self):
        if self._courses is None:
            self._courses = build_sqlite_course_services(self._db_path)
        return self._courses

    @staticmethod
    def _spec_is_pending(spec_json_relpath: str) -> bool:
        return spec_json_relpath.endswith(".pending.json")

    def list_courses(self) -> list[CourseSummary]:
        courses = self._services.courses.list_courses()
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
            assignments = self._services.assignments.list_assignments(course_id)
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
            roster = self._services.enrollments.list_roster(course_id)
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
        course = self._services.courses.create_course(
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
        assignment = self._services.assignments.create_assignment(
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
        return StudentSummary(id=student.id, full_name=student.full_name)

    def create_assignment_template(
        self,
        *,
        assignment_id: int,
        name: str,
        template_pdf_path: str,
        spec_json_path: str | None = None,
    ) -> AssignmentTemplateSummary:
        template = self._services.assignment_templates.create_template(
            assignment_id=assignment_id,
            name=name,
            source_template_pdf=template_pdf_path,
            source_spec_json=spec_json_path,
        )
        return AssignmentTemplateSummary(
            id=template.id,
            course_id=template.course_id,
            name=template.name,
            template_pdf_relpath=template.template_pdf_relpath,
            spec_json_relpath=template.spec_json_relpath,
            spec_pending=self._spec_is_pending(template.spec_json_relpath),
        )

    def list_assignment_templates(
        self,
        course_id: int | None,
    ) -> list[AssignmentTemplateSummary]:
        if course_id is None:
            return []
        try:
            templates = self._services.assignment_templates.list_templates_for_course(
                course_id
            )
        except CourseNotFoundError:
            return []
        return [
            AssignmentTemplateSummary(
                id=template.id,
                course_id=template.course_id,
                name=template.name,
                template_pdf_relpath=template.template_pdf_relpath,
                spec_json_relpath=template.spec_json_relpath,
                spec_pending=self._spec_is_pending(template.spec_json_relpath),
            )
            for template in templates
        ]

    def set_assignment_template(
        self,
        *,
        assignment_id: int,
        template_id: int,
    ) -> None:
        self._services.assignment_templates.set_assignment_template(
            assignment_id=assignment_id,
            template_id=template_id,
        )

    def replace_assignment_templates(
        self,
        *,
        assignment_id: int,
        template_ids: list[int],
        current_template_id: int,
    ) -> None:
        self._services.assignment_templates.replace_assignment_templates(
            assignment_id=assignment_id,
            template_ids=template_ids,
            current_template_id=current_template_id,
        )

    def get_assignment_template(
        self,
        assignment_id: int | None,
    ) -> AssignmentTemplateSummary | None:
        if assignment_id is None:
            return None
        template = self._services.assignment_templates.get_assignment_template(
            assignment_id
        )
        if template is None:
            return None
        return AssignmentTemplateSummary(
            id=template.id,
            course_id=template.course_id,
            name=template.name,
            template_pdf_relpath=template.template_pdf_relpath,
            spec_json_relpath=template.spec_json_relpath,
            spec_pending=self._spec_is_pending(template.spec_json_relpath),
        )

    def list_assignment_templates_for_assignment(
        self,
        assignment_id: int | None,
    ) -> list[AssignmentTemplateSummary]:
        if assignment_id is None:
            return []
        templates = self._services.assignment_templates.list_assignment_templates(
            assignment_id
        )
        return [
            AssignmentTemplateSummary(
                id=template.id,
                course_id=template.course_id,
                name=template.name,
                template_pdf_relpath=template.template_pdf_relpath,
                spec_json_relpath=template.spec_json_relpath,
                spec_pending=self._spec_is_pending(template.spec_json_relpath),
            )
            for template in templates
        ]
