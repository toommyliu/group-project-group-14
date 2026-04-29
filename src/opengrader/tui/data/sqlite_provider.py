from __future__ import annotations

from pathlib import Path

from opengrader.core import resolve_data_dir, resolve_database_path
from opengrader.core.courses import (
    CourseNotFoundError,
    StudentInput,
    build_sqlite_course_services,
)
from opengrader.core.submissions import load_student_submission_summary
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
        self._qa_content = QaQuestionContent(default_image_path)
        self._sample_submission_dir = (
            Path(__file__).resolve().parents[4]
            / "samples"
            / "cs146-midterm1"
            / "student-submissions"
        )

    @property
    def _services(self):
        if self._courses is None:
            self._courses = build_sqlite_course_services(self._db_path)
        return self._courses

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
        submission_students = self._students_from_submission_files()
        if submission_students:
            return submission_students
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

    def get_student_submission_summary(
        self,
        *,
        student_id: int | None,
        student_name: str | None,
        assignment_id: int | None,
        assignment_title: str | None,
    ) -> StudentSubmissionSummary | None:
        _ = (student_name, assignment_id)
        normalized_assignment = " ".join((assignment_title or "").strip().lower().split())
        if normalized_assignment not in {"quiz 1", "cs146: quiz 1", "quiz1"}:
            return None
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
    ) -> AssignmentTemplateSummary:
        template = self._services.assignment_templates.create_template(
            assignment_id=assignment_id,
            name=name,
            source_template_pdf=template_pdf_path,
        )
        return AssignmentTemplateSummary(
            id=template.id,
            course_id=template.course_id,
            name=template.name,
            template_pdf_relpath=template.template_pdf_relpath,
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
            )
            for template in templates
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
            )
            for template in templates
        ]
