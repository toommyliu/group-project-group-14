from __future__ import annotations

from pathlib import Path

from opengrader.core.pdf.templates import load_qa_markdown
from opengrader.core.pdf.templates.qa_file import (
    QAQuestion,
    QARubricItem,
    QASubquestion,
)
from opengrader.core.submissions import load_student_submission_document
from opengrader.tui.data.models import QuestionSeed, RubricOptionSeed


class QaQuestionContent:
    def __init__(self, default_image_path: str) -> None:
        self._default_image_path = default_image_path

    def build_question_seeds(
        self,
        *,
        assignment_title: str | None = None,
        assignment_version: str | None = None,
        submission_markdown_path: str | None = None,
    ) -> list[QuestionSeed]:
        qa_path = self._qa_path_for_assignment(assignment_title)
        if qa_path is None or not qa_path.exists():
            return []

        document = load_qa_markdown(qa_path)
        questions = self._questions_for_version(document, assignment_version)
        seeds = [
            QuestionSeed(
                question_id=question_id,
                prompt=prompt,
                image_path=self._default_image_path,
                rubric_options=rubric_options,
                answers=answers,
            )
            for question_id, prompt, answers, rubric_options in self._flatten_questions(
                questions
            )
        ]
        return self._apply_submission_images(
            seeds,
            submission_markdown_path=submission_markdown_path,
        )

    @staticmethod
    def _qa_path_for_assignment(assignment_title: str | None) -> Path | None:
        normalized = " ".join((assignment_title or "").strip().lower().split())
        project_root = Path(__file__).resolve().parents[4]
        if normalized in {"quiz 1", "cs146: quiz 1", "quiz1"}:
            return project_root / "samples" / "qa-multi.md"
        return None

    @staticmethod
    def _questions_for_version(document, version: str | None) -> list[QAQuestion]:
        if not document.version_questions:
            return document.questions

        selected = version
        if selected is None:
            selected = (
                document.versions[0]
                if document.versions
                else next(iter(document.version_questions))
            )
        return document.version_questions.get(selected, document.questions)

    def _apply_submission_images(
        self,
        seeds: list[QuestionSeed],
        *,
        submission_markdown_path: str | None,
    ) -> list[QuestionSeed]:
        if not submission_markdown_path:
            return seeds

        submission = load_student_submission_document(submission_markdown_path)
        image_paths = {
            question.question_id.upper(): str(question.response_image_path)
            for question in submission.questions
            if question.response_image_path is not None
        }
        return [
            QuestionSeed(
                question_id=seed.question_id,
                prompt=seed.prompt,
                image_path=image_paths.get(seed.question_id.upper(), seed.image_path),
                rubric_options=seed.rubric_options,
                answers=seed.answers,
                response_text=seed.response_text,
            )
            for seed in seeds
        ]

    def _flatten_questions(
        self,
        questions: list[QAQuestion],
    ) -> list[tuple[str, str, list[str], list[RubricOptionSeed]]]:
        flattened: list[tuple[str, str, list[str], list[RubricOptionSeed]]] = []
        for question in questions:
            if not question.subquestions:
                flattened.append(
                    (
                        question.id.upper(),
                        question.prompt.strip(),
                        list(question.answers),
                        self._rubric_options(question.rubric),
                    )
                )
                continue

            for subquestion in question.subquestions:
                flattened.append(self._flatten_subquestion(question, subquestion))
        return flattened

    def _flatten_subquestion(
        self,
        parent: QAQuestion,
        subquestion: QASubquestion,
    ) -> tuple[str, str, list[str], list[RubricOptionSeed]]:
        prompt_prefix = parent.prompt.strip()
        prompt = subquestion.prompt.strip()
        if prompt_prefix:
            prompt = f"{prompt_prefix} {prompt}"
        return (
            subquestion.id.upper(),
            prompt,
            list(subquestion.answers),
            self._rubric_options(subquestion.rubric),
        )

    @staticmethod
    def _rubric_options(rubric: list[QARubricItem]) -> list[RubricOptionSeed]:
        options: list[RubricOptionSeed] = []
        for item in rubric:
            if item.subitems:
                for subitem in item.subitems:
                    if subitem.points_delta is None:
                        continue
                    options.append(
                        RubricOptionSeed(
                            label=f"{item.label}: {subitem.label}",
                            points_delta=subitem.points_delta,
                        )
                    )
                continue
            if item.points_delta is None:
                continue
            options.append(
                RubricOptionSeed(
                    label=item.label,
                    points_delta=item.points_delta,
                )
            )
        return options
