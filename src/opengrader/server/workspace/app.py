"""Workspace API shared by the terminal and browser clients."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
import re
import secrets
import shutil
from tempfile import NamedTemporaryFile
from zipfile import ZIP_STORED, ZipFile
from threading import RLock

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from starlette._utils import get_route_path
from starlette.background import BackgroundTask

from opengrader.core.export.bundle import student_pdf_stem, unique_stem
from opengrader.core.grading.edits import GradeEdit
from opengrader.core.outline import outline_versions
from opengrader.core.qa_file import QARubricItem, parse_qa_markdown
from opengrader.core.qa_file.errors import QAParseError
from opengrader.core.submissions.region_crop import CropInputs, RosterStudent
from opengrader.core.workspace import roster
from opengrader.core.analytics.assignment_statistics import (
    AssignmentVersionStatistics,
)
from opengrader.core.workspace.store import (
    DOCUMENT,
    Workspace,
    WorkspaceConflict,
    WorkspaceNotFound,
    queue_feedback,
    record_event,
)
from opengrader.core.workspace.folder_session import (
    active_folder_session,
    KeyEdit,
    edit_answer_key,
    end_folder_session,
    folder_lock,
    folder_session_info,
    load_folder_session,
    regrade_folder,
    sync_answer_key,
    sync_folder,
    write_through,
)
from opengrader.core.workspace.roster import RosterConflict
from opengrader.core.workspace.writeback import WriteBackError
from opengrader.core.workspace.intake import publish_pipeline, publish_submission
from opengrader.core.workspace.recut import recutting_assignments
from opengrader.core.submissions import load_student_submission_markdown
from opengrader.core.workspace.models import (
    Answer,
    Assignment,
    AssignmentSummary,
    FeedbackJob,
    FolderSyncResult,
    GradeSaveResult,
    RegradeResult,
    RubricEditResult,
    PipelineOutput,
    PipelinePublication,
    Question,
    Submission,
    WorkspaceInfo,
)
from opengrader.server.assignment_pdf import (
    AssignmentPDFInput,
    PDF_RESPONSES,
    assignment_pdf_response,
)
from opengrader.server.assignment_qa import (
    AssignmentInput,
    QAPreview,
    VisualEditInput,
    VisualEditResult,
    parse_preview,
    preview_qa,
    edit_assignment_draft,
)
from opengrader.server.auth import add_auth_routes, signed_in
from opengrader.server.roster import add_roster_routes
from opengrader.server.students import add_student_routes
from opengrader.server.settings import ServerSettings
from opengrader.server.scan_store import ScanStore
from opengrader.server.scan_jobs import ScanJobs, ScanWorkers
from opengrader.server.feedback import FeedbackScheduler
from opengrader.server.scans import add_scan_routes
from opengrader.server.outline import add_outline_routes
from opengrader.server.recut import start_recut
from opengrader.server.web import add_web_routes
from opengrader.server.workspace.jobs import FeedbackJobs


class AssignmentUpdate(AssignmentInput):
    expected_qa_markdown: str


_NOT_IN_FOLDER_SESSION = "Create a workspace first to do this."
# (method or None for any change, path, message). A folder session grades
# one class folder whose files are the truth, so the browser hides these;
# the service refuses them too in case an old tab or script still asks.
_FOLDER_SESSION_REFUSALS = (
    (
        "POST",
        re.compile(r"/api/assignments(/from-template)?/?"),
        "This browser session grades one class folder, so it cannot add "
        "assignments. " + _NOT_IN_FOLDER_SESSION,
    ),
    (
        "DELETE",
        re.compile(r"/api/assignments/[^/]+/?"),
        "This browser session grades one class folder, so its assignment "
        "cannot be deleted here. " + _NOT_IN_FOLDER_SESSION,
    ),
    (
        "PUT",
        re.compile(r"/api/assignments/[^/]+/?"),
        "In a folder session the answer key is the file in the class folder, "
        "so only a question's rubric can be edited here. "
        + _NOT_IN_FOLDER_SESSION,
    ),
    (
        None,
        re.compile(r"/api/assignments/[^/]+/scans(/.*)?"),
        "Scans cannot be added to a folder session. " + _NOT_IN_FOLDER_SESSION,
    ),
    (
        None,
        re.compile(r"/api/assignments/[^/]+/(outline|template|versions)(/.*)?"),
        "In a folder session the assignment comes from the class folder, so "
        "its outline cannot be edited here. " + _NOT_IN_FOLDER_SESSION,
    ),
    # The roster is the class folder's own file: students are added, edited
    # and removed in it one at a time, never replaced from an upload.
    (
        "POST",
        re.compile(r"/api/roster/import(/preview)?"),
        "In a folder session the roster is the file in the class folder, so "
        "a roster is not imported here. " + _NOT_IN_FOLDER_SESSION,
    ),
)


class RubricEdit(BaseModel):
    """One question's rubric as the editor leaves it, and as it found it.

    `expected_rubric` is what the editor opened; the edit is refused if the
    question's rubric is no longer that, so two people (or the browser and
    the terminal app) cannot silently overwrite each other.
    """

    version: str | None = None
    rubric: list[QARubricItem]
    expected_rubric: list[QARubricItem]
    # The question's answers, saved with the rubric in the same write; left
    # out, they are not touched. For a multiple-choice question they are its
    # correct letters.
    answers: list[str] | None = None
    expected_answers: list[str] | None = None


class AnswerChoice(BaseModel):
    label: str
    text: str


class AnswerFieldInfo(BaseModel):
    """One question (or part) of the answer key as a place to put an answer."""

    question_id: str
    version: str | None = None
    # "1", "3a" — what the question is called on the page.
    label: str
    prompt: str
    points: int
    # Empty unless the question is multiple choice.
    choices: list[AnswerChoice]
    # The accepted answers; for multiple choice, the correct letters.
    answers: list[str]


class QuestionAnswers(BaseModel):
    question_id: str
    version: str | None = None
    answers: list[str]
    expected_answers: list[str]


class AnswersEdit(BaseModel):
    """Answers for several questions at once, each with what the editor opened."""

    questions: list[QuestionAnswers]


def folder_session_refusal(method: str, path: str) -> str | None:
    if method in ("GET", "HEAD", "OPTIONS"):
        return None
    for wanted, pattern, message in _FOLDER_SESSION_REFUSALS:
        if (wanted is None or wanted == method) and pattern.fullmatch(path):
            return message
    return None


class FolderSessionGuard:
    """Refuse workspace-only changes while the folder-session marker exists.

    Plain ASGI rather than `BaseHTTPMiddleware`, which would wrap the event
    stream's streaming response; everything else passes straight through.
    """

    def __init__(self, app, root: Path):  # noqa: ANN001 - ASGI app
        self.app = app
        self.root = root

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] == "http":
            message = folder_session_refusal(scope["method"], get_route_path(scope))
            if message is not None and load_folder_session(self.root) is not None:
                response = JSONResponse({"detail": message}, status_code=409)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(
    root: Path,
    *,
    web_dist: Path | None = None,
    process_jobs: bool = True,
    settings: ServerSettings | None = None,
    shared_session: bool = False,
    workers: ScanWorkers | None = None,
    feedback_scheduler: FeedbackScheduler | None = None,
    create: bool = True,
) -> FastAPI:
    root = root.resolve()
    from opengrader.core.workspace.store import DATABASE

    workspace = (
        Workspace(root) if not create or (root / DATABASE).exists() else Workspace.create(root)
    )
    jobs = FeedbackJobs(workspace)
    assignments_dir = root / ".opengrader/assignments"
    assignments_dir.mkdir(exist_ok=True)
    registry_lock = RLock()
    scan_store = ScanStore(assignments_dir)
    scan_store.recover()

    def publish(assignment_id: str, markdown: Path) -> int:
        document = load_student_submission_markdown(markdown)
        return publish_submission(
            workspace,
            assignment_id,
            document,
            asset_root=markdown.parent,
            source_key=markdown.parent.name,
        )

    def crop_inputs(assignment_id: str, version: str | None, student_id: str | None) -> CropInputs:
        document = parse_qa_markdown(workspace.assignment_source(assignment_id))
        if (version or "") not in outline_versions(document):
            raise ValueError("Choose the assignment version for this submission.")
        stored = workspace.outlines(assignment_id).get(version or "")
        if stored is None or not stored.regions.questions:
            raise ValueError("Draw the outline before creating submissions.")
        students = tuple(RosterStudent(student.sid, student.name) for student in roster.students(workspace))
        student = None
        if student_id is not None:
            student = next((item for item in students if item.student_id == student_id), None)
            if student is None:
                raise ValueError(f"No student on the roster has SID {student_id}.")
            if any(item.student_id == student_id for item in workspace.submissions(assignment_id)):
                raise ValueError(f"{student.name} already has a submission.")
        return CropInputs(
            regions=stored.regions,
            title=document.title or "Untitled assignment",
            version=version,
            roster=students,
            student=student,
        )

    def version_templates(assignment_id: str) -> dict[str, Path]:
        versions = outline_versions(parse_qa_markdown(workspace.assignment_source(assignment_id)))
        outlines = workspace.outlines(assignment_id)
        if not all(version in outlines and outlines[version].template for version in versions):
            return {}
        return {version: workspace.asset(outlines[version].template) for version in versions}

    scan_jobs = ScanJobs(
        scan_store,
        registry_lock,
        publish,
        crop_inputs,
        workspace.submission_for_source,
        version_templates,
        workers=workers,
    )

    def require_assignment(assignment_id: str) -> None:
        workspace.assignment_source(assignment_id)
        if assignment_id in scan_jobs.stopping:
            raise HTTPException(409, "This assignment is being deleted.")

    def extraction_source(assignment_id: str) -> None:
        directory = assignments_dir / assignment_id
        directory.mkdir(exist_ok=True)
        (directory / "submissions").mkdir(exist_ok=True)
        # A job input, regenerated from SQLite; never read back as grading state.
        (directory / "qa.md").write_text(
            workspace.assignment_source(assignment_id), encoding="utf-8"
        )

    for assignment in workspace.assignments():
        extraction_source(assignment.id)

    changed = asyncio.Event()

    def notify() -> None:
        nonlocal changed
        previous, changed = changed, asyncio.Event()
        previous.set()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        loop = asyncio.get_running_loop()
        workspace.on_commit = lambda: loop.call_soon_threadsafe(notify)
        for assignment_id in recutting_assignments(workspace):
            start_recut(workspace, scan_jobs, assignment_id)
        if process_jobs:
            if feedback_scheduler is not None:
                feedback_scheduler.add(jobs)
            else:
                jobs.start()
        try:
            yield
        finally:
            await run_in_threadpool(scan_jobs.close)
            if process_jobs and feedback_scheduler is not None:
                await run_in_threadpool(feedback_scheduler.remove, jobs)
            if process_jobs and feedback_scheduler is None:
                await run_in_threadpool(jobs.close)
            workspace.on_commit = None

    app = FastAPI(title="Opengrader workspace", lifespan=lifespan)
    app.add_middleware(FolderSessionGuard, root=root)
    api = APIRouter(prefix="/api")
    add_auth_routes(app, settings or ServerSettings(_env_file=None), workspace, shared_session=shared_session)

    def stop_scans() -> None:
        with registry_lock:
            futures = [future for assignment_id in list(scan_jobs.futures) for future in scan_jobs.stop_assignment(assignment_id)]
        scan_jobs.wait_for_stopped(tuple(futures))

    app.state.workspace = workspace
    app.state.scan_jobs = scan_jobs
    app.state.stop_scans = stop_scans
    app.state.shutdown_token = secrets.token_urlsafe(32)

    @app.exception_handler(WorkspaceConflict)
    async def conflict(_request: Request, exc: WorkspaceConflict):
        return JSONResponse(
            {
                "detail": str(exc),
                "current": exc.current.model_dump(mode="json") if exc.current else None,
            },
            status_code=409,
        )

    @app.exception_handler(RosterConflict)
    async def roster_conflict(_request: Request, exc: RosterConflict):
        return JSONResponse(
            {"detail": [{"loc": ["body", exc.field], "msg": str(exc), "type": "conflict"}]},
            status_code=409,
        )

    @app.exception_handler(WorkspaceNotFound)
    async def missing(_request: Request, exc: WorkspaceNotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(_request: Request, exc: ValueError):
        content: dict[str, object] = {"detail": str(exc)}
        if isinstance(exc, QAParseError):
            content["location"] = asdict(exc.location)
        return JSONResponse(content, status_code=422)

    @api.get("/workspace")
    def get_workspace() -> WorkspaceInfo:
        return workspace.info().model_copy(
            update={"folder_session": folder_session_info(workspace)}
        )

    def require_folder_session() -> str:
        session = active_folder_session(workspace)
        if session is None:
            raise HTTPException(409, "This workspace is not a folder session.")
        return session.assignment_id

    @api.post("/folder-session/sync")
    def sync_folder_session() -> FolderSyncResult:
        with folder_lock(root):
            assignment_id = require_folder_session()
            try:
                return sync_folder(workspace, assignment_id)
            except WriteBackError as exc:
                raise HTTPException(409, str(exc)) from exc

    @api.post("/folder-session/convert")
    def convert_folder_session() -> WorkspaceInfo:
        with folder_lock(root):
            assignment_id = require_folder_session()
            try:
                sync_folder(workspace, assignment_id)
            except WriteBackError:
                pass  # a moved folder: the grades are all in the workspace anyway
            end_folder_session(root)
            # Other open tabs reload the assignment and see the new mode.
            with workspace.connect(write=True) as db:
                record_event(db, assignment_id, kind="assignment")
        return get_workspace()

    @api.post("/shutdown", status_code=204, include_in_schema=False)
    def shutdown(request: Request) -> Response:
        expected = f"Bearer {app.state.shutdown_token}".encode()
        given = request.headers.get("authorization", "").encode()
        if not secrets.compare_digest(given, expected):
            raise HTTPException(404)
        callback = getattr(app.state, "shutdown", None)
        if callback is not None:
            callback()
        return Response(status_code=204)

    @api.get("/assignments")
    def assignments() -> list[Assignment]:
        return workspace.assignments()

    @api.get("/assignment-summaries")
    def assignment_summaries() -> list[AssignmentSummary]:
        return workspace.assignment_summaries()

    @api.post("/assignments/{assignment_id}/imports")
    def import_submissions(
        assignment_id: str, output: PipelineOutput
    ) -> PipelinePublication:
        session = load_folder_session(root)
        if session is not None and session.assignment_id == assignment_id:
            # Opening a class folder again after its answer key was edited
            # (a rubric changed in the terminal app, say): take the file's
            # key first, or the import below refuses it as a different one.
            with folder_lock(root):
                sync_answer_key(workspace, assignment_id)
        result = publish_pipeline(workspace, assignment_id, output)
        with registry_lock:
            extraction_source(result.assignment.id)
        return result

    @api.post("/drafts/preview")
    def preview(request: AssignmentInput) -> QAPreview:
        return preview_qa(request.qa_markdown)

    @api.post("/drafts/edit")
    def edit_preview(request: VisualEditInput) -> VisualEditResult:
        return edit_assignment_draft(request)

    @api.post("/drafts/pdf", response_class=Response, responses=PDF_RESPONSES)
    def preview_pdf(request: AssignmentPDFInput) -> Response:
        return assignment_pdf_response(preview(request).document, request.version)

    @api.post("/assignments", status_code=201)
    def create_assignment(request: AssignmentInput) -> Assignment:
        preview(request)
        with registry_lock:
            assignment = workspace.create_assignment(request.qa_markdown)
            extraction_source(assignment.id)
            return assignment

    @api.put("/assignments/{assignment_id}")
    def update_assignment(assignment_id: str, request: AssignmentUpdate) -> Assignment:
        preview(request)
        with registry_lock:
            require_assignment(assignment_id)
            if scan_store.list(assignment_id):
                raise WorkspaceConflict(
                    "Assignments with uploaded scans cannot be edited."
                )
            assignment = workspace.update_assignment(
                assignment_id, request.qa_markdown, request.expected_qa_markdown
            )
            extraction_source(assignment_id)
            return assignment

    @api.put("/assignments/{assignment_id}/questions/{question_id}/rubric")
    def save_rubric(
        assignment_id: str, question_id: str, edit: RubricEdit, request: Request
    ) -> RubricEditResult:
        """Edit one question's rubric while grading; a folder session only.

        There the answer key is a file, and the edit is written into it at
        once (`folder_session.edit_rubric`). A workspace's key has no file
        to keep in step, and its grades reach files only by write-back, which
        does not carry the answer key — so it keeps the rule that the key is
        edited before submissions arrive.
        """
        require_assignment(assignment_id)
        user = signed_in(request)
        with folder_lock(root):
            session = load_folder_session(root)
            if session is None or session.assignment_id != assignment_id:
                raise HTTPException(
                    409,
                    "A rubric can be edited while grading only when a class "
                    "folder is open from classic mode. In a workspace, edit "
                    "it on the Answer key page before submissions are added.",
                )
            result = edit_answer_key(
                workspace,
                assignment_id,
                [
                    KeyEdit(
                        question_id,
                        edit.version or None,
                        rubric=edit.rubric,
                        expected_rubric=edit.expected_rubric,
                        answers=edit.answers,
                        expected_answers=edit.expected_answers,
                    )
                ],
                actor=user.email if user else None,
            )
        if result.changed:
            with registry_lock:
                extraction_source(assignment_id)
        return result

    @api.get("/assignments/{assignment_id}/answer-fields")
    def answer_fields_of(assignment_id: str) -> list[AnswerFieldInfo]:
        """Every question (or part) of the answer key that takes an answer.

        A draft keeps a multiple-choice question's options as lines of its
        text; they are listed here as choices, which is what saving a correct
        one makes them.
        """
        from opengrader.core.qa_file.answers import answer_fields

        require_assignment(assignment_id)
        return [
            AnswerFieldInfo(
                question_id=field.key,
                version=field.version or None,
                label=field.label,
                prompt=field.prompt,
                points=field.points,
                choices=[AnswerChoice(label=label, text=text) for label, text in field.choices],
                answers=list(field.answers),
            )
            for field in answer_fields(workspace.assignment_source(assignment_id))
        ]

    @api.put("/assignments/{assignment_id}/answers")
    def save_answers(
        assignment_id: str, edit: AnswersEdit, request: Request
    ) -> RubricEditResult:
        """Add or change the answers of several questions; a folder session only.

        One write into the answer key file (`folder_session.edit_answer_key`),
        so either every answer here is saved or none is. No grade changes:
        what was graded stays graded until auto-grading is run again.
        """
        require_assignment(assignment_id)
        user = signed_in(request)
        with folder_lock(root):
            session = load_folder_session(root)
            if session is None or session.assignment_id != assignment_id:
                raise HTTPException(
                    409,
                    "Answers can be added here only when a class folder is "
                    "open from classic mode. In a workspace, edit them on the "
                    "Answer key page before submissions are added.",
                )
            result = edit_answer_key(
                workspace,
                assignment_id,
                [
                    KeyEdit(
                        question.question_id,
                        question.version or None,
                        answers=question.answers,
                        expected_answers=question.expected_answers,
                    )
                    for question in edit.questions
                ],
                actor=user.email if user else None,
            )
        if result.changed:
            with registry_lock:
                extraction_source(assignment_id)
        return result

    @api.post("/assignments/{assignment_id}/regrade")
    def regrade(assignment_id: str, request: Request) -> RegradeResult:
        """Grade the folder's work again with its answer key; a folder session only.

        The rule-based part of auto-grading, run on the files and then
        brought back here (`folder_session.regrade_folder`). No AI is used
        from the browser. A workspace has no files to grade in place.
        """
        require_assignment(assignment_id)
        user = signed_in(request)
        with folder_lock(root):
            session = load_folder_session(root)
            if session is None or session.assignment_id != assignment_id:
                raise HTTPException(
                    409,
                    "Work can be graded again from here only when a class "
                    "folder is open from classic mode.",
                )
            try:
                return regrade_folder(
                    workspace, assignment_id, actor=user.email if user else None
                )
            except WriteBackError as exc:
                raise HTTPException(409, str(exc)) from exc

    @api.delete("/assignments/{assignment_id}", status_code=204)
    async def delete_assignment(assignment_id: str) -> Response:
        with registry_lock:
            require_assignment(assignment_id)
            futures = scan_jobs.stop_assignment(assignment_id)
        try:
            await run_in_threadpool(scan_jobs.wait_for_stopped, futures)
            with registry_lock:
                workspace.delete_assignment(assignment_id)
            await run_in_threadpool(
                shutil.rmtree, assignments_dir / assignment_id, True
            )
        finally:
            with registry_lock:
                scan_jobs.stopping.discard(assignment_id)
        return Response(status_code=204)

    @api.get("/assignments/{assignment_id}/source")
    def source(assignment_id: str) -> AssignmentInput:
        require_assignment(assignment_id)
        return AssignmentInput(qa_markdown=workspace.assignment_source(assignment_id))

    @api.get("/assignments/{assignment_id}/qa")
    def qa(assignment_id: str) -> QAPreview:
        return parse_preview(workspace.assignment_source(assignment_id))

    @api.get("/assignments/{assignment_id}/pdf")
    def pdf(assignment_id: str, version: str | None = None) -> Response:
        document = preview_qa(workspace.assignment_source(assignment_id)).document
        return assignment_pdf_response(document, version)

    @api.get("/assignments/{assignment_id}/submissions")
    def submissions(assignment_id: str) -> list[Submission]:
        require_assignment(assignment_id)
        return workspace.submissions(assignment_id)

    @api.get("/assignments/{assignment_id}/statistics")
    def statistics(assignment_id: str) -> list[AssignmentVersionStatistics]:
        require_assignment(assignment_id)
        return workspace.statistics(assignment_id)

    @api.get("/assignments/{assignment_id}/submissions/{submission_id}/questions")
    def questions(assignment_id: str, submission_id: int) -> list[Question]:
        require_assignment(assignment_id)
        workspace.document(assignment_id, submission_id)
        return [
            answer.question
            for answer in workspace.answers(assignment_id, submission_id)
        ]

    @api.get("/assignments/{assignment_id}/submissions/{submission_id}/answers")
    def answers(assignment_id: str, submission_id: int) -> list[Answer]:
        return workspace.answers(assignment_id, submission_id)

    @api.get("/assignments/{assignment_id}/submissions/{submission_id}/document")
    def document(assignment_id: str, submission_id: int) -> Response:
        return Response(
            DOCUMENT.dump_json(workspace.document(assignment_id, submission_id)),
            media_type="application/json",
        )

    @api.get(
        "/assignments/{assignment_id}/submissions/{submission_id}/answers/{question_id}"
    )
    def answer(assignment_id: str, submission_id: int, question_id: str) -> Answer:
        return workspace.answer(assignment_id, submission_id, question_id)

    @api.put(
        "/assignments/{assignment_id}/submissions/{submission_id}/answers/{question_id}"
    )
    def save(
        assignment_id: str,
        submission_id: int,
        question_id: str,
        edit: GradeEdit,
        request: Request,
    ) -> GradeSaveResult:
        user = signed_in(request)
        actor = user.email if user else None
        if load_folder_session(root) is None:
            return workspace.save_grade(
                assignment_id, submission_id, question_id, edit, actor=actor
            )
        # Held across the commit and the file write so a sync cannot pull
        # this cell in between; checked again inside in case of a convert.
        with folder_lock(root):
            result = workspace.save_grade(
                assignment_id, submission_id, question_id, edit, actor=actor
            )
            session = load_folder_session(root)
            if session is None or session.assignment_id != assignment_id:
                return result
            return result.model_copy(
                update={
                    "folder": write_through(
                        workspace, assignment_id, submission_id, question_id
                    )
                }
            )

    @api.get("/assets/{name}")
    def asset(name: str) -> FileResponse:
        return FileResponse(workspace.asset(f"assets/{name}"))

    @api.get("/jobs")
    def job_status() -> list[FeedbackJob]:
        return workspace.jobs()

    def feedback_pdf(
        assignment_id: str, submission_id: int, current: list[FeedbackJob]
    ) -> Path:
        job = next(
            (
                job
                for job in current
                if job.assignment_id == assignment_id
                and job.submission_id == submission_id
            ),
            None,
        )
        if job is None or job.status != "ready" or not job.artifact:
            raise WorkspaceConflict(
                f"The feedback PDF for submission {submission_id} is not ready yet."
            )
        return workspace.root / ".opengrader" / job.artifact

    def feedback_filename(assignment_id: str, submission_id: int) -> str:
        return student_pdf_stem(
            workspace.document(assignment_id, submission_id), anonymous=False
        )

    @api.get("/assignments/{assignment_id}/submissions/{submission_id}/feedback")
    def feedback(assignment_id: str, submission_id: int) -> FileResponse:
        path = feedback_pdf(assignment_id, submission_id, workspace.jobs())
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=f"{feedback_filename(assignment_id, submission_id)}.pdf",
        )

    @api.get("/assignments/{assignment_id}/feedback")
    def feedback_archive(
        assignment_id: str, submission: list[int] = Query(min_length=1)
    ) -> FileResponse:
        require_assignment(assignment_id)
        current = workspace.jobs()
        title = next(
            item.title for item in workspace.assignments() if item.id == assignment_id
        )
        with NamedTemporaryFile(suffix=".zip", delete=False) as handle:
            archive_path = Path(handle.name)
        try:
            used: set[str] = set()
            with ZipFile(archive_path, "w", ZIP_STORED) as archive:
                for submission_id in dict.fromkeys(submission):
                    path = feedback_pdf(assignment_id, submission_id, current)
                    stem = unique_stem(
                        feedback_filename(assignment_id, submission_id), used
                    )
                    archive.write(path, f"{stem}.pdf")
        except BaseException:
            archive_path.unlink()
            raise
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename=f"{title or 'Assignment'} feedback.zip",
            background=BackgroundTask(archive_path.unlink),
        )

    @api.post("/jobs/{assignment_id}/{submission_id}/retry", status_code=204)
    def retry(assignment_id: str, submission_id: int) -> Response:
        workspace.document(assignment_id, submission_id)
        with workspace.connect(write=True) as db:
            queue_feedback(db, assignment_id, submission_id)
        return Response(status_code=204)

    @api.get("/events")
    async def events(request: Request, after: int = 0):
        try:
            cursor = int(request.headers.get("last-event-id", after))
        except ValueError:
            raise HTTPException(422, "Invalid event cursor.")

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                wakeup = changed
                batch = await run_in_threadpool(workspace.events, cursor)
                for event in batch:
                    cursor = event.cursor
                    yield f"id: {cursor}\ndata: {event.model_dump_json()}\n\n"
                if not batch:
                    yield ": connected\n\n"
                    try:
                        await asyncio.wait_for(wakeup.wait(), timeout=15)
                    except TimeoutError:
                        pass

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    add_scan_routes(
        api,
        scan_store,
        scan_jobs,
        registry_lock,
        require_assignment,
        lambda assignment_id, directories: workspace.delete_submissions(
            assignment_id, [p.name for p in directories]
        ),
    )
    add_outline_routes(
        api, workspace, scan_jobs, registry_lock, require_assignment, extraction_source
    )
    add_roster_routes(api, workspace)
    add_student_routes(api, workspace, scan_jobs, require_assignment)
    # Included last: the router copies its routes at inclusion time.
    app.include_router(api)
    add_web_routes(app, web_dist or Path(__file__).parents[2] / "web" / "dist")
    return app
