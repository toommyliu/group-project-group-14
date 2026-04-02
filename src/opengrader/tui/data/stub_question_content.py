from __future__ import annotations

from pathlib import Path

import fitz

from opengrader.tui.data.models import QuestionSeed, RubricOptionSeed


class StubQuestionContent:
    def __init__(self, default_image_path: str) -> None:
        self._default_image_path = default_image_path
        self._question_images: list[tuple[str, str]] = []

    def build_question_seeds(self) -> list[QuestionSeed]:
        rubric_options = [
            RubricOptionSeed(label="Correct", points_delta=0.0),
            RubricOptionSeed(label="Partially Correct", points_delta=-4.0),
            RubricOptionSeed(label="Incorrect", points_delta=-10.0),
        ]
        output: list[QuestionSeed] = []
        for question_id, image_path in self._load_question_images():
            output.append(
                QuestionSeed(
                    question_id=question_id,
                    image_path=image_path,
                    rubric_options=list(rubric_options),
                )
            )
        return output

    def _load_question_images(self) -> list[tuple[str, str]]:
        if self._question_images:
            return self._question_images

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
        self._question_images = [
            (question_id, question_to_path[question_id])
            for question_id in ordered_questions
        ]
        return self._question_images

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
