from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from PIL import Image

from opengrader.core.export.gradebook import discover_submission_paths
from opengrader.core.grading.questions import (
    QuestionContext,
    flatten_question_contexts,
    questions_for_submission,
    rubric_options,
)
from opengrader.core.qa_file import load_qa_markdown
from opengrader.core.qa_file.scoring import RubricOptionKind, RubricSelectionMode
from opengrader.core.question_types import QuestionType
from opengrader.core.submissions import load_student_submission_markdown
from opengrader.server.web import add_web_routes


class RubricOption(BaseModel):
    label: str
    points_delta: float
    kind: RubricOptionKind
    selection_mode: RubricSelectionMode
    default_comment: str | None


class Question(BaseModel):
    id: str
    prompt: str
    possible_points: float
    question_type: QuestionType
    expected_answers: list[str]
    rubric: list[RubricOption]


class Assignment(BaseModel):
    id: str
    title: str


class Submission(BaseModel):
    id: int
    version: str | None


class Answer(BaseModel):
    submission_id: int
    version: str | None
    question: Question
    response_text: str
    response_image_url: str | None
    response_image_width: int | None
    response_image_height: int | None
    points_awarded: float | None
    rubric_labels: tuple[str, ...]
    analysis: str


def _question(context: QuestionContext) -> Question:
    return Question(
        id=context.question_id,
        prompt=context.prompt,
        possible_points=context.points,
        question_type=context.question_type,
        expected_answers=context.expected_answers,
        rubric=[
            RubricOption(
                label=option.label,
                points_delta=option.points_delta,
                kind=option.kind,
                selection_mode=option.selection_mode,
                default_comment=option.default_comment,
            )
            for option in rubric_options(context)
        ],
    )


@dataclass
class LoadedAssignment:
    summary: Assignment
    questions: dict[int, list[Question]]
    answers: dict[tuple[int, str], Answer]
    submissions: list[Submission]
    images: dict[tuple[int, str], Path]


def load_assignment(
    assignment_id: str, qa_file: Path, submissions: Path
) -> LoadedAssignment:
    """Read an assignment once; HTTP requests use the loaded snapshot."""
    qa_file = qa_file.expanduser().resolve()
    qa = load_qa_markdown(qa_file)
    paths = discover_submission_paths(submissions.expanduser().resolve())
    if not paths:
        raise ValueError(f"no *.submission.md files found in {submissions}")

    questions_by_submission: dict[int, list[Question]] = {}
    answers: dict[tuple[int, str], Answer] = {}
    summaries: list[Submission] = []
    images: dict[tuple[int, str], Path] = {}
    questions_by_version: dict[str | None, list[Question]] = {}
    image_sizes: dict[Path, tuple[int, int] | None] = {}
    for submission_id, path in enumerate(paths, start=1):
        document = load_student_submission_markdown(path)
        version, qa_questions = questions_for_submission(qa, document)
        if version not in questions_by_version:
            questions_by_version[version] = [
                _question(c) for c in flatten_question_contexts(qa_questions)
            ]
        questions = questions_by_version[version]
        if not questions:
            raise ValueError(f"no grading questions found in {qa_file}")
        questions_by_submission[submission_id] = questions
        summaries.append(Submission(id=submission_id, version=version))
        responses = {q.question_id: q for q in document.questions}
        for question in questions:
            response = responses.get(question.id)
            if response is None:
                continue
            image_url = None
            image_size = None
            if response.response_image_path:
                image = Path(response.response_image_path).resolve()
                # Serve only crops inside this assignment, never a referenced arbitrary file.
                if (
                    image.is_relative_to(qa_file.parent)
                    and image.is_file()
                    and image.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                ):
                    if image not in image_sizes:
                        try:
                            with Image.open(image) as crop:
                                image_sizes[image] = crop.size
                        except (OSError, ValueError):
                            image_sizes[image] = None
                    image_size = image_sizes[image]
                    images[submission_id, question.id] = image
                    image_url = (
                        f"/assignments/{quote(assignment_id, safe='')}/submissions/{submission_id}"
                        f"/answers/{question.id}/image"
                    )
            answers[submission_id, question.id] = Answer(
                submission_id=submission_id,
                version=version,
                question=question,
                response_text=response.response_text,
                response_image_url=image_url,
                response_image_width=image_size[0] if image_size else None,
                response_image_height=image_size[1] if image_size else None,
                points_awarded=response.points_awarded,
                rubric_labels=response.rubric_labels,
                analysis=response.analysis,
            )

    return LoadedAssignment(
        summary=Assignment(id=assignment_id, title=qa.title or assignment_id),
        questions=questions_by_submission,
        answers=answers,
        submissions=summaries,
        images=images,
    )


def create_app(data_home: Path, *, web_dist: Path | None = None) -> FastAPI:
    assignments_dir = data_home / "assignments"
    assignments_dir.mkdir(parents=True, exist_ok=True)
    assignments: dict[str, LoadedAssignment] = {}
    for directory in sorted(assignments_dir.iterdir()):
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        try:
            assignments[directory.name] = load_assignment(
                directory.name,
                directory / "qa.md",
                directory / "submissions",
            )
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"Cannot open assignment {directory.name!r}: {exc}"
            ) from exc

    app = FastAPI(title="Opengrader", version="0.0.1")

    def assignment_for(assignment_id: str) -> LoadedAssignment:
        assignment = assignments.get(assignment_id)
        if assignment is None:
            raise HTTPException(404, "Assignment not found")
        return assignment

    @app.get("/assignments")
    def list_assignments() -> list[Assignment]:
        return [assignment.summary for assignment in assignments.values()]

    @app.get("/assignments/{assignment_id}/submissions")
    def list_submissions(assignment_id: str) -> list[Submission]:
        return assignment_for(assignment_id).submissions

    @app.get("/assignments/{assignment_id}/questions")
    def list_questions(assignment_id: str, submission_id: int = 1) -> list[Question]:
        """List the selected submission's answer-key version; default to the first."""
        questions = assignment_for(assignment_id).questions.get(submission_id)
        if questions is None:
            raise HTTPException(404, "Submission not found")
        return questions

    @app.get(
        "/assignments/{assignment_id}/submissions/{submission_id}/answers/{question_id}"
    )
    def get_answer(assignment_id: str, submission_id: int, question_id: str) -> Answer:
        answer = assignment_for(assignment_id).answers.get((submission_id, question_id))
        if answer is None:
            raise HTTPException(404, "Answer not found")
        return answer

    @app.get(
        "/assignments/{assignment_id}/submissions/{submission_id}/answers/{question_id}/image"
    )
    def get_answer_image(
        assignment_id: str, submission_id: int, question_id: str
    ) -> FileResponse:
        image = assignment_for(assignment_id).images.get((submission_id, question_id))
        if image is None or not image.is_file():
            raise HTTPException(404, "Answer image not found")
        return FileResponse(image)

    add_web_routes(
        app,
        web_dist if web_dist is not None else Path(__file__).parent.parent / "web" / "dist",
    )
    return app
