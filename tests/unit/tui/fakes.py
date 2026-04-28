from pathlib import Path

from opengrader.tui.data import QuestionSeed, RubricOptionSeed, StudentSummary

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_IMAGE_PATH = str(PROJECT_ROOT / "images" / "Grading.jpg")


class FakeDataProvider:
    def list_students(self, course_id: int | None) -> list[StudentSummary]:
        if course_id is None:
            return []
        return [
            StudentSummary(1, "Tommy Liu"),
            StudentSummary(2, "Andy Cortes"),
        ]

    def build_question_seeds(
        self,
        *,
        assignment_id: int | None = None,
        assignment_title: str | None = None,
        student_item_id: str | None = None,
    ) -> list[QuestionSeed]:
        _ = (assignment_id, student_item_id)
        rubric_options = [
            RubricOptionSeed(label="Correct", points_delta=0.0),
            RubricOptionSeed(label="Incorrect", points_delta=-10.0),
        ]
        if assignment_title not in (None, "HW 1"):
            return [QuestionSeed("Q1", DEFAULT_IMAGE_PATH, list(rubric_options))]
        return [
            QuestionSeed("Q1", DEFAULT_IMAGE_PATH, list(rubric_options)),
            QuestionSeed("Q2", DEFAULT_IMAGE_PATH, list(rubric_options)),
        ]
