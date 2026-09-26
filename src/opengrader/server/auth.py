"""Google OpenID Connect routes and browser session identity."""

from __future__ import annotations

from typing import Mapping

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from opengrader.server.settings import ServerSettings


class AuthUser(BaseModel):
    id: str
    email: str
    name: str


class AuthStatus(BaseModel):
    enabled: bool
    user: AuthUser | None = None


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


class RemoteAuthGuard:
    def __init__(self, app: ASGIApp, *, enabled: bool = True) -> None:
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        connection = HTTPConnection(scope)
        local = (
            connection.client is not None
            and connection.client.host in {"127.0.0.1", "::1"}
            and connection.url.hostname in {"localhost", "127.0.0.1", "::1"}
            and "x-forwarded-for" not in connection.headers
            and "x-forwarded-proto" not in connection.headers
        )
        if (
            local
            or scope["path"].startswith("/auth/")
            or (self.enabled and connection.session.get("user"))
        ):
            await self.app(scope, receive, send)
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        elif not self.enabled:
            await JSONResponse(
                {"detail": "Remote access is disabled for this workspace."},
                status_code=403,
            )(scope, receive, send)
        elif scope["path"] == "/api" or scope["path"].startswith("/api/"):
            await JSONResponse(
                {"detail": "Sign in with Google to access this workspace."},
                status_code=401,
            )(scope, receive, send)
        else:
            await RedirectResponse("/auth/login", status_code=303)(scope, receive, send)


def add_auth_routes(app: FastAPI, settings: ServerSettings) -> None:
    if settings.auth_enabled:
        app.add_middleware(RemoteAuthGuard)
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.session_secret.get_secret_value(),
            session_cookie="opengrader_session",
            max_age=12 * 60 * 60,
            same_site="lax",
            https_only=settings.secure_cookie,
        )

    oauth = OAuth()
    if settings.auth_enabled:
        oauth.register(
            name="google",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret.get_secret_value(),
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )

    @app.get("/auth/me")
    def auth_status(request: Request) -> AuthStatus:
        user = request.session.get("user") if settings.auth_enabled else None
        return AuthStatus(enabled=settings.auth_enabled, user=user)

    @app.get("/auth/login", include_in_schema=False, response_model=None)
    async def login(request: Request) -> RedirectResponse:
        if not settings.auth_enabled:
            raise HTTPException(503, "Google sign-in is not configured.")
        redirect_uri = str(request.url_for("auth_callback"))
        return await oauth.google.authorize_redirect(  # type: ignore[union-attr]
            request, redirect_uri, hd=settings.google_domain
        )

    @app.get("/auth/callback", include_in_schema=False, response_model=None)
    async def auth_callback(request: Request) -> RedirectResponse:
        if not settings.auth_enabled:
            raise HTTPException(503, "Google sign-in is not configured.")
        try:
            token = await oauth.google.authorize_access_token(request)  # type: ignore[union-attr]
        except OAuthError as exc:
            raise HTTPException(401, "Google sign-in was not completed.") from exc
        claims = token.get("userinfo") or {}
        try:
            user = user_from_claims(claims, settings.google_domain)
        except ValueError as exc:
            request.session.clear()
            raise HTTPException(403, str(exc)) from exc
        request.session["user"] = user.model_dump()
        return RedirectResponse("/", status_code=303)

    @app.post("/auth/logout", status_code=204)
    def logout(request: Request) -> Response:
        if settings.auth_enabled:
            request.session.clear()
        return Response(status_code=204)
