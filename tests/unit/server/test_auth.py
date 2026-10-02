import json
from base64 import b64encode

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from opengrader.server.auth import RemoteAuthGuard, user_from_claims
from opengrader.server.settings import ServerSettings
from opengrader.server.workspace.app import create_app


def test_auth_is_optional_for_offline_use(tmp_path):
    with TestClient(
        create_app(tmp_path, settings=ServerSettings(_env_file=None), process_jobs=False)
    ) as client:
        assert client.get("/auth/me").json() == {
            "enabled": False,
            "user": None,
            "role": None,
            "viewing_as_grader": False,
        }
        response = client.get("/auth/login")
        assert response.status_code == 503


@pytest.mark.parametrize("mode, enabled", [(None, False), ("false", False), ("true", True)])
def test_saved_credentials_only_enable_sign_in_when_requested(tmp_path, monkeypatch, mode, enabled):
    if mode is None:
        monkeypatch.delenv("OPENGRADER_AUTH_ENABLED", raising=False)
    else:
        monkeypatch.setenv("OPENGRADER_AUTH_ENABLED", mode)
    settings = ServerSettings(
        _env_file=None,
        google_client_id="client-id",
        google_client_secret="client-secret",
        session_secret="session-secret",
        instructors="prof@sjsu.edu",
    )
    with TestClient(create_app(tmp_path, settings=settings, process_jobs=False)) as client:
        assert client.get("/auth/me").json() == {
            "enabled": enabled,
            "user": None,
            "role": None,
            "viewing_as_grader": False,
        }


def test_local_mode_refuses_remote_access(tmp_path):
    app = create_app(tmp_path, settings=ServerSettings(_env_file=None), process_jobs=False)
    app.add_middleware(RemoteAuthGuard)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/assignments").status_code == 200
        response = client.get("/api/assignments", headers={"X-Forwarded-Proto": "https"})
        assert response.status_code == 403
        assert response.json() == {"detail": "Remote access is disabled for this workspace."}


def test_session_reports_user_and_logout_clears_it(tmp_path):
    settings = ServerSettings(
        _env_file=None,
        auth_enabled=True,
        google_client_id="client-id",
        google_client_secret="client-secret",
        session_secret="test-session-secret",
        instructors="grader@sjsu.edu",
    )
    session = {
        "user": {
            "id": "google-subject",
            "email": "grader@sjsu.edu",
            "name": "Test Grader",
        }
    }
    cookie = TimestampSigner(settings.session_secret.get_secret_value()).sign(
        b64encode(json.dumps(session).encode("utf-8"))
    ).decode("utf-8")

    with TestClient(
        create_app(tmp_path, settings=settings, process_jobs=False)
    ) as client:
        client.cookies.set("opengrader_session", cookie, domain="testserver.local")
        assert client.get("/auth/me").json() == {
            "enabled": True,
            "user": {
                "id": "google-subject",
                "email": "grader@sjsu.edu",
                "name": "Test Grader",
            },
            "role": "instructor",
            "viewing_as_grader": False,
        }
        assert client.get("/api/assignments").status_code == 200
        response = client.post("/auth/logout")
        assert response.status_code == 204
        assert 'opengrader_session=null' in response.headers["set-cookie"]
        assert client.get("/api/assignments").status_code == 401


def test_remote_requests_require_sign_in(tmp_path):
    settings = ServerSettings(
        _env_file=None,
        auth_enabled=True,
        google_client_id="client-id",
        google_client_secret="client-secret",
        session_secret="test-session-secret",
        instructors="prof@sjsu.edu",
    )
    with TestClient(
        create_app(tmp_path, settings=settings, process_jobs=False)
    ) as client:
        response = client.get("/api/assignments")
        assert response.status_code == 401
        assert response.json() == {
            "detail": "Sign in with Google to access this workspace."
        }
        response = client.get("/assignments", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/auth/login"
        assert client.get("/auth/me").json() == {
            "enabled": True,
            "user": None,
            "role": None,
            "viewing_as_grader": False,
        }


def test_local_cli_access_does_not_allow_unsigned_proxied_requests(tmp_path):
    settings = ServerSettings(
        _env_file=None,
        auth_enabled=True,
        google_client_id="client-id",
        google_client_secret="client-secret",
        session_secret="test-session-secret",
        instructors="prof@sjsu.edu",
    )
    with TestClient(
        create_app(tmp_path, settings=settings, process_jobs=False),
        base_url="http://localhost:8000",
        client=("127.0.0.1", 50000),
    ) as client:
        assert client.get("/api/assignments").status_code == 200
        for headers in (
            {"Host": "grader.example.com"},
            {"X-Forwarded-Proto": "https"},
            {"X-Forwarded-For": "127.0.0.1"},
        ):
            assert client.get("/api/assignments", headers=headers).status_code == 401


def test_google_claims_must_be_verified_and_from_sjsu():
    claims = {
        "sub": "google-subject",
        "email": "Grader@SJSU.edu",
        "email_verified": True,
        "hd": "sjsu.edu",
        "name": "Test Grader",
    }
    assert user_from_claims(claims, "sjsu.edu").email == "grader@sjsu.edu"

    for invalid in (
        {**claims, "hd": "gmail.com"},
        {**claims, "email_verified": False},
        {**claims, "sub": ""},
    ):
        try:
            user_from_claims(invalid, "sjsu.edu")
        except ValueError:
            pass
        else:
            raise AssertionError("invalid Google claims should be rejected")


@pytest.mark.parametrize("error, expected", [("access_denied", "access_denied"), ("private-student-answer", "oauth_error")])
def test_oauth_failure_logs_only_known_error_codes(tmp_path, error, expected):
    from structlog.testing import capture_logs

    settings = ServerSettings(
        _env_file=None, auth_enabled=True, google_client_id="client-id",
        google_client_secret="client-secret", session_secret="session-secret",
    )
    with TestClient(create_app(tmp_path, settings=settings, process_jobs=False)) as client:
        with capture_logs() as events:
            response = client.get("/auth/callback", params={"error": error})
    assert response.status_code == 401
    assert response.json() == {"detail": "Google sign-in was not completed."}
    assert events == [{"event": "auth.failed", "log_level": "info", "error": expected}]
