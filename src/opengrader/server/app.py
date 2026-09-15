import logging
import shutil
from contextlib import asynccontextmanager

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from PIL import Image

from opengrader.core.export.gradebook import discover_submission_paths
from opengrader.core.grading.questions import (
    QuestionContext,
    flatten_question_contexts,
    questions_for_submission,
    rubric_options,
)
from opengrader.core.qa_file.scoring import RubricOptionKind, RubricSelectionMode
from opengrader.core.question_types import QuestionType
from opengrader.core.submissions import load_student_submission_markdown
from opengrader.server.assignment_qa import AssignmentInput, QAPreview, preview_qa
from opengrader.server.web import add_web_routes
from opengrader.server.assignment_pdf import AssignmentPDFInput, PDF_RESPONSES, assignment_pdf_response
from opengrader.server.scan_store import ScanStore
from opengrader.server.scan_jobs import ScanJobs
from opengrader.server.scans import add_scan_routes
from opengrader.server.submission_ids import submission_ids


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


class AssignmentUpdate(AssignmentInput):
    expected_qa_markdown: str


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
    qa: QAPreview
    questions: dict[int, list[Question]]
    answers: dict[tuple[int, str], Answer]
    submissions: list[Submission]
    images: dict[tuple[int, str], Path]


def load_assignment(
    assignment_id: str, qa_file: Path, submissions: Path, *, paths: list[Path] | None = None
) -> LoadedAssignment:
    """Read an assignment once; HTTP requests use the loaded snapshot."""
    qa_file = qa_file.expanduser().resolve()
    preview = preview_qa(qa_file.read_text(encoding="utf-8"))
    qa = preview.document
    submissions = submissions.expanduser().resolve()
    paths = discover_submission_paths(submissions) if paths is None else paths
    ids = submission_ids(submissions, paths)

    questions_by_submission: dict[int, list[Question]] = {}
    answers: dict[tuple[int, str], Answer] = {}
    summaries: list[Submission] = []
    images: dict[tuple[int, str], Path] = {}
    questions_by_version: dict[str | None, list[Question]] = {}
    image_sizes: dict[Path, tuple[int, int] | None] = {}
    for path in paths:
        submission_id = ids[path.relative_to(submissions).as_posix()]
        document = load_student_submission_markdown(path)
        version, qa_questions = questions_for_submission(qa, document)
        if version not in questions_by_version:
            questions_by_version[version] = [
                _question(c) for c in flatten_question_contexts(qa_questions)
            ]
        questions = questions_by_version[version]
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
        summary=Assignment(id=assignment_id, title=qa.title or "Untitled assignment"),
        qa=preview,
        questions=questions_by_submission,
        answers=answers,
        submissions=sorted(summaries, key=lambda submission: submission.id),
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

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await run_in_threadpool(scan_jobs.close)

    app = FastAPI(title="Opengrader", version="0.0.1", lifespan=lifespan)
    registry_lock = RLock()
    scan_store = ScanStore(assignments_dir)
    scan_store.recover()

    def refresh_assignment(assignment_id: str, markdown: Path) -> None:
        directory = assignments_dir / assignment_id
        added = load_assignment(assignment_id, directory / "qa.md", directory / "submissions", paths=[markdown])
        current = assignments[assignment_id]
        summaries = {submission.id: submission for submission in current.submissions + added.submissions}
        assignments[assignment_id] = LoadedAssignment(
            summary=current.summary, qa=current.qa,
            questions=current.questions | added.questions,
            answers=current.answers | added.answers,
            submissions=sorted(summaries.values(), key=lambda submission: submission.id),
            images=current.images | added.images,
        )

    scan_jobs = ScanJobs(scan_store, registry_lock, refresh_assignment)
    app.state.scan_jobs = scan_jobs

    def assignment_for(assignment_id: str) -> LoadedAssignment:
        with registry_lock:
            if assignment_id in scan_jobs.stopping:
                raise HTTPException(409, "This assignment is being deleted.")
            assignment = assignments.get(assignment_id)
        if assignment is None:
            raise HTTPException(404, "Assignment not found")
        return assignment

    @app.get("/assignments")
    def list_assignments() -> list[Assignment]:
        with registry_lock:
            return [assignment.summary for assignment in assignments.values()]

    def validated_preview(request: AssignmentInput) -> QAPreview:
        try:
            return preview_qa(request.qa_markdown)
        except ValueError as exc:
            raise HTTPException(
                422,
                detail=[{
                    "loc": ["body", "qa_markdown"],
                    "msg": str(exc),
                    "type": "value_error",
                }],
            ) from exc

    @app.post("/assignments/preview")
    def preview_assignment(request: AssignmentInput) -> QAPreview:
        return validated_preview(request)

    @app.post("/assignments/preview/pdf", response_class=Response, responses=PDF_RESPONSES)
    def preview_assignment_pdf(request: AssignmentPDFInput) -> Response:
        return assignment_pdf_response(validated_preview(request).document, request.version)

    @app.post("/assignments", status_code=201)
    def create_assignment(request: AssignmentInput) -> Assignment:
        """Publish complete assignment files before adding the in-memory snapshot."""
        preview = validated_preview(request)
        assignment_id = str(uuid4())
        summary = Assignment(
            id=assignment_id, title=preview.document.title or "Untitled assignment"
        )
        loaded = LoadedAssignment(
            summary=summary, qa=preview, questions={}, answers={}, submissions=[], images={}
        )
        destination = assignments_dir / assignment_id
        try:
            with TemporaryDirectory(prefix=".create-", dir=assignments_dir) as staging:
                directory = Path(staging)
                (directory / "qa.md").write_bytes(request.qa_markdown.encode("utf-8"))
                (directory / "submissions").mkdir()
                # Publish complete files before exposing the assignment to requests.
                with registry_lock:
                    if destination.exists():
                        raise FileExistsError("Assignment ID already exists")
                    directory.rename(destination)
                    assignments[assignment_id] = loaded
        except OSError as exc:
            raise HTTPException(
                500, "Could not save assignment. Check server storage and try again."
            ) from exc
        return summary

    def editable_assignment(assignment_id: str) -> LoadedAssignment:
        assignment = assignment_for(assignment_id)
        submissions = assignments_dir / assignment_id / "submissions"
        # Check disk as well as the snapshot, including files added through the CLI.
        if scan_store.list(assignment_id):
            raise HTTPException(409, "Assignments with uploaded scans cannot be edited.")
        if assignment.submissions or any(submissions.iterdir()):
            raise HTTPException(409, "Assignments with submissions cannot be edited.")
        return assignment

    @app.get("/assignments/{assignment_id}/source")
    def get_assignment_source(assignment_id: str) -> AssignmentInput:
        with registry_lock:
            assignment_for(assignment_id)
            try:
                content = (assignments_dir / assignment_id / "qa.md").read_bytes().decode("utf-8")
            except OSError as exc:
                raise HTTPException(500, "Could not read assignment source.") from exc
            return AssignmentInput(qa_markdown=content)

    @app.put("/assignments/{assignment_id}")
    def update_assignment(assignment_id: str, request: AssignmentUpdate) -> Assignment:
        preview = validated_preview(request)
        with registry_lock:
            try:
                editable_assignment(assignment_id)
                destination = assignments_dir / assignment_id / "qa.md"
                if destination.read_bytes() != request.expected_qa_markdown.encode("utf-8"):
                    raise HTTPException(409, "This assignment changed since you opened it. Reopen it before saving.")
                summary = Assignment(id=assignment_id, title=preview.document.title or "Untitled assignment")
                loaded = LoadedAssignment(summary=summary, qa=preview, questions={}, answers={}, submissions=[], images={})
                with TemporaryDirectory(prefix=".update-", dir=assignments_dir) as staging:
                    source = Path(staging) / "qa.md"
                    source.write_bytes(request.qa_markdown.encode("utf-8"))
                    source.replace(destination)
                assignments[assignment_id] = loaded
            except OSError as exc:
                raise HTTPException(500, "Could not save assignment. Check server storage and try again.") from exc
        return summary

    @app.delete("/assignments/{assignment_id}", status_code=204)
    def delete_assignment(assignment_id: str) -> Response:
        with registry_lock:
            assignment_for(assignment_id)
            futures = scan_jobs.stop_assignment(assignment_id)
        try:
            # Extraction writes staging files outside the lock. Let it stop before removing its directory.
            scan_jobs.wait_for_stopped(futures)
            with registry_lock:
                deleted = assignments_dir / f".deleted-{uuid4()}"
                (assignments_dir / assignment_id).rename(deleted)
                del assignments[assignment_id]
        except OSError as exc:
            with registry_lock:
                for batch in scan_store.list(assignment_id):
                    if batch.status in ("checking", "queued", "processing"):
                        batch.status = "failed"
                        batch.message = "Processing stopped for deletion, but deletion failed. Retry processing or delete again."
                        batch.revision += 1
                        scan_store.save(assignment_id, batch)
            raise HTTPException(500, "Could not delete assignment. Check server storage and try again.") from exc
        finally:
            with registry_lock:
                scan_jobs.stopping.discard(assignment_id)
        # Once unpublished, cleanup failure must not report the deletion as failed.
        try:
            shutil.rmtree(deleted)
        except OSError:
            logging.getLogger(__name__).exception("Could not clean deleted assignment %s", deleted)
        return Response(status_code=204)

    @app.get("/assignments/{assignment_id}/qa")
    def get_assignment_qa(assignment_id: str) -> QAPreview:
        return assignment_for(assignment_id).qa

    @app.get("/assignments/{assignment_id}/pdf", response_class=Response, responses=PDF_RESPONSES)
    def get_assignment_pdf(assignment_id: str, version: str | None = None) -> Response:
        return assignment_pdf_response(assignment_for(assignment_id).qa.document, version)

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

    def discard_scan_submissions(assignment_id: str, directories: list[Path]) -> None:
        root = assignments_dir / assignment_id / "submissions"
        ids = submission_ids(root, [])
        removed = {ids.get(f"{directory.name}/{directory.name}.submission.md") for directory in directories}
        current = assignments[assignment_id]
        assignments[assignment_id] = LoadedAssignment(
            summary=current.summary, qa=current.qa,
            questions={key: value for key, value in current.questions.items() if key not in removed},
            answers={key: value for key, value in current.answers.items() if key[0] not in removed},
            submissions=[item for item in current.submissions if item.id not in removed],
            images={key: value for key, value in current.images.items() if key[0] not in removed},
        )

    add_scan_routes(app, scan_store, scan_jobs, registry_lock, assignment_for, discard_scan_submissions)
    add_web_routes(
        app,
        web_dist if web_dist is not None else Path(__file__).parent.parent / "web" / "dist",
    )
    return app
