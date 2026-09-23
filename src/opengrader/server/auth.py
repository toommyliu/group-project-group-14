"""Google OpenID Connect routes and browser session identity."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware


@dataclass(frozen=True)
class AuthSettings:
    client_id: str = ""
    client_secret: str = ""
    session_secret: str = ""
    hosted_domain: str = "sjsu.edu"
    secure_cookie: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AuthSettings":
        values = os.environ if environ is None else environ
        settings = cls(
            client_id=values.get("OPENGRADER_GOOGLE_CLIENT_ID", "").strip(),
            client_secret=values.get("OPENGRADER_GOOGLE_CLIENT_SECRET", "").strip(),
            session_secret=values.get("OPENGRADER_SESSION_SECRET", "").strip(),
            hosted_domain=values.get("OPENGRADER_GOOGLE_DOMAIN", "sjsu.edu").strip().lower(),
            secure_cookie=values.get("OPENGRADER_SECURE_COOKIE", "").strip().lower()
            in {"1", "true", "yes"},
        )
        configured = (settings.client_id, settings.client_secret, settings.session_secret)
        if any(configured) and not all(configured):
            raise ValueError(
                "Google sign-in requires OPENGRADER_GOOGLE_CLIENT_ID, "
                "OPENGRADER_GOOGLE_CLIENT_SECRET, and OPENGRADER_SESSION_SECRET."
            )
        if settings.enabled and not settings.hosted_domain:
            raise ValueError("OPENGRADER_GOOGLE_DOMAIN cannot be empty.")
        return settings

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret and self.session_secret)


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


def add_auth_routes(app: FastAPI, settings: AuthSettings) -> None:
    if settings.enabled:
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.session_secret,
            session_cookie="opengrader_session",
            max_age=12 * 60 * 60,
            same_site="lax",
            https_only=settings.secure_cookie,
        )

    oauth = OAuth()
    if settings.enabled:
        oauth.register(
            name="google",
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )

    @app.get("/auth/me", include_in_schema=False)
    def auth_status(request: Request) -> AuthStatus:
        user = request.session.get("user") if settings.enabled else None
        return AuthStatus(enabled=settings.enabled, user=user)

    @app.get("/auth/login", include_in_schema=False, response_model=None)
    async def login(request: Request) -> RedirectResponse:
        if not settings.enabled:
            raise HTTPException(503, "Google sign-in is not configured.")
        redirect_uri = str(request.url_for("auth_callback"))
        return await oauth.google.authorize_redirect(  # type: ignore[union-attr]
            request, redirect_uri, hd=settings.hosted_domain
        )

    @app.get("/auth/callback", include_in_schema=False, response_model=None)
    async def auth_callback(request: Request) -> RedirectResponse:
        if not settings.enabled:
            raise HTTPException(503, "Google sign-in is not configured.")
        try:
            token = await oauth.google.authorize_access_token(request)  # type: ignore[union-attr]
        except OAuthError as exc:
            raise HTTPException(401, "Google sign-in was not completed.") from exc
        claims = token.get("userinfo") or {}
        try:
            user = user_from_claims(claims, settings.hosted_domain)
        except ValueError as exc:
            request.session.clear()
            raise HTTPException(403, str(exc)) from exc
        request.session["user"] = user.model_dump()
        return RedirectResponse("/", status_code=303)

    @app.post("/auth/logout", include_in_schema=False, response_model=None)
    def logout(request: Request) -> RedirectResponse:
        if settings.enabled:
            request.session.clear()
        return RedirectResponse("/", status_code=303)
