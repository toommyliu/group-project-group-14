import json
from base64 import b64encode

import httpx
import pytest
from authlib.integrations.starlette_client import StarletteOAuth2App
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from opengrader.core.workspace.folder_session import (
    load_folder_session,
    start_folder_session,
)
from opengrader.core.workspace.models import AssignmentInputs
from opengrader.server.auth import RemoteAuthGuard
from opengrader.server.settings import ServerSettings
from opengrader.server.workspace.app import create_app


@pytest.fixture(params=[False, True], ids=["offline", "google-sign-in"])
def local_app(tmp_path, request):
    settings = ServerSettings(
        _env_file=None,
        auth_enabled=request.param,
        google_client_id="client-id",
        google_client_secret="client-secret",
        session_secret="test-session-secret",
        instructors="prof@sjsu.edu",
    )
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<title>Opengrader test</title>")
    app = create_app(tmp_path, web_dist=web, settings=settings, process_jobs=False)
    if not settings.auth_enabled:
        app.add_middleware(RemoteAuthGuard)
    workspace = app.state.workspace
    assignment = workspace.create_assignment("- (1 point) Question\n")
    (tmp_path / "submissions").mkdir()
    workspace.save_assignment_inputs(
        AssignmentInputs(
            id=assignment.id, folder=".", scan="", output=".", submissions="submissions"
        )
    )
    start_folder_session(tmp_path, assignment.id)

    return app


@pytest.fixture
def local_client(local_app):
    with TestClient(
        local_app, base_url="http://localhost:8000", client=("127.0.0.1", 50000)
    ) as client:
        yield client


@pytest.mark.parametrize("headers", [
    {"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "https://attacker.example"},
    {"Origin": "null"},
    {"Origin": "null", "Sec-Fetch-Site": "cross-site"},
    {"Origin": ""},
    {"Origin": "http://localhost:9000", "Sec-Fetch-Site": "same-site"},
    {"Origin": "https://localhost:8000"},
    {"Origin": "http://127.0.0.1:8000"},
    {"Origin": "http://localhost:8000.attacker.example"},
    {"Origin": "http://localhost:invalid"},
    {"Origin": "http://localhost:8000/path"},
    {"Origin": "http://user@localhost:8000"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Origin": "http://localhost:8000", "Sec-Fetch-Site": "cross-site"},
    {
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    },
])
def test_cross_origin_post_cannot_end_folder_session(local_client, headers):
    response = local_client.post("/api/folder-session/convert", headers=headers)

    assert response.status_code == 403
    assert load_folder_session(local_client.app.state.workspace.root) is not None


@pytest.mark.parametrize("headers", [
    {},
    {"Origin": "http://localhost:8000", "Sec-Fetch-Site": "same-origin"},
    {"Origin": "null", "Sec-Fetch-Site": "same-origin"},
    {"Origin": "http://localhost:8000"},
    {"Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Site": "none"},
    {"Host": "localhost:5173", "Origin": "http://localhost:5173"},
    {"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:8000"},
    {"Host": "[::1]:8000", "Origin": "http://[::1]:8000"},
    {"Host": "localhost", "Origin": "http://localhost"},
])
def test_local_clients_can_end_folder_session(local_client, headers):
    response = local_client.post("/api/folder-session/convert", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["folder_session"] is None
    assert load_folder_session(local_client.app.state.workspace.root) is None


@pytest.mark.parametrize("destination", ["empty", "iframe"])
def test_cross_site_fetch_and_embedding_cannot_read_api(local_client, destination):
    response = local_client.get("/api/assignments", headers={
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "no-cors" if destination == "empty" else "navigate",
        "Sec-Fetch-Dest": destination,
    })

    assert response.status_code == 403


def test_cross_site_auth_post_is_rejected(local_client):
    response = local_client.post("/auth/logout", headers={
        "Origin": "https://attacker.example",
        "Sec-Fetch-Site": "cross-site",
    })

    assert response.status_code == 403


def test_cross_site_page_navigation_is_allowed(local_client):
    response = local_client.get("/assignments", headers={
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    })

    assert response.status_code == 200
    assert "Opengrader test" in response.text


def test_cross_site_api_page_navigation_is_allowed(local_client):
    response = local_client.get("/api/assignments", headers={
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
    })

    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.parametrize("headers", [
    {"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "https://untrusted.grader.example", "Sec-Fetch-Site": "same-site"},
    {"Origin": "https://attacker.example"},
])
def test_hosted_session_rejects_cross_origin_mutations(tmp_path, headers):
    settings = ServerSettings(
        _env_file=None,
        auth_enabled=True,
        google_client_id="client-id",
        google_client_secret="client-secret",
        session_secret="test-session-secret",
        instructors="prof@sjsu.edu",
    )
    session = {"user": {"id": "prof", "email": "prof@sjsu.edu", "name": "Professor"}}
    cookie = TimestampSigner("test-session-secret").sign(
        b64encode(json.dumps(session).encode())
    ).decode()
    app = create_app(tmp_path, settings=settings, process_jobs=False)
    with TestClient(app, base_url="https://grader.example") as client:
        client.cookies.set("opengrader_session", cookie, domain="grader.example")
        response = client.post(
            "/auth/logout", headers={**headers, "X-Forwarded-Proto": "https"}
        )
        assert response.status_code == 403
        assert client.get("/auth/me").json()["user"]["email"] == "prof@sjsu.edu"
        response = client.post("/auth/logout", headers={
            "Origin": "https://grader.example",
            "X-Forwarded-Proto": "https",
        })
        assert response.status_code == 204
        assert client.get("/auth/me").json()["user"] is None


def test_service_launcher_installs_origin_protection(tmp_path, monkeypatch):
    from opengrader.server.workspace.client import WorkspaceClient, ensure_service

    monkeypatch.setenv("OPENGRADER_AUTH_ENABLED", "false")
    connection = ensure_service(tmp_path)
    try:
        with httpx.Client(base_url=connection.url) as client:
            response = client.post("/api/jobs/assignment/1/retry", headers={
                "Origin": "https://attacker.example",
                "Sec-Fetch-Site": "cross-site",
            })
            assert response.status_code == 403
            assert client.post("/api/jobs/assignment/1/retry").status_code == 204
    finally:
        WorkspaceClient(connection).request("POST", "/api/shutdown")


def test_google_callback_allows_cross_site_navigation(tmp_path, monkeypatch):
    async def token(self, request, **kwargs):
        return {"userinfo": {
            "sub": "prof",
            "email": "prof@sjsu.edu",
            "email_verified": True,
            "hd": "sjsu.edu",
        }}

    monkeypatch.setattr(StarletteOAuth2App, "authorize_access_token", token)
    settings = ServerSettings(
        _env_file=None,
        auth_enabled=True,
        google_client_id="client-id",
        google_client_secret="client-secret",
        session_secret="test-session-secret",
        instructors="prof@sjsu.edu",
    )
    app = create_app(tmp_path, settings=settings, process_jobs=False)
    with TestClient(
        app, base_url="http://localhost:8000", client=("127.0.0.1", 50000)
    ) as client:
        response = client.get("/auth/callback", follow_redirects=False, headers={
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        })

        assert response.status_code == 303
        assert client.get("/auth/me").json()["user"]["email"] == "prof@sjsu.edu"
        logout = client.post("/auth/logout", headers={
            "Origin": "https://attacker.example",
            "Sec-Fetch-Site": "cross-site",
        })
        assert logout.status_code == 403
        assert client.get("/auth/me").json()["user"]["email"] == "prof@sjsu.edu"
