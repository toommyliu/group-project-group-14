"""One application server with explicitly registered grading workspaces."""

import asyncio
import logging
import secrets
import shutil
import sqlite3
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from starlette._utils import get_route_path

from opengrader.core.workspace.models import CourseDetails, CourseText, StaffRole
from opengrader.core.workspace.store import DATABASE, Workspace
from opengrader.server.auth import (
    RemoteAuthGuard,
    add_auth_routes,
    course_role,
    is_local,
    signed_in,
)
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


class DeleteCourse(BaseModel):
    number: str


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
        self.admins = settings.admins

    def role(self, email: str) -> StaffRole | None:
        return "instructor" if email in self.admins else None

    def admits(self, email: str) -> bool:
        return email in self.admins or any(
            self.course_role(item, email) for item in self.registry.list()
        )

    def course_role(self, item: RegisteredWorkspace, email: str) -> StaffRole | None:
        try:
            workspace = Workspace(Path(item.path))
            if workspace.info().id != item.id:
                return None
            return course_role(self.admins, workspace, email)
        except (OSError, ValueError, sqlite3.Error):
            return None


def _set_aside(root: Path, workspace_id: str) -> Path:
    if not (root / DATABASE).is_file():
        raise ValueError("This course's folder is unavailable. Remove the course instead.")
    with exclusive_lock(root / SERVICE_LOCK):
        if Workspace(root).info().id != workspace_id:
            raise ValueError("The course in this folder has changed. Register its new location.")
        trash = root / f".opengrader-deleted-{uuid4().hex}"
        (root / ".opengrader").rename(trash)
    return trash


def _discard(trash: Path) -> None:
    try:
        shutil.rmtree(trash)
    except OSError:
        logger.exception("Could not finish deleting %s", trash)
        return
    with suppress(OSError):
        trash.parent.rmdir()


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
        self.closers: dict[str, AsyncExitStack] = {}
        self.deleting: set[str] = set()
        self.lock = asyncio.Lock()
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
                    )
                    if app.state.workspace.info().id != item.id:
                        raise ValueError(
                            "The course in this folder has changed. Register its new location."
                        )
                    await pending.enter_async_context(app.router.lifespan_context(app))
                    self.closers[item.id] = pending.pop_all()
                    self.apps[item.id] = app
            app = self.apps[item.id]
            if app.state.workspace.root != Path(item.path):
                raise ValueError(
                    "This course moved while it was open. Restart the server to use its new location."
                )
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
        for closer in reversed(list(self.closers.values())):
            await closer.aclose()

    async def delete(self, item: RegisteredWorkspace) -> None:
        self.deleting.add(item.id)
        try:
            async with self.lock:
                if app := self.apps.pop(item.id, None):
                    await run_in_threadpool(app.state.stop_scans)
                    await self.closers.pop(item.id).aclose()
                trash = await run_in_threadpool(_set_aside, Path(item.path), item.id)
                await run_in_threadpool(self.registry.remove, item.id)
            await run_in_threadpool(_discard, trash)
        finally:
            self.deleting.discard(item.id)

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
            if item.id in self.deleting:
                await self.refuse(scope, 409, "This course is being deleted.")(scope, receive, send)
                return
            user = signed_in(Request(scope))
            if (
                self.settings.auth_enabled
                and not is_local(Request(scope))
                and (
                    user is None
                    or not await run_in_threadpool(
                        ServerAccess(self.registry, self.settings).course_role,
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

    def owner(request: Request) -> bool:
        return not settings.auth_enabled or (
            signed_in(request) is None and is_local(request)
        )

    def admin(request: Request) -> None:
        user = signed_in(request)
        if not (owner(request) or user and user.email in settings.admins):
            raise HTTPException(403, "Only admins can add or remove courses.")

    def course_instructor(request: Request, workspace_id: str) -> None:
        try:
            item = registry.get(workspace_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        user = signed_in(request)
        if not (
            owner(request)
            or user and access.course_role(item, user.email) == "instructor"
        ):
            raise HTTPException(403, "Only this course's instructors can edit it.")

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
            or (user is not None and access.course_role(item, user.email))
        ]

    @app.post("/api/workspaces", status_code=201)
    def create_workspace(body: CourseForm, request: Request) -> WorkspaceSummary:
        admin(request)
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
        course_instructor(request, workspace_id)
        try:
            return summary(registry.update(workspace_id, body))
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ValueError, OSError, sqlite3.Error) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.delete("/api/workspaces/{workspace_id}", status_code=204)
    def remove_workspace(workspace_id: str, request: Request) -> Response:
        admin(request)
        try:
            registry.remove(workspace_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return Response(status_code=204)

    @app.post("/api/workspaces/{workspace_id}/delete", status_code=204)
    async def delete_workspace(workspace_id: str, body: DeleteCourse, request: Request) -> Response:
        admin(request)
        try:
            item = await run_in_threadpool(registry.get, workspace_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        if body.number.strip() != item.number:
            raise HTTPException(422, f"Type {item.number} to delete this course.")
        try:
            await workspaces.delete(item)
        except BlockingIOError as exc:
            raise HTTPException(
                409, "Another Opengrader server is using this course. Close it there, then try again."
            ) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            logger.exception("Could not delete course %s", workspace_id)
            raise HTTPException(
                503, "The course could not be deleted. Check that its folder is available and writable."
            ) from exc
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
