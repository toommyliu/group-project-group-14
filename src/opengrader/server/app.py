"""One application server with explicitly registered grading workspaces."""

import asyncio
import logging
import secrets
import sqlite3
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from starlette._utils import get_route_path

from opengrader.core.workspace.models import CourseDetails, CourseText
from opengrader.core.workspace.store import Workspace
from opengrader.server.auth import RemoteAuthGuard, add_auth_routes, is_local, signed_in
from opengrader.server.feedback import FeedbackScheduler
from opengrader.server.registry import RegisteredWorkspace, WorkspaceRegistry
from opengrader.server.scan_jobs import ScanWorkers
from opengrader.server.settings import ServerSettings
from opengrader.server.web import add_web_routes
from opengrader.server.workspace.app import create_app as create_workspace_app
from opengrader.server.workspace.client import SERVICE_LOCK, exclusive_lock

logger = logging.getLogger(__name__)


class CourseForm(CourseDetails):
    model_config = ConfigDict(extra="forbid")
    name: CourseText


class RegisterWorkspace(BaseModel):
    path: str


class WorkspaceSummary(CourseDetails):
    id: str
    url: str
    last_opened: str | None
    available: bool


class ServerInfo(BaseModel):
    id: str


class ServerAccess:
    def __init__(self, registry: WorkspaceRegistry, settings: ServerSettings):
        self.registry = registry
        self.settings = settings

    def role(self, email: str):
        if email in self.settings.instructors:
            return "instructor"
        return (
            "grader"
            if any(self.can_open(item, email) for item in self.registry.list())
            else None
        )

    def can_open(self, item: RegisteredWorkspace, email: str) -> bool:
        if email in self.settings.instructors:
            return True
        try:
            workspace = Workspace(Path(item.path))
            return workspace.info().id == item.id and email in workspace.graders()
        except (OSError, ValueError, sqlite3.Error):
            return False


def _page_request(scope) -> bool:
    return scope["method"] == "GET" and get_route_path(scope).split("/", 2)[
        1
    ] not in {"api", "auth", "assets"}


class Workspaces:
    """Lazily open each workspace once and own its lifetime and write lock."""

    def __init__(self, registry, settings, *, web_dist, process_jobs):
        self.registry = registry
        self.settings = settings
        self.web_dist = web_dist
        self.process_jobs = process_jobs
        self.apps: dict[str, FastAPI] = {}
        self.lock = asyncio.Lock()
        self.stack = AsyncExitStack()
        self.workers = ScanWorkers()
        self.feedback = FeedbackScheduler()

    async def open(self, item: RegisteredWorkspace) -> FastAPI:
        async with self.lock:
            if item.id not in self.apps:
                # Keep partial initialization out of the cache and release its
                # lock on failure, so other workspaces can still be opened.
                async with AsyncExitStack() as pending:
                    root = Path(item.path)
                    if not (root / ".opengrader/workspace.sqlite3").is_file():
                        raise ValueError("Course folder is unavailable.")
                    pending.enter_context(exclusive_lock(root / SERVICE_LOCK))
                    app = await run_in_threadpool(
                        create_workspace_app,
                        root,
                        settings=self.settings,
                        web_dist=self.web_dist,
                        process_jobs=self.process_jobs,
                        shared_session=True,
                        workers=self.workers,
                        feedback_scheduler=self.feedback,
                        create=False,
                        course=item.course,
                    )
                    if app.state.workspace.info().id != item.id:
                        raise ValueError(
                            "The course in this folder has changed. Register its new location."
                        )
                    await pending.enter_async_context(app.router.lifespan_context(app))
                    self.stack.push_async_callback(pending.pop_all().aclose)
                    self.apps[item.id] = app
            app = self.apps[item.id]
            if app.state.workspace.root != Path(item.path):
                raise ValueError(
                    "This course moved while it was open. Restart the server to use its new location."
                )
            app.state.course = item.course
            return app

    async def resume_feedback(self) -> None:
        for item in await run_in_threadpool(self.registry.list):
            try:

                def pending(path=item.path):
                    workspace = Workspace(Path(path))
                    return any(
                        job.status in {"pending", "running"} for job in workspace.jobs()
                    )

                if await run_in_threadpool(pending):
                    await self.open(item)
            except (OSError, ValueError, RuntimeError, sqlite3.Error):
                logger.exception("Could not resume feedback in %s", item.path)

    async def close(self):
        # Workers may still publish results, so drain them before closing
        # workspace lifespans and releasing their locks.
        await run_in_threadpool(self.workers.close)
        if self.process_jobs:
            await run_in_threadpool(self.feedback.close)
        await self.stack.aclose()

    def refuse(self, scope, status: int, detail: str) -> Response:
        index = self.web_dist / "index.html"
        if _page_request(scope) and index.is_file():
            return FileResponse(
                index, status_code=status, headers={"Cache-Control": "no-cache"}
            )
        return JSONResponse({"detail": detail}, status_code=status)

    async def __call__(self, scope, receive, send):
        try:
            item = await run_in_threadpool(
                self.registry.get, scope["path_params"]["workspace_id"]
            )
            user = signed_in(Request(scope))
            if (
                self.settings.auth_enabled
                and not is_local(Request(scope))
                and (
                    user is None
                    or not await run_in_threadpool(
                        ServerAccess(self.registry, self.settings).can_open,
                        item,
                        user.email,
                    )
                )
            ):
                await self.refuse(
                    scope, 403, "You do not have access to this course."
                )(scope, receive, send)
                return
            app = await self.open(item)
            if _page_request(scope):
                await run_in_threadpool(self.registry.opened, item.id)
        except LookupError as exc:
            await self.refuse(scope, 404, str(exc))(scope, receive, send)
            return
        except (OSError, ValueError, RuntimeError, sqlite3.Error):
            logger.exception("Could not open registered workspace")
            await self.refuse(
                scope,
                409,
                "This course could not be opened. Its folder may be unavailable or another server may be using it.",
            )(scope, receive, send)
            return
        await app(scope, receive, send)


def create_app(
    home: Path | None = None,
    *,
    settings: ServerSettings | None = None,
    web_dist: Path | None = None,
    process_jobs: bool = True,
) -> FastAPI:
    registry = WorkspaceRegistry(home)
    settings = settings or ServerSettings(_env_file=registry.home / "server.env")
    access = ServerAccess(registry, settings)
    web_dist = web_dist or Path(__file__).parents[1] / "web/dist"
    workspaces = Workspaces(
        registry, settings, web_dist=web_dist, process_jobs=process_jobs
    )

    @asynccontextmanager
    async def lifespan(app):
        recovery = None
        if process_jobs:
            workspaces.feedback.start()
            recovery = asyncio.create_task(workspaces.resume_feedback())
        try:
            yield
        finally:
            if recovery is not None:
                await recovery
            await workspaces.close()

    app = FastAPI(title="Opengrader", lifespan=lifespan)
    app.state.registry = registry
    app.state.workspaces = workspaces
    app.state.shutdown_token = secrets.token_urlsafe(32)
    add_auth_routes(app, settings, access=access if settings.auth_enabled else None)
    if not settings.auth_enabled:
        app.add_middleware(RemoteAuthGuard)

    # Explicitly guard sensitive endpoints with the server's private token.
    def control(request: Request):
        expected = f"Bearer {app.state.shutdown_token}".encode()
        if not secrets.compare_digest(
            request.headers.get("authorization", "").encode(), expected
        ):
            raise HTTPException(404)

    def instructor(request: Request):
        if request.scope.get("session", {}).get("view_as_grader"):
            raise HTTPException(403, "Only instructors can manage courses.")
        user = signed_in(request)
        if settings.auth_enabled and not (
            user
            and user.email in settings.instructors
            or user is None
            and is_local(request)
        ):
            raise HTTPException(403, "Only instructors can manage courses.")

    def summary(item: RegisteredWorkspace):
        return WorkspaceSummary(
            **item.course.model_dump(),
            id=item.id,
            url=item.url_path + "/",
            last_opened=item.last_opened,
            available=(Path(item.path) / ".opengrader/workspace.sqlite3").is_file(),
        )

    @app.get("/api/server")
    def info() -> ServerInfo:
        return ServerInfo(id=registry.id)

    @app.get("/api/workspaces")
    def list_workspaces(request: Request) -> list[WorkspaceSummary]:
        user = signed_in(request)
        return [
            summary(item)
            for item in registry.list()
            if (user is None and is_local(request))
            or (user is not None and access.can_open(item, user.email))
        ]

    @app.post("/api/workspaces", status_code=201)
    def create_workspace(body: CourseForm, request: Request) -> WorkspaceSummary:
        instructor(request)
        try:
            return summary(registry.create(body))
        except (ValueError, FileExistsError) as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            logger.exception("Could not create workspace")
            raise HTTPException(
                503,
                "The course could not be created. Check that the server's storage is available and writable.",
            ) from exc

    @app.put("/api/workspaces/{workspace_id}")
    def update_workspace(
        workspace_id: str, body: CourseForm, request: Request
    ) -> WorkspaceSummary:
        instructor(request)
        try:
            return summary(registry.update(workspace_id, body))
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.delete("/api/workspaces/{workspace_id}", status_code=204)
    def remove_workspace(workspace_id: str, request: Request) -> Response:
        instructor(request)
        try:
            registry.remove(workspace_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return Response(status_code=204)

    @app.post("/api/server/workspaces/register", include_in_schema=False)
    def register_workspace(
        body: RegisterWorkspace, request: Request
    ) -> RegisteredWorkspace:
        control(request)
        try:
            item = registry.register(Path(body.path))
            registry.opened(item.id)
            return item
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/shutdown", status_code=204, include_in_schema=False)
    def shutdown(request: Request) -> Response:
        control(request)
        if callback := getattr(app.state, "shutdown", None):
            callback()
        return Response(status_code=204)

    app.mount("/courses/{workspace_id}", workspaces)
    add_web_routes(app, web_dist)
    return app
