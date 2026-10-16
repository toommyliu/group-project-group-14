"""One application server with explicitly registered grading workspaces."""

import asyncio
import json
import logging
import os
import re
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
from opengrader.server.peer import login_of, own_uid, peer_uid
from opengrader.server.transport import ACCOUNT_HEADER

# Where a workspace shared with other accounts tells them how to join.
CONNECTION_NOTE = Path(".opengrader/connection.json")
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# The one change a grader who joined someone else's workspace may make.
_GRADE_SAVE = re.compile(r"/api/assignments/[^/]+/submissions/\d+/answers/[^/]+/?")


def server_owner() -> str:
    from opengrader.core.folder_access import current_account

    return current_account()


class OtherAccountGuard:
    """Another account on this computer may reach this server only to join a
    workspace that was shared with it (checked per request in `Workspaces`).
    Until now any account could call it — list and create courses — because
    being on this computer was taken to mean being its owner."""

    def __init__(self, app) -> None:  # noqa: ANN001 - ASGI app
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] == "http" and not get_route_path(scope).startswith("/courses/"):
            uid = peer_uid(scope.get("client"), scope.get("server"))
            mine = own_uid()
            if uid is not None and mine is not None and uid != mine:
                await JSONResponse(
                    {"detail": f"This Opengrader belongs to {server_owner()}."},
                    status_code=403,
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)

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
        self.items: dict[str, RegisteredWorkspace] = {}
        # Per shared workspace: what a joining account must present.
        self.grader_tokens: dict[str, str] = {}
        # This server's own address, once it listens (set by `serve`).
        self.base_url: str | None = None
        self.owner = server_owner()
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
                    app.state.owner = self.owner
                    app.state.sharing_changed = lambda item_id=item.id: self.publish_note(item_id)
                    pending.callback(self.withdraw_note, item.id)
                    self.closers[item.id] = pending.pop_all()
                    self.apps[item.id] = app
                    self.items[item.id] = item
                    await run_in_threadpool(self.publish_note, item.id)
            app = self.apps[item.id]
            if app.state.workspace.root != Path(item.path):
                raise ValueError(
                    "This course moved while it was open. Restart the server to use its new location."
                )
            return app

    def publish_note(self, item_id: str) -> None:
        """Leave (or take away) the note that lets the sharing group join.

        It holds this server's address and a token only that group can read,
        so another account's `opengrader tui` can reach this workspace's
        service instead of starting its own. It is only good while this
        server runs; a stale one is detected by the joining side.
        """
        from opengrader.core.workspace.sharing import sharing_group

        item, app = self.items.get(item_id), self.apps.get(item_id)
        if item is None or app is None:
            return
        note = Path(item.path) / CONNECTION_NOTE
        try:
            group = sharing_group(app.state.workspace)
        except (OSError, sqlite3.Error):
            group = None
        if not group or not self.base_url:
            self.withdraw_note(item_id)
            return
        try:
            import grp

            gid = grp.getgrnam(group).gr_gid
        except (ImportError, KeyError):
            logger.warning("Cannot share %s: no group named %s", item.path, group)
            self.withdraw_note(item_id)
            return
        token = self.grader_tokens.setdefault(item_id, secrets.token_urlsafe(32))
        body = json.dumps(
            {
                "workspace_id": item_id,
                "url": self.base_url + item.url_path,
                "token": token,
                "owner": self.owner,
                "pid": os.getpid(),
            }
        )
        temporary = note.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(body)
        try:
            os.chown(temporary, -1, gid)
            os.chmod(temporary, 0o640)
        except OSError:
            logger.warning("Cannot give group %s the note in %s", group, item.path)
            temporary.unlink(missing_ok=True)
            return
        temporary.replace(note)

    def withdraw_note(self, item_id: str) -> None:
        self.grader_tokens.pop(item_id, None)
        item = self.items.get(item_id)
        if item is not None:
            with suppress(OSError):
                (Path(item.path) / CONNECTION_NOTE).unlink(missing_ok=True)

    async def open_shared(self) -> None:
        """Open every shared workspace when the server starts, so its note is
        there for the graders without the owner opening it first."""
        from opengrader.core.workspace.sharing import sharing_group

        for item in await run_in_threadpool(self.registry.list):
            try:
                shared = await run_in_threadpool(
                    lambda path=item.path: sharing_group(Workspace(Path(path)))
                )
                if shared:
                    await self.open(item)
            except (OSError, ValueError, RuntimeError, sqlite3.Error):
                logger.exception("Could not open shared workspace %s", item.path)

    def identify(self, scope, item: RegisteredWorkspace) -> tuple[str | None, Response | None]:  # noqa: ANN001
        """(account, refusal). The account is set only for someone who joined
        this workspace from another account; None means its owner."""
        request = Request(scope)
        token = self.grader_tokens.get(item.id)
        presented = request.headers.get("authorization", "")
        uid = peer_uid(scope.get("client"), scope.get("server"))
        mine = own_uid()
        if token and secrets.compare_digest(presented.encode(), f"Bearer {token}".encode()):
            declared = request.headers.get(ACCOUNT_HEADER, "").strip()
            verified = login_of(uid) if uid is not None else None
            if uid is not None and uid == mine:
                return None, None
            login = verified or declared
            if verified and declared and verified != declared:
                return None, JSONResponse(
                    {"detail": f"This connection belongs to {verified}, not {declared}."}, status_code=403
                )
            if not login or (verified is None and login == self.owner):
                return None, JSONResponse(
                    {"detail": "Say which account is joining (opengrader tui does)."}, status_code=401
                )
            return login, None
        if uid is not None and mine is not None and uid != mine:
            return None, JSONResponse(
                {
                    "detail": (
                        f"This workspace belongs to {self.owner}. To grade in it from "
                        f"your own account, {self.owner} shares it (opengrader share "
                        f"--group <group>) and you run opengrader tui {item.path}."
                    )
                },
                status_code=403,
            )
        return None, None

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
            account, refusal = await run_in_threadpool(self.identify, scope, item)
            if refusal is not None:
                await refusal(scope, receive, send)
                return
            if account is not None:
                from opengrader.core.workspace.sharing import viewer

                who = await run_in_threadpool(viewer, app.state.workspace, account, self.owner)
                if (
                    scope["method"] not in _SAFE_METHODS
                    and who.role != "instructor"
                    and not (scope["method"] == "PUT" and _GRADE_SAVE.fullmatch(get_route_path(scope)))
                ):
                    await JSONResponse(
                        {"detail": f"Only an instructor can do that in {self.owner}'s workspace; graders grade."},
                        status_code=403,
                    )(scope, receive, send)
                    return
                scope.setdefault("state", {})["account"] = account
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
        await workspaces.open_shared()
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
    app.add_middleware(OtherAccountGuard)

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
