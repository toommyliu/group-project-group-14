from __future__ import annotations

from opengrader.tui.data.models import (
    AssignmentSummary,
    AssignmentTemplateSummary,
    CourseSummary,
    QuestionSeed,
    StudentSummary,
)
from opengrader.tui.data.stub_question_content import StubQuestionContent


class StubDataProvider:
    def __init__(self, default_image_path: str) -> None:
        self._question_content = StubQuestionContent(default_image_path)

    def list_courses(self) -> list[CourseSummary]:
        return [
            CourseSummary(
                id=1,
                code="CMPE",
                name="195",
                term="Spring 2026",
                section="",
            ),
            CourseSummary(
                id=2,
                code="CMPE",
                name="102",
                term="Spring 2026",
                section="",
            ),
        ]

    def list_assignments(self, course_id: int | None) -> list[AssignmentSummary]:
        if course_id is None:
            return []
        return [
            AssignmentSummary(id=1, title="HW 1", assignment_type="assignment"),
            AssignmentSummary(id=2, title="HW 2", assignment_type="assignment"),
            AssignmentSummary(id=3, title="Midterm", assignment_type="exam"),
        ]

    def list_students(self, course_id: int | None) -> list[StudentSummary]:
        if course_id is None:
            return []
        return [
            StudentSummary(id=1, full_name="Tommy Liu"),
            StudentSummary(id=2, full_name="Andy Cortes"),
            StudentSummary(id=3, full_name="Arjun Gill"),
            StudentSummary(id=4, full_name="Marco Lopez"),
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
        return CourseSummary(
            id=1,
            code=code,
            name=name,
            term=term,
            section=section,
        )

    def create_assignment(
        self,
        *,
        course_id: int,
        assignment_type: str,
        title: str,
        max_points: str,
    ) -> AssignmentSummary:
        _ = (course_id, max_points)
        return AssignmentSummary(id=1, title=title, assignment_type=assignment_type)

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
        _ = (course_id, sid, middle_name, email)
        return StudentSummary(id=1, full_name=f"{first_name} {last_name}")

    def create_assignment_template(
        self,
        *,
        assignment_id: int,
        name: str,
        template_pdf_path: str,
        spec_json_path: str | None = None,
    ) -> AssignmentTemplateSummary:
        _ = (template_pdf_path, spec_json_path)
        return AssignmentTemplateSummary(
            id=1,
            course_id=1,
            name=name,
            template_pdf_relpath=f"courses/cmpe-195/assignments/{assignment_id}-hw-1/templates/{name.lower().replace(' ', '-')}-t1/template.pdf",
            spec_json_relpath=(
                f"courses/cmpe-195/assignments/{assignment_id}-hw-1/templates/{name.lower().replace(' ', '-')}-t1/spec.json"
                if spec_json_path is not None
                else f"courses/cmpe-195/assignments/{assignment_id}-hw-1/templates/{name.lower().replace(' ', '-')}-t1/spec.pending.json"
            ),
            spec_pending=spec_json_path is None,
        )

    def list_assignment_templates(
        self,
        course_id: int | None,
    ) -> list[AssignmentTemplateSummary]:
        if course_id is None:
            return []
        return [
            AssignmentTemplateSummary(
                id=1,
                course_id=course_id,
                name="Default Template",
                template_pdf_relpath=(
                    "courses/cmpe-195-spring-2026/"
                    "assignments/1-hw-1/templates/default-template-t1/template.pdf"
                ),
                spec_json_relpath=(
                    "courses/cmpe-195-spring-2026/"
                    "assignments/1-hw-1/templates/default-template-t1/spec.json"
                ),
                spec_pending=False,
            )
        ]

    def set_assignment_template(
        self,
        *,
        assignment_id: int,
        template_id: int,
    ) -> None:
        _ = (assignment_id, template_id)

    def replace_assignment_templates(
        self,
        *,
        assignment_id: int,
        template_ids: list[int],
        current_template_id: int,
    ) -> None:
        _ = (assignment_id, template_ids, current_template_id)

    def get_assignment_template(
        self,
        assignment_id: int | None,
    ) -> AssignmentTemplateSummary | None:
        if assignment_id is None:
            return None
        return AssignmentTemplateSummary(
            id=1,
            course_id=1,
            name="Default Template",
            template_pdf_relpath=(
                "courses/cmpe-195-spring-2026/"
                "assignments/1-hw-1/templates/default-template-t1/template.pdf"
            ),
            spec_json_relpath=(
                "courses/cmpe-195-spring-2026/"
                "assignments/1-hw-1/templates/default-template-t1/spec.json"
            ),
            spec_pending=False,
        )

    def list_assignment_templates_for_assignment(
        self,
        assignment_id: int | None,
    ) -> list[AssignmentTemplateSummary]:
        if assignment_id is None:
            return []
        return [
            AssignmentTemplateSummary(
                id=1,
                course_id=1,
                name="Default Template",
                template_pdf_relpath=(
                    "courses/cmpe-195-spring-2026/"
                    "assignments/1-hw-1/templates/default-template-t1/template.pdf"
                ),
                spec_json_relpath=(
                    "courses/cmpe-195-spring-2026/"
                    "assignments/1-hw-1/templates/default-template-t1/spec.json"
                ),
                spec_pending=False,
            )
        ]
