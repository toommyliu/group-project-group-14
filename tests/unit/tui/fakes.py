from pathlib import Path

from local_grader.tui.data import (
    AssignmentSummary,
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
        assessment_type: str,
        title: str,
        max_points: str,
    ) -> AssignmentSummary:
        _ = (course_id, max_points)
        return AssignmentSummary(99, title, assessment_type)

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
