import json
import sqlite3
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


def hosted(root, admins="prof@sjsu.edu", **client):
    settings = ServerSettings(
        _env_file=None,
        auth_enabled=True,
        google_client_id="client-id",
        google_client_secret="client-secret",
        session_secret=SECRET,
        admins=admins,
    )
    return TestClient(create_app(root, settings=settings, process_jobs=False), **client)


def sign_in(client, email):
    session = {"user": {"id": email, "email": email, "name": email}}
    cookie = TimestampSigner(SECRET).sign(b64encode(json.dumps(session).encode()))
    client.cookies.set("opengrader_session", cookie.decode(), domain="testserver.local")


def test_course_instructors_manage_staff_and_graders_cannot(tmp_path):
    with hosted(tmp_path) as client:
        sign_in(client, "prof@sjsu.edu")
        assert client.get("/auth/me").json()["admin"] is True
        assert client.get("/api/staff").json() == {
            "domain": "sjsu.edu",
            "admins": ["prof@sjsu.edu"],
            "staff": [],
        }
        added = client.post(
            "/api/staff", json={"email": " Co@SJSU.edu", "role": "instructor"}
        )
        assert added.json()["staff"] == [{"email": "co@sjsu.edu", "role": "instructor"}]

        sign_in(client, "co@sjsu.edu")
        me = client.get("/auth/me").json()
        assert (me["role"], me["admin"]) == ("instructor", False)
        assert client.get("/api/roster").status_code == 200
        client.post("/api/staff", json={"email": "ta@sjsu.edu", "role": "grader"})
        client.put("/api/staff/role", json={"email": "ta@sjsu.edu", "role": "instructor"})
        client.put("/api/staff/role", json={"email": "ta@sjsu.edu", "role": "grader"})
        assert client.get("/api/staff").json()["staff"] == [
            {"email": "co@sjsu.edu", "role": "instructor"},
            {"email": "ta@sjsu.edu", "role": "grader"},
        ]

        sign_in(client, "ta@sjsu.edu")
        assert client.get("/auth/me").json()["role"] == "grader"
        assert client.get("/api/assignments").status_code == 200
        assert client.get("/api/roster").status_code == 403
        assert client.get("/api/staff").status_code == 403
        promote = {"email": "ta@sjsu.edu", "role": "instructor"}
        assert client.put("/api/staff/role", json=promote).status_code == 403
        add = {"email": "other@sjsu.edu", "role": "grader"}
        assert client.post("/api/staff", json=add).status_code == 403
        remove = client.post("/api/staff/remove", json={"email": "co@sjsu.edu"})
        assert remove.status_code == 403


def test_staff_must_be_one_new_address_on_the_domain(tmp_path):
    Workspace.create(tmp_path).add_staff("ta@sjsu.edu", "grader")
    with hosted(tmp_path) as client:
        sign_in(client, "prof@sjsu.edu")
        for email in (
            "ta@gmail.com",
            "ta@sjsu.edu, tb@sjsu.edu",
            "prof@sjsu.edu",
            "TA@sjsu.edu",
            "a" * 65 + "@sjsu.edu",
            "mailto:ta@sjsu.edu",
        ):
            response = client.post("/api/staff", json={"email": email, "role": "grader"})
            assert response.status_code == 422, email
        response = client.post("/api/staff", json={"email": "tb@sjsu.edu", "role": "admin"})
        assert response.status_code == 422
        assert client.get("/api/staff").json()["staff"] == [
            {"email": "ta@sjsu.edu", "role": "grader"}
        ]


def test_nobody_changes_their_own_access_or_an_admins(tmp_path):
    Workspace.create(tmp_path).add_staff("co@sjsu.edu", "instructor")
    with hosted(tmp_path) as client:
        sign_in(client, "co@sjsu.edu")
        demote = {"email": "co@sjsu.edu", "role": "grader"}
        assert client.put("/api/staff/role", json=demote).status_code == 422
        leave = client.post("/api/staff/remove", json={"email": "co@sjsu.edu"})
        assert leave.status_code == 422
        demote = {"email": "prof@sjsu.edu", "role": "grader"}
        assert client.put("/api/staff/role", json=demote).status_code == 422
        remove = client.post("/api/staff/remove", json={"email": "prof@sjsu.edu"})
        assert remove.status_code == 422
        assert client.get("/auth/me").json()["role"] == "instructor"


def test_admins_are_instructors_whatever_their_course_role(tmp_path):
    Workspace.create(tmp_path).add_staff("ta@sjsu.edu", "grader")
    with hosted(tmp_path, admins="prof@sjsu.edu,ta@sjsu.edu") as client:
        sign_in(client, "ta@sjsu.edu")
        assert client.get("/auth/me").json()["role"] == "instructor"
        assert client.get("/api/staff").json()["admins"] == ["prof@sjsu.edu", "ta@sjsu.edu"]

    with hosted(tmp_path) as client:
        sign_in(client, "ta@sjsu.edu")
        assert client.get("/auth/me").json()["role"] == "grader"


def test_removing_staff_ends_their_session(tmp_path):
    Workspace.create(tmp_path).add_staff("ta@sjsu.edu", "grader")
    with hosted(tmp_path) as client:
        sign_in(client, "ta@sjsu.edu")
        grader_cookie = client.cookies.get("opengrader_session")
        assert client.get("/api/assignments").status_code == 200

        sign_in(client, "prof@sjsu.edu")
        removed = client.post("/api/staff/remove", json={"email": "ta@sjsu.edu"})
        assert removed.json()["staff"] == []

        client.cookies.set("opengrader_session", grader_cookie, domain="testserver.local")
        response = client.get("/api/assignments")
        assert response.status_code == 401
        assert "opengrader_session=null" in response.headers["set-cookie"]


def test_sign_in_admits_only_admins_and_staff(tmp_path, monkeypatch):
    Workspace.create(tmp_path).add_staff("co@sjsu.edu", "instructor")
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

        for email in ("prof@sjsu.edu", "co@sjsu.edu"):
            claims["email"] = email
            response = client.get("/auth/callback", follow_redirects=False)
            assert response.status_code == 303
            assert client.get("/auth/me").json()["role"] == "instructor"


def test_upgrading_keeps_existing_graders(tmp_path):
    workspace = Workspace.create(tmp_path)
    with sqlite3.connect(workspace.database) as db:
        db.executescript(
            "DROP TABLE staff; CREATE TABLE graders (email TEXT PRIMARY KEY);"
            "INSERT INTO graders VALUES ('ta@sjsu.edu'); PRAGMA user_version = 8;"
            "ALTER TABLE workspace DROP COLUMN number; ALTER TABLE workspace DROP COLUMN name;"
            "ALTER TABLE workspace DROP COLUMN term; ALTER TABLE workspace DROP COLUMN year;"
        )
    assert Workspace(tmp_path).staff() == {"ta@sjsu.edu": "grader"}


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
    workspace.add_staff("ta@sjsu.edu", "grader")
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


def test_only_callers_with_the_service_token_can_stop_the_server(tmp_path):
    stopped = []
    with hosted(tmp_path) as remote:
        remote.app.state.shutdown = lambda: stopped.append(True)
        sign_in(remote, "prof@sjsu.edu")
        assert remote.post("/api/shutdown").status_code == 404
        assert stopped == []

    with hosted(
        tmp_path, base_url="http://localhost:8000", client=("127.0.0.1", 50000)
    ) as local:
        local.app.state.shutdown = lambda: stopped.append(True)
        assert local.post("/api/shutdown").status_code == 404
        wrong = {"Authorization": "Bearer not-the-token"}
        assert local.post("/api/shutdown", headers=wrong).status_code == 404
        assert stopped == []

        token = {"Authorization": f"Bearer {local.app.state.shutdown_token}"}
        assert local.post("/api/shutdown", headers=token).status_code == 204
        assert stopped == [True]


def test_staff_is_not_managed_when_sign_in_is_off(tmp_path):
    app = create_app(tmp_path, settings=ServerSettings(_env_file=None), process_jobs=False)
    with TestClient(app) as client:
        assert client.get("/api/staff").status_code == 404


