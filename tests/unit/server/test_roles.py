import json
from base64 import b64encode
from uuid import uuid4

from authlib.integrations.starlette_client import StarletteOAuth2App
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from opengrader.core.submissions import load_student_submission_markdown
from opengrader.core.workspace.intake import publish_submission
from opengrader.core.workspace.store import Workspace
from opengrader.server.settings import ServerSettings
from opengrader.server.workspace.app import create_app

SECRET = "test-session-secret"


def hosted(root, instructors="prof@sjsu.edu", **client):
    settings = ServerSettings(
        _env_file=None,
        auth_enabled=True,
        google_client_id="client-id",
        google_client_secret="client-secret",
        session_secret=SECRET,
        instructors=instructors,
    )
    return TestClient(create_app(root, settings=settings, process_jobs=False), **client)


def sign_in(client, email):
    session = {"user": {"id": email, "email": email, "name": email}}
    cookie = TimestampSigner(SECRET).sign(b64encode(json.dumps(session).encode()))
    client.cookies.set("opengrader_session", cookie.decode(), domain="testserver.local")


def test_instructors_manage_graders_and_graders_cannot(tmp_path):
    with hosted(tmp_path) as client:
        sign_in(client, "prof@sjsu.edu")
        assert client.get("/api/people").json() == {
            "domain": "sjsu.edu",
            "instructors": ["prof@sjsu.edu"],
            "graders": [],
        }
        response = client.post("/api/people/graders", json={"email": " TA@SJSU.edu"})
        assert response.json() == {
            "domain": "sjsu.edu",
            "instructors": ["prof@sjsu.edu"],
            "graders": ["ta@sjsu.edu"],
        }

        sign_in(client, "ta@sjsu.edu")
        assert client.get("/auth/me").json()["role"] == "grader"
        assert client.get("/api/assignments").status_code == 200
        assert client.get("/api/people").status_code == 403
        add = client.post("/api/people/graders", json={"email": "other@sjsu.edu"})
        assert add.status_code == 403
        remove = client.post("/api/people/graders/remove", json={"email": "ta@sjsu.edu"})
        assert remove.status_code == 403


def test_graders_must_be_one_address_on_the_domain(tmp_path):
    with hosted(tmp_path) as client:
        sign_in(client, "prof@sjsu.edu")
        for email in (
            "ta@gmail.com",
            "ta@sjsu.edu, tb@sjsu.edu",
            "prof@sjsu.edu",
            "a" * 65 + "@sjsu.edu",
            "mailto:ta@sjsu.edu",
        ):
            response = client.post("/api/people/graders", json={"email": email})
            assert response.status_code == 422, email
        assert client.get("/api/people").json()["graders"] == []


def test_promoting_a_grader_replaces_their_grader_access(tmp_path):
    Workspace.create(tmp_path).add_grader("ta@sjsu.edu")
    with hosted(tmp_path, instructors="prof@sjsu.edu,ta@sjsu.edu") as client:
        sign_in(client, "prof@sjsu.edu")
        assert client.get("/api/people").json()["graders"] == []
        remove = client.post(
            "/api/people/graders/remove", json={"email": "ta@sjsu.edu"}
        )
        assert remove.status_code == 422

    with hosted(tmp_path) as client:
        sign_in(client, "ta@sjsu.edu")
        assert client.get("/api/assignments").status_code == 401


def test_removing_a_grader_ends_their_session(tmp_path):
    Workspace.create(tmp_path).add_grader("ta@sjsu.edu")
    with hosted(tmp_path) as client:
        sign_in(client, "ta@sjsu.edu")
        grader_cookie = client.cookies.get("opengrader_session")
        assert client.get("/api/assignments").status_code == 200

        sign_in(client, "prof@sjsu.edu")
        removed = client.post("/api/people/graders/remove", json={"email": "ta@sjsu.edu"})
        assert removed.json()["graders"] == []

        client.cookies.set("opengrader_session", grader_cookie, domain="testserver.local")
        response = client.get("/api/assignments")
        assert response.status_code == 401
        assert "opengrader_session=null" in response.headers["set-cookie"]


def test_sign_in_admits_only_people_on_the_list(tmp_path, monkeypatch):
    claims = {"email_verified": True, "hd": "sjsu.edu", "name": "Someone"}

    async def token(self, request, **kwargs):
        return {"userinfo": {**claims, "sub": claims["email"]}}

    monkeypatch.setattr(StarletteOAuth2App, "authorize_access_token", token)
    with hosted(tmp_path) as client:
        claims["email"] = "student@sjsu.edu"
        response = client.get("/auth/callback", follow_redirects=False)
        assert response.status_code == 403
        assert "student@sjsu.edu" in response.text
        assert "Ask an instructor to add you." in response.text
        assert client.get("/auth/me").json()["user"] is None

        claims["email"] = "prof@sjsu.edu"
        response = client.get("/auth/callback", follow_redirects=False)
        assert response.status_code == 303
        assert client.get("/auth/me").json()["role"] == "instructor"


def test_signing_in_starts_in_the_instructor_view(tmp_path, monkeypatch):
    async def token(self, request, **kwargs):
        return {
            "userinfo": {
                "sub": "prof",
                "email": "prof@sjsu.edu",
                "email_verified": True,
                "hd": "sjsu.edu",
            }
        }

    monkeypatch.setattr(StarletteOAuth2App, "authorize_access_token", token)
    with hosted(tmp_path) as client:
        sign_in(client, "prof@sjsu.edu")
        assert client.post("/auth/view-as", json={"role": "grader"}).status_code == 204

        assert client.get("/auth/callback", follow_redirects=False).status_code == 303
        status = client.get("/auth/me").json()
        assert (status["role"], status["viewing_as_grader"]) == ("instructor", False)


def test_saved_grades_record_the_signed_in_grader(tmp_path):
    folder = tmp_path / "scans"
    folder.mkdir()
    path = folder / "one.submission.md"
    path.write_text(
        "---\nstudent_id: '123'\n---\n"
        "- q1:\n  - Response text: Yes\n  - Possible points: 1\n  - Points:\n"
    )
    workspace = Workspace.create(tmp_path, actor="server-account")
    assignment = workspace.create_assignment(
        "---\ntitle: Quiz\n---\n- (1 point) Agree?\n  - **Answer**\n    - Yes\n"
    )
    publish_submission(
        workspace,
        assignment.id,
        load_student_submission_markdown(path),
        asset_root=folder,
        source_key="one",
    )
    workspace.add_grader("ta@sjsu.edu")
    url = f"/api/assignments/{assignment.id}/submissions/1/answers/q1"
    with hosted(tmp_path) as client:
        sign_in(client, "ta@sjsu.edu")
        answer = client.get(url).json()
        response = client.put(
            url,
            json={
                "request_id": str(uuid4()),
                "expected_context_revision": answer["context_revision"],
                "expected_revision": answer["revision"],
                "rubric_labels": [],
                "analysis": "",
                "score_mode": "manual",
                "points_awarded": 1,
            },
        )
        assert response.status_code == 200, response.text

    assert [entry["actor"] for entry in workspace.history(assignment.id)] == [
        "ta@sjsu.edu"
    ]


def test_only_the_server_machine_can_stop_the_server(tmp_path):
    stopped = []
    with hosted(tmp_path) as remote:
        remote.app.state.shutdown = lambda: stopped.append(True)
        sign_in(remote, "prof@sjsu.edu")
        assert remote.post("/api/shutdown").status_code == 403
        assert stopped == []

    with hosted(
        tmp_path, base_url="http://localhost:8000", client=("127.0.0.1", 50000)
    ) as local:
        local.app.state.shutdown = lambda: stopped.append(True)
        assert local.post("/api/shutdown").status_code == 204
        assert stopped == [True]


def test_people_are_not_managed_when_sign_in_is_off(tmp_path):
    app = create_app(tmp_path, settings=ServerSettings(_env_file=None), process_jobs=False)
    with TestClient(app) as client:
        assert client.get("/api/people").status_code == 404


def test_instructors_can_view_the_app_as_a_grader(tmp_path):
    Workspace.create(tmp_path).add_grader("ta@sjsu.edu")
    with hosted(tmp_path) as client:
        sign_in(client, "prof@sjsu.edu")
        assert client.post("/auth/view-as", json={"role": "grader"}).status_code == 204
        status = client.get("/auth/me").json()
        assert (status["role"], status["viewing_as_grader"]) == ("grader", True)
        assert client.get("/api/people").status_code == 403

        assert client.post("/auth/view-as", json={"role": "instructor"}).status_code == 204
        assert client.get("/auth/me").json()["role"] == "instructor"
        assert client.get("/api/people").status_code == 200

        sign_in(client, "ta@sjsu.edu")
        switch = client.post("/auth/view-as", json={"role": "instructor"})
        assert switch.status_code == 403
        assert client.get("/api/people").status_code == 403
