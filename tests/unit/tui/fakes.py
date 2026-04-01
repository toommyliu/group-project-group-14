from pathlib import Path

from local_grader.tui.state import QuestionGrade, RubricOption
from local_grader.tui.widgets.nav_item import NavItem

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_IMAGE_PATH = str(PROJECT_ROOT / "images" / "Grading.jpg")


class FakeSampleDataService:
    def load_courses(self) -> list[NavItem]:
        return [
            NavItem("c1", "CMPE 195", "Spring 2026"),
            NavItem("c2", "CMPE 102", "Spring 2026"),
        ]

    def load_assignments(self, course_id: int | None) -> list[NavItem]:
        if course_id is None:
            return []
        return [
            NavItem("a1", "HW 1"),
            NavItem("a2", "HW 2"),
        ]

    def load_students(self, status_lookup) -> list[NavItem]:
        students = [
            ("s1", "Tommy Liu"),
            ("s2", "Andy Cortes"),
        ]
        return [
            NavItem(item_id, label, status_lookup(item_id))
            for item_id, label in students
        ]

    def build_question_grades(self) -> list[QuestionGrade]:
        rubric_options = [
            RubricOption(label="Correct", points_delta=0.0),
            RubricOption(label="Incorrect", points_delta=-10.0),
        ]
        return [
            QuestionGrade("Q1", DEFAULT_IMAGE_PATH, list(rubric_options)),
            QuestionGrade("Q2", DEFAULT_IMAGE_PATH, list(rubric_options)),
        ]
