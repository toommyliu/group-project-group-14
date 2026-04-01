from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import fitz

from local_grader.tui.state import QuestionGrade, RubricOption
from local_grader.tui.widgets.miller_column import MillerItem


class SampleDataService:
    def __init__(self, default_image_path: str) -> None:
        self._default_image_path = default_image_path
        self._sample_question_images: list[tuple[str, str]] = []

    def load_courses(self) -> list[MillerItem]:
        return [
            MillerItem("c1", "CMPE 195", "Spring 2026"),
            MillerItem("c2", "CMPE 102", "Spring 2026"),
        ]

    def load_assignments(self, course_id: int | None) -> list[MillerItem]:
        if course_id is None:
            return []
        return [
            MillerItem("a1", "HW 1"),
            MillerItem("a2", "HW 2"),
            MillerItem("a3", "Midterm"),
        ]

    def load_students(
        self,
        status_lookup: Callable[[str], str],
    ) -> list[MillerItem]:
        students = [
            ("s1", "Tommy Liu"),
            ("s2", "Andy Cortes"),
            ("s3", "Arjun Gill"),
            ("s4", "Marco Lopez"),
        ]
        return [
            MillerItem(item_id, label, status_lookup(item_id))
            for item_id, label in students
        ]

    def build_question_grades(self) -> list[QuestionGrade]:
        rubric_options = [
            RubricOption(label="Correct", points_delta=0.0),
            RubricOption(label="Partially Correct", points_delta=-4.0),
            RubricOption(label="Incorrect", points_delta=-10.0),
        ]
        output: list[QuestionGrade] = []
        for question_id, image_path in self._load_sample_question_images():
            output.append(
                QuestionGrade(
                    question_id=question_id,
                    image_path=image_path,
                    rubric_options=list(rubric_options),
                )
            )
        return output

    def _load_sample_question_images(self) -> list[tuple[str, str]]:
        if self._sample_question_images:
            return self._sample_question_images

        source_dir = Path("tests/question_split_output")
        image_dir = Path(".tui_cache/question_split_images")
        image_dir.mkdir(parents=True, exist_ok=True)

        question_to_path: dict[str, str] = {}
        for pdf_path in sorted(source_dir.glob("question_*.pdf")):
            image_path = image_dir / f"{pdf_path.stem}.png"
            if not image_path.exists():
                self._render_pdf_page_to_png(pdf_path, image_path)

            for question_id in self._question_ids_from_stem(pdf_path.stem):
                question_to_path[question_id] = str(image_path)

        if not question_to_path:
            return [("Q1", self._default_image_path)]

        ordered_questions = sorted(
            question_to_path.keys(),
            key=lambda qid: int(qid[1:]) if qid[1:].isdigit() else 999,
        )
        self._sample_question_images = [
            (question_id, question_to_path[question_id])
            for question_id in ordered_questions
        ]
        return self._sample_question_images

    @staticmethod
    def _question_ids_from_stem(stem: str) -> list[str]:
        raw = stem.removeprefix("question_")
        parts = [part for part in raw.split("_") if part]
        ids: list[str] = []
        for part in parts:
            if part.isdigit():
                ids.append(f"Q{int(part)}")
        if ids:
            return ids
        return [stem.upper()]

    @staticmethod
    def _render_pdf_page_to_png(pdf_path: Path, image_path: Path) -> None:
        doc = fitz.open(str(pdf_path))
        try:
            page = doc[0]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            pixmap.save(str(image_path))
        finally:
            doc.close()
