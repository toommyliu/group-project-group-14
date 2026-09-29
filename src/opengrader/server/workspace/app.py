"""Workspace API shared by the terminal and browser clients."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
import re
import shutil
from threading import RLock

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from opengrader.core.grading.edits import GradeEdit
from opengrader.core.qa_file.errors import QAParseError
from opengrader.core.analytics.assignment_statistics import (
    AssignmentVersionStatistics,
)
from opengrader.core.workspace.store import (
    DOCUMENT,
    Workspace,
    WorkspaceConflict,
    WorkspaceNotFound,
    record_event,
)
from opengrader.core.workspace.folder_session import (
    active_folder_session,
    end_folder_session,
    folder_lock,
    folder_session_info,
    load_folder_session,
    sync_folder,
    write_through,
)
from opengrader.core.workspace.writeback import WriteBackError
from opengrader.core.workspace.intake import publish_pipeline, publish_submission
from opengrader.core.submissions import load_student_submission_markdown
from opengrader.core.workspace.models import (
    Answer,
    Assignment,
    AssignmentSummary,
    FeedbackJob,
    FolderSyncResult,
    GradeSaveResult,
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
    preview_qa,
    edit_assignment_draft,
)
from opengrader.server.auth import add_auth_routes
from opengrader.server.settings import ServerSettings
from opengrader.server.scan_store import ScanStore
from opengrader.server.scan_jobs import ScanJobs
from opengrader.server.scans import add_scan_routes
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
        re.compile(r"/api/assignments/?"),
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
        "so it cannot be edited here. " + _NOT_IN_FOLDER_SESSION,
    ),
    (
        None,
        re.compile(r"/api/assignments/[^/]+/scans(/.*)?"),
        "Scans cannot be added to a folder session. " + _NOT_IN_FOLDER_SESSION,
    ),
)


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
            message = folder_session_refusal(scope["method"], scope["path"])
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
) -> FastAPI:
    root = root.resolve()
    from opengrader.core.workspace.store import DATABASE

    workspace = (
        Workspace(root) if (root / DATABASE).exists() else Workspace.create(root)
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

    scan_jobs = ScanJobs(scan_store, registry_lock, publish)

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
        if process_jobs:
            jobs.start()
        try:
            yield
        finally:
            await run_in_threadpool(scan_jobs.close)
            if process_jobs:
                await run_in_threadpool(jobs.close)
            workspace.on_commit = None

    app = FastAPI(title="Opengrader workspace", lifespan=lifespan)
    app.add_middleware(FolderSessionGuard, root=root)
    api = APIRouter(prefix="/api")
    add_auth_routes(app, settings or ServerSettings(_env_file=None))

    app.state.workspace = workspace
    app.state.scan_jobs = scan_jobs

    @app.exception_handler(WorkspaceConflict)
    async def conflict(_request: Request, exc: WorkspaceConflict):
        return JSONResponse(
            {
                "detail": str(exc),
                "current": exc.current.model_dump(mode="json") if exc.current else None,
            },
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

    @api.post("/shutdown", status_code=204)
    def shutdown() -> Response:
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
        return preview_qa(workspace.assignment_source(assignment_id))

    @api.get("/assignments/{assignment_id}/pdf")
    def pdf(assignment_id: str, version: str | None = None) -> Response:
        return assignment_pdf_response(qa(assignment_id).document, version)

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
        assignment_id: str, submission_id: int, question_id: str, edit: GradeEdit
    ) -> GradeSaveResult:
        if load_folder_session(root) is None:
            return workspace.save_grade(assignment_id, submission_id, question_id, edit)
        # Held across the commit and the file write so a sync cannot pull
        # this cell in between; checked again inside in case of a convert.
        with folder_lock(root):
            result = workspace.save_grade(
                assignment_id, submission_id, question_id, edit
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

    @api.get("/assignments/{assignment_id}/submissions/{submission_id}/feedback")
    def feedback(assignment_id: str, submission_id: int) -> FileResponse:
        job = next(
            (
                job
                for job in workspace.jobs()
                if job.assignment_id == assignment_id
                and job.submission_id == submission_id
            ),
            None,
        )
        if job is None or job.status != "ready" or not job.artifact:
            raise WorkspaceConflict("The current feedback PDF is not ready yet.")
        path = workspace.root / ".opengrader" / job.artifact
        return FileResponse(
            path, media_type="application/pdf", filename=f"feedback-{submission_id}.pdf"
        )

    @api.post("/jobs/{assignment_id}/{submission_id}/retry", status_code=204)
    def retry(assignment_id: str, submission_id: int) -> Response:
        with workspace.connect(write=True) as db:
            db.execute(
                "UPDATE feedback_jobs SET status='pending', error='' WHERE assignment_id=? AND submission_id=? AND status='failed'",
                (assignment_id, submission_id),
            )
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
    # Included last: the router copies its routes at inclusion time.
    app.include_router(api)
    add_web_routes(app, web_dist or Path(__file__).parents[2] / "web" / "dist")
    return app
