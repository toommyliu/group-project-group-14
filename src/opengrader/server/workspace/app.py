"""Workspace API shared by the terminal and browser clients."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import shutil
from threading import RLock

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from opengrader.core.grading.edits import GradeEdit
from opengrader.core.workspace.store import (
    DOCUMENT,
    Workspace,
    WorkspaceConflict,
    WorkspaceNotFound,
)
from opengrader.core.workspace.intake import publish_pipeline, publish_submission
from opengrader.core.submissions import load_student_submission_markdown
from opengrader.core.workspace.models import (
    Answer,
    Assignment,
    FeedbackJob,
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
from opengrader.server.scan_store import ScanStore
from opengrader.server.scan_jobs import ScanJobs
from opengrader.server.scans import add_scan_routes
from opengrader.server.terminals.manager import TerminalManager
from opengrader.server.terminals.routes import add_terminal_routes
from opengrader.server.web import add_web_routes
from opengrader.server.workspace.jobs import FeedbackJobs


class AssignmentUpdate(AssignmentInput):
    expected_qa_markdown: str


def create_app(
    root: Path, *, web_dist: Path | None = None, process_jobs: bool = True
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
    terminals = TerminalManager(root / ".opengrader")

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
        terminals.start_maintenance()
        if process_jobs:
            jobs.start()
        try:
            yield
        finally:
            await terminals.close()
            await run_in_threadpool(scan_jobs.close)
            if process_jobs:
                await run_in_threadpool(jobs.close)
            workspace.on_commit = None

    app = FastAPI(title="Opengrader workspace", lifespan=lifespan)

    app.state.workspace = workspace
    app.state.scan_jobs = scan_jobs
    app.state.terminals = terminals

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
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/api/workspace")
    def get_workspace() -> WorkspaceInfo:
        return workspace.info()

    @app.post("/api/shutdown", status_code=204)
    def shutdown() -> Response:
        callback = getattr(app.state, "shutdown", None)
        if callback is not None:
            callback()
        return Response(status_code=204)

    @app.get("/assignments")
    def assignments() -> list[Assignment]:
        return workspace.assignments()

    @app.post("/api/pipeline-results")
    def pipeline_results(output: PipelineOutput) -> PipelinePublication:
        result = publish_pipeline(workspace, output)
        with registry_lock:
            extraction_source(result.assignment.id)
        return result

    @app.post("/assignments/preview")
    def preview(request: AssignmentInput) -> QAPreview:
        return preview_qa(request.qa_markdown)

    @app.post("/assignments/preview/edit")
    def edit_preview(request: VisualEditInput) -> VisualEditResult:
        return edit_assignment_draft(request)

    @app.post(
        "/assignments/preview/pdf", response_class=Response, responses=PDF_RESPONSES
    )
    def preview_pdf(request: AssignmentPDFInput) -> Response:
        return assignment_pdf_response(preview(request).document, request.version)

    @app.post("/assignments", status_code=201)
    def create_assignment(request: AssignmentInput) -> Assignment:
        preview(request)
        with registry_lock:
            assignment = workspace.create_assignment(request.qa_markdown)
            extraction_source(assignment.id)
            return assignment

    @app.put("/assignments/{assignment_id}")
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

    @app.delete("/assignments/{assignment_id}", status_code=204)
    async def delete_assignment(
        assignment_id: str, stop_terminal_sessions: bool = False
    ) -> Response:
        async with terminals.lock:
            running = [
                s
                for s in terminals.sessions.values()
                if s.record.assignment_id == assignment_id and s.process is not None
            ]
            if running and not stop_terminal_sessions:
                raise HTTPException(
                    409,
                    "Confirm that this assignment's terminal sessions may be stopped before deleting it.",
                )
            for session in running:
                await terminals._stop(
                    session, "Stopped when assignment deletion was confirmed."
                )
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

    @app.get("/assignments/{assignment_id}/source")
    def source(assignment_id: str) -> AssignmentInput:
        require_assignment(assignment_id)
        return AssignmentInput(qa_markdown=workspace.assignment_source(assignment_id))

    @app.get("/assignments/{assignment_id}/qa")
    def qa(assignment_id: str) -> QAPreview:
        return preview_qa(workspace.assignment_source(assignment_id))

    @app.get("/assignments/{assignment_id}/pdf")
    def pdf(assignment_id: str, version: str | None = None) -> Response:
        return assignment_pdf_response(qa(assignment_id).document, version)

    @app.get("/assignments/{assignment_id}/submissions")
    def submissions(assignment_id: str) -> list[Submission]:
        require_assignment(assignment_id)
        return workspace.submissions(assignment_id)

    @app.get("/assignments/{assignment_id}/questions")
    def questions(assignment_id: str, submission_id: int = 1) -> list[Question]:
        require_assignment(assignment_id)
        workspace.document(assignment_id, submission_id)
        return [
            answer.question
            for answer in workspace.answers(assignment_id, submission_id)
        ]

    @app.get("/assignments/{assignment_id}/submissions/{submission_id}/answers")
    def answers(assignment_id: str, submission_id: int) -> list[Answer]:
        return workspace.answers(assignment_id, submission_id)

    @app.get("/assignments/{assignment_id}/submissions/{submission_id}/document")
    def document(assignment_id: str, submission_id: int) -> Response:
        return Response(
            DOCUMENT.dump_json(workspace.document(assignment_id, submission_id)),
            media_type="application/json",
        )

    @app.get(
        "/assignments/{assignment_id}/submissions/{submission_id}/answers/{question_id}"
    )
    def answer(assignment_id: str, submission_id: int, question_id: str) -> Answer:
        return workspace.answer(assignment_id, submission_id, question_id)

    @app.put(
        "/assignments/{assignment_id}/submissions/{submission_id}/answers/{question_id}"
    )
    def save(
        assignment_id: str, submission_id: int, question_id: str, edit: GradeEdit
    ) -> GradeSaveResult:
        return workspace.save_grade(assignment_id, submission_id, question_id, edit)

    @app.get("/api/assets/{name}")
    def asset(name: str) -> FileResponse:
        return FileResponse(workspace.asset(f"assets/{name}"))

    @app.get("/api/jobs")
    def job_status() -> list[FeedbackJob]:
        return workspace.jobs()

    @app.get("/assignments/{assignment_id}/submissions/{submission_id}/feedback")
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

    @app.post("/api/jobs/{assignment_id}/{submission_id}/retry", status_code=204)
    def retry(assignment_id: str, submission_id: int) -> Response:
        with workspace.connect(write=True) as db:
            db.execute(
                "UPDATE feedback_jobs SET status='pending', error='' WHERE assignment_id=? AND submission_id=? AND status='failed'",
                (assignment_id, submission_id),
            )
        return Response(status_code=204)

    @app.get("/api/events")
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
        app,
        scan_store,
        scan_jobs,
        registry_lock,
        require_assignment,
        lambda assignment_id, directories: workspace.delete_submissions(
            assignment_id, [p.name for p in directories]
        ),
    )
    add_terminal_routes(app, terminals, require_assignment)
    add_web_routes(app, web_dist or Path(__file__).parents[2] / "web" / "dist")
    return app
