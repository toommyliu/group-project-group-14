from __future__ import annotations

from pathlib import Path

from opengrader.core.submissions import load_student_submission_summary
from opengrader.core.roster import read_roster
from opengrader.tui.data.models import (
    AssignmentSummary,
    AssignmentTemplateSummary,
    CourseSummary,
    QuestionSeed,
    StudentSummary,
    StudentSubmissionSummary,
)
from opengrader.tui.data.qa_question_content import QaQuestionContent
from opengrader.tui.data.stub_question_content import StubQuestionContent


class StubDataProvider:
    def __init__(self, default_image_path: str) -> None:
        self._question_content = StubQuestionContent(default_image_path)
        self._qa_content = QaQuestionContent(default_image_path)
        self._sample_submission_dir = (
            Path(__file__).resolve().parents[4]
            / "samples"
            / "cs146-midterm1"
            / "student-submissions"
        )

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
            AssignmentSummary(id=2, title="Quiz 1", assignment_type="quiz"),
            AssignmentSummary(id=3, title="Midterm", assignment_type="exam"),
        ]

    def list_students(self, course_id: int | None) -> list[StudentSummary]:
        if course_id is None:
            return []
        submission_students = self._students_from_submission_files()
        if submission_students:
            return submission_students
        roster_path = Path(__file__).resolve().parents[4] / "data" / "roster.csv"
        try:
            rows = read_roster(str(roster_path))
        except OSError:
            rows = []
        if not rows:
            return []
        return [
            StudentSummary(
                id=index + 1,
                full_name=(row.get("name") or f"Student {index + 1}").strip(),
            )
            for index, row in enumerate(rows)
        ]

    def get_student_submission_summary(
        self,
        *,
        student_id: int | None,
        student_name: str | None,
        assignment_id: int | None,
        assignment_title: str | None,
    ) -> StudentSubmissionSummary | None:
        _ = (student_name, assignment_id, assignment_title)
        if student_id is None or student_id <= 0:
            return None
        sample_index = student_id - 1
        sample_submission_paths = self._sample_submission_paths()
        if sample_index >= len(sample_submission_paths):
            return None

        path = sample_submission_paths[sample_index]
        if not path.exists():
            return None

        summary = load_student_submission_summary(path)
        return StudentSubmissionSummary(
            name=summary.name,
            student_id=summary.student_id,
            course_code=summary.course_code,
            assignment_title=summary.assignment_title,
            assignment_version=summary.assignment_version,
            source_pdf=summary.source_pdf,
            score=summary.score,
            markdown_path=str(summary.markdown_path),
        )

    def build_question_seeds(
        self,
        *,
        assignment_id: int | None = None,
        assignment_title: str | None = None,
        assignment_version: str | None = None,
        submission_markdown_path: str | None = None,
    ) -> list[QuestionSeed]:
        _ = assignment_id
        qa_seeds = self._qa_content.build_question_seeds(
            assignment_title=assignment_title,
            assignment_version=assignment_version,
            submission_markdown_path=submission_markdown_path,
        )
        if qa_seeds:
            return qa_seeds
        return self._question_content.build_question_seeds(
            assignment_title=assignment_title,
            assignment_version=assignment_version,
        )

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
    ) -> AssignmentTemplateSummary:
        _ = template_pdf_path
        return AssignmentTemplateSummary(
            id=1,
            course_id=1,
            name=name,
            template_pdf_relpath=f"courses/cmpe-195/assignments/{assignment_id}-hw-1/templates/{name.lower().replace(' ', '-')}-t1.pdf",
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
                "courses/cmpe-195-spring-2026/"
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
                    "courses/cmpe-195-spring-2026/"
                    "assignments/1-hw-1/templates/default-template-t1.pdf"
                ),
            )
        ]

    def _sample_submission_paths(self) -> list[Path]:
        return sorted(self._sample_submission_dir.glob("*.submission.md"))

    def _students_from_submission_files(self) -> list[StudentSummary]:
        students: list[StudentSummary] = []
        for index, path in enumerate(self._sample_submission_paths()):
            summary = load_student_submission_summary(path)
            full_name = summary.name.strip() or f"Student {index + 1}"
            students.append(StudentSummary(id=index + 1, full_name=full_name))
        return students
