"""Google OpenID Connect routes, browser session identity, and roles."""

from html import escape
import re
from threading import Lock
from typing import Annotated, Literal, Mapping, Protocol

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette._utils import get_route_path

from opengrader.core.workspace.store import Workspace
from opengrader.server.settings import ServerSettings, on_domain

Role = Literal["instructor", "grader"]


class AuthUser(BaseModel):
    id: str
    email: str
    name: str


class AuthStatus(BaseModel):
    enabled: bool
    user: AuthUser | None = None
    role: Role | None = None
    viewing_as_grader: bool = False


class ViewAs(BaseModel):
    role: Role


class Grader(BaseModel):
    email: str


class People(BaseModel):
    domain: str
    instructors: list[str]
    graders: list[str]


def user_from_claims(claims: Mapping[str, object], hosted_domain: str) -> AuthUser:
    if (
        str(claims.get("hd", "")).lower() != hosted_domain
        or claims.get("email_verified") is not True
        or not claims.get("sub")
        or not claims.get("email")
    ):
        raise ValueError(f"Sign in with a verified @{hosted_domain} account.")
    return AuthUser(
        id=str(claims["sub"]),
        email=str(claims["email"]).lower(),
        name=str(claims.get("name") or claims["email"]),
    )


def is_local(connection: HTTPConnection) -> bool:
    return (
        connection.client is not None
        and connection.client.host in {"127.0.0.1", "::1"}
        and connection.url.hostname in {"localhost", "127.0.0.1", "::1"}
        and "x-forwarded-for" not in connection.headers
        and "x-forwarded-proto" not in connection.headers
    )


def is_untrusted_browser_request(connection: HTTPConnection) -> bool:
    site = connection.headers.get("sec-fetch-site")
    if site == "same-origin":
        return False
    if (
        connection.scope["method"] in {"GET", "HEAD"}
        and connection.headers.get("sec-fetch-mode") == "navigate"
        and connection.headers.get("sec-fetch-dest") == "document"
    ):
        return False
    if site not in {None, "none"}:
        return True
    origin = connection.headers.get("origin")
    if origin is None:
        return False
    return origin != str(connection.url.replace(path="", query="", fragment=""))


def signed_in(connection: HTTPConnection) -> AuthUser | None:
    user = connection.scope.get("session", {}).get("user")
    return AuthUser.model_validate(user) if user else None


class Access:
    def __init__(self, workspace: Workspace, settings: ServerSettings) -> None:
        self._workspace = workspace
        self._domain = settings.google_domain
        self._lock = Lock()
        self.instructors = settings.instructors
        graders = frozenset(workspace.graders())
        for email in graders & self.instructors:
            workspace.remove_grader(email)
        self.graders = graders - self.instructors

    def role(self, email: str) -> Role | None:
        if email in self.instructors:
            return "instructor"
        return "grader" if email in self.graders else None

    def people(self) -> People:
        return People(
            domain=self._domain,
            instructors=sorted(self.instructors),
            graders=sorted(self.graders),
        )

    def add_grader(self, email: str) -> None:
        email = email.strip().lower()
        if not on_domain(email, self._domain):
            raise ValueError(f"Enter one @{self._domain} email address.")
        if email in self.instructors:
            raise ValueError(f"{email} is already an instructor.")
        with self._lock:
            self._workspace.add_grader(email)
            self.graders |= {email}

    def remove_grader(self, email: str) -> None:
        email = email.strip().lower()
        if email in self.instructors:
            raise ValueError(f"{email} is an instructor, not a grader.")
        with self._lock:
            self._workspace.remove_grader(email)
            self.graders -= {email}


class Roles(Protocol):
    def role(self, email: str) -> Role | None: ...


class RemoteAuthGuard:
    def __init__(self, app: ASGIApp, *, access: Roles | None = None) -> None:
        self.app = app
        self.access = access

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        connection = HTTPConnection(scope)
        if is_untrusted_browser_request(connection):
            await JSONResponse(
                {"detail": f"Open {connection.base_url} in a new browser tab, then try again."},
                status_code=403,
            )(scope, receive, send)
            return
        user = None
        if self.access is not None:
            user = signed_in(connection)
            if user is not None and await run_in_threadpool(self.access.role, user.email) is None:
                connection.session.clear()
                user = None
        if is_local(connection) or get_route_path(scope).startswith("/auth/") or user:
            await self.app(scope, receive, send)
        elif self.access is None:
            await JSONResponse(
                {"detail": "Remote access is disabled for this workspace."},
                status_code=403,
            )(scope, receive, send)
        elif get_route_path(scope) == "/api" or get_route_path(scope).startswith("/api/") or re.match(r"/courses/[^/]+/(api|auth)(/|$)", get_route_path(scope)):
            await JSONResponse(
                {"detail": "Sign in with Google to access this workspace."},
                status_code=401,
            )(scope, receive, send)
        else:
            await RedirectResponse("/auth/login", status_code=303)(scope, receive, send)


def refused(message: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='en'><title>Opengrader</title>"
        "<h1>You don't have access</h1>"
        f"<p>{escape(message)}</p>"
        "<p><a href='/auth/login?prompt=select_account'>"
        "Sign in with a different Google account</a></p></html>",
        status_code=403,
    )


def add_auth_routes(
    app: FastAPI, settings: ServerSettings, workspace: Workspace | None = None,
    *, access: Roles | None = None, shared_session: bool = False,
) -> None:
    if workspace is not None:
        access = Access(workspace, settings) if settings.auth_enabled else None
    app.state.access = access
    if access is not None:
        app.add_middleware(RemoteAuthGuard, access=access)
    if access is not None and not shared_session:
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.session_secret.get_secret_value(),
            session_cookie="opengrader_session",
            max_age=12 * 60 * 60,
            same_site="lax",
            https_only=settings.secure_cookie,
        )

    oauth = OAuth()
    if access is not None:
        oauth.register(
            name="google",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret.get_secret_value(),
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )

    def viewer(request: Request) -> AuthStatus:
        if access is None:
            return AuthStatus(enabled=False)
        user = signed_in(request)
        if user is not None:
            role = access.role(user.email)
        else:
            role = "instructor" if is_local(request) else None
        viewing = role == "instructor" and bool(request.session.get("view_as_grader"))
        return AuthStatus(
            enabled=True,
            user=user,
            role="grader" if viewing else role,
            viewing_as_grader=viewing,
        )

    Viewer = Annotated[AuthStatus, Depends(viewer)]

    @app.get("/auth/me")
    def auth_status(status: Viewer) -> AuthStatus:
        return status

    @app.post("/auth/view-as", status_code=204)
    def view_as(request: Request, choice: ViewAs, status: Viewer) -> Response:
        if status.role != "instructor" and not status.viewing_as_grader:
            raise HTTPException(403, "Only instructors can switch views.")
        if choice.role == "grader":
            request.session["view_as_grader"] = True
        else:
            request.session.pop("view_as_grader", None)
        return Response(status_code=204)

    @app.get("/auth/login", include_in_schema=False, response_model=None)
    async def login(request: Request) -> RedirectResponse:
        if shared_session:
            return RedirectResponse("/auth/login", status_code=303)
        if access is None:
            raise HTTPException(503, "Google sign-in is not configured.")
        redirect_uri = str(request.url_for("auth_callback"))
        options = {"hd": settings.google_domain}
        if request.query_params.get("prompt") == "select_account":
            options["prompt"] = "select_account"
        return await oauth.google.authorize_redirect(  # type: ignore[union-attr]
            request, redirect_uri, **options
        )

    @app.get("/auth/callback", include_in_schema=False, response_model=None)
    async def auth_callback(request: Request) -> Response:
        if access is None:
            raise HTTPException(503, "Google sign-in is not configured.")
        try:
            token = await oauth.google.authorize_access_token(request)  # type: ignore[union-attr]
        except OAuthError as exc:
            raise HTTPException(401, "Google sign-in was not completed.") from exc
        request.session.clear()
        try:
            user = user_from_claims(token.get("userinfo") or {}, settings.google_domain)
            if access.role(user.email) is None:
                raise ValueError(
                    f"{user.email} hasn't been added yet. "
                    "Ask an instructor to add you."
                )
        except ValueError as exc:
            return refused(str(exc))
        request.session["user"] = user.model_dump()
        return RedirectResponse("/" if workspace else "/courses", status_code=303)

    @app.post("/auth/logout", status_code=204)
    def logout(request: Request) -> Response:
        if access is not None:
            request.session.clear()
        return Response(status_code=204)

    def managed_access(status: Viewer) -> Access:
        if not isinstance(access, Access):
            raise HTTPException(404, "People are managed within a workspace when sign-in is on.")
        if status.role != "instructor":
            raise HTTPException(403, "Only instructors can manage people.")
        return access

    Managed = Annotated[Access, Depends(managed_access)]

    @app.get("/api/people")
    def people(managed: Managed) -> People:
        return managed.people()

    @app.post("/api/people/graders")
    def add_grader(grader: Grader, managed: Managed) -> People:
        managed.add_grader(grader.email)
        return managed.people()

    @app.post("/api/people/graders/remove")
    def remove_grader(grader: Grader, managed: Managed) -> People:
        managed.remove_grader(grader.email)
        return managed.people()
