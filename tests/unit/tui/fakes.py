from pathlib import Path

from opengrader.tui.data import (
    AssignmentSummary,
    AssignmentTemplateSummary,
    CourseSummary,
    QuestionSeed,
    RubricOptionSeed,
    StudentSummary,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_IMAGE_PATH = str(PROJECT_ROOT / "images" / "Grading.jpg")


class FakeDataProvider:
    def list_courses(self) -> list[CourseSummary]:
        return [
            CourseSummary(1, "CMPE", "195", "Spring 2026", ""),
            CourseSummary(2, "CMPE", "102", "Spring 2026", ""),
        ]

    def list_assignments(self, course_id: int | None) -> list[AssignmentSummary]:
        if course_id is None:
            return []
        return [
            AssignmentSummary(1, "HW 1", ""),
            AssignmentSummary(2, "HW 2", ""),
        ]

    def list_students(self, course_id: int | None) -> list[StudentSummary]:
        if course_id is None:
            return []
        return [
            StudentSummary(1, "Tommy Liu"),
            StudentSummary(2, "Andy Cortes"),
        ]

    def build_question_seeds(self) -> list[QuestionSeed]:
        rubric_options = [
            RubricOptionSeed(label="Correct", points_delta=0.0),
            RubricOptionSeed(label="Incorrect", points_delta=-10.0),
        ]
        return [
            QuestionSeed("Q1", DEFAULT_IMAGE_PATH, list(rubric_options)),
            QuestionSeed("Q2", DEFAULT_IMAGE_PATH, list(rubric_options)),
        ]

    def create_course(
        self,
        *,
        code: str,
        name: str,
        term: str,
        section: str,
    ) -> CourseSummary:
        return CourseSummary(99, code, name, term, section)

    def create_assignment(
        self,
        *,
        course_id: int,
        assignment_type: str,
        title: str,
        max_points: str,
    ) -> AssignmentSummary:
        _ = (course_id, max_points)
        return AssignmentSummary(99, title, assignment_type)

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
        return StudentSummary(99, f"{first_name} {last_name}")

    def create_assignment_template(
        self,
        *,
        assignment_id: int,
        name: str,
        template_pdf_path: str,
    ) -> AssignmentTemplateSummary:
        _ = template_pdf_path
        return AssignmentTemplateSummary(
            id=1,
            course_id=1,
            name=name,
            template_pdf_relpath=(
                f"courses/cmpe-195-senior-design-spring-2026-01/"
                f"assignments/{assignment_id}-hw-1/templates/"
                f"{name.lower().replace(' ', '-')}-t1.pdf"
            ),
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
                    "courses/cmpe-195-senior-design-spring-2026-01/"
                    "assignments/1-hw-1/templates/default-template-t1.pdf"
                ),
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
                "courses/cmpe-195-senior-design-spring-2026-01/"
                "assignments/1-hw-1/templates/default-template-t1.pdf"
            ),
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
                    "courses/cmpe-195-senior-design-spring-2026-01/"
                    "assignments/1-hw-1/templates/default-template-t1.pdf"
                ),
            )
        ]
