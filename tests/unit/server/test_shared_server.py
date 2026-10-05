import json
import shutil
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from opengrader.cli.web import _root
from opengrader.cli.course import command
from opengrader.core.workspace.models import CourseDetails
from opengrader.core.workspace.store import Workspace
from opengrader.server.app import create_app
from opengrader.server.home import resolve_data_home
from opengrader.server.registry import WorkspaceRegistry, folder_name
from opengrader.server.settings import ServerSettings
from opengrader.server.workspace.client import exclusive_lock


def local_client(app):
    return TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1234))


def test_server_home_does_not_use_launcher_override(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENGRADER_SERVER_HOME")
    monkeypatch.setenv("OPENGRADER_HOME", str(tmp_path / "checkout"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert resolve_data_home() == tmp_path / ".opengrader"
    monkeypatch.setenv("OPENGRADER_SERVER_HOME", str(tmp_path / "hosted"))
    assert resolve_data_home() == tmp_path / "hosted"


def test_creation_registration_and_recent_order_survive_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "opengrader.server.registry.default_workspaces_dir",
        lambda: tmp_path / "Opengrader",
    )
    registry = WorkspaceRegistry(tmp_path / "server")
    created = registry.create(
        CourseDetails(number="CS101", name="Intro", term="fall", year=2026)
    )
    existing = Workspace.create(tmp_path / "course-on-disk")
    registered = registry.register(existing.root, CourseDetails(number="CS201"))
    assert created.path == str(tmp_path / "Opengrader/CS101 Fall 2026")
    assert registered.path == str(existing.root)
    registry.opened(registered.id)
    registry.opened(created.id)
    reopened = WorkspaceRegistry(registry.home)
    assert reopened.id == registry.id
    assert [item.id for item in reopened.list()] == [created.id, registered.id]
    assert reopened.get(created.id).last_opened is not None
    assert reopened.register(existing.root).number == "CS201"


def test_registry_upgrade_keeps_existing_names_as_course_numbers(tmp_path):
    import sqlite3

    workspace = Workspace.create(tmp_path / "data")
    home = tmp_path / "server"
    home.mkdir()
    with sqlite3.connect(home / "registry.sqlite3") as db:
        db.executescript("""
            CREATE TABLE instance (id TEXT NOT NULL);
            INSERT INTO instance VALUES ('server');
            CREATE TABLE workspaces (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE, last_opened TEXT
            );
        """)
        db.execute(
            "INSERT INTO workspaces VALUES (?, 'CMPE 195', ?, NULL)",
            (workspace.info().id, str(workspace.root)),
        )
    db.close()
    registry = WorkspaceRegistry(home)
    assert registry.id == "server"
    assert registry.get(workspace.info().id).course == CourseDetails(number="CMPE 195")


def test_registry_concurrent_registration_and_duplicate_copies(tmp_path):
    workspace = Workspace.create(tmp_path / "data")
    home = tmp_path / "server"
    with ThreadPoolExecutor(max_workers=4) as pool:
        items = list(
            pool.map(
                lambda _: WorkspaceRegistry(home).register(workspace.root), range(4)
            )
        )
    assert len({item.id for item in items}) == 1
    registry = WorkspaceRegistry(home)
    copied = tmp_path / "copy"
    shutil.copytree(workspace.root, copied)
    with pytest.raises(ValueError, match="Another copy"):
        registry.register(copied)
    shutil.rmtree(workspace.root)
    assert registry.register(copied).path == str(copied)
    assert len(registry.list()) == 1


def test_course_folder_names_are_safe_on_every_platform():
    assert (
        folder_name(CourseDetails(number="C/C++: Intro", term="spring", year=2026))
        == "C-C++- Intro Spring 2026"
    )
    assert folder_name(CourseDetails(number="CS 101.")) == "CS 101"
    for number in ["..", "CON", "lpt1"]:
        with pytest.raises(ValueError):
            folder_name(CourseDetails(number=number))


def test_opening_server_and_unknown_cwd_does_not_create_workspace(
    tmp_path, monkeypatch
):
    cwd = tmp_path / "project"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    assert _root(None) is None
    with pytest.raises(ValueError, match="No workspace"):
        _root(cwd)
    app = create_app(tmp_path / "server", process_jobs=False)
    with local_client(app) as client:
        assert client.get("/api/workspaces").json() == []
        assert client.get("/courses/unknown/api/workspace").status_code == 404
        assert (
            client.post(
                "/api/workspaces", json={"name": "Course", "path": str(cwd)}
            ).status_code
            == 422
        )
    assert not (cwd / ".opengrader").exists()


def test_scoped_workspaces_share_workers_but_not_data_or_assets(tmp_path):
    app = create_app(tmp_path / "server", process_jobs=False)
    registry = app.state.registry
    workspaces = [Workspace.create(tmp_path / name) for name in ["A", "B"]]
    items = [registry.register(workspace.root) for workspace in workspaces]
    with local_client(app) as client:
        for item in items:
            response = client.post(
                item.url_path + "/api/assignments",
                json={
                    "qa_markdown": f"---\ntitle: {item.number}\n---\n- (1 point) Question\n"
                },
            )
            assert response.status_code == 201, response.text
            assignment_id = response.json()["id"]
            other = next(other for other in items if other.id != item.id)
            assert (
                client.get(
                    other.url_path + f"/api/assignments/{assignment_id}/qa"
                ).status_code
                == 404
            )
            assets = Path(item.path) / ".opengrader/assets"
            assets.mkdir(exist_ok=True)
            (assets / "same.txt").write_text(item.number)
        for item in items:
            assert client.get(item.url_path + "/api/assets/same.txt").text == item.number
            assert (
                client.get(item.url_path + "/api/workspace").json()["course"]["number"]
                == item.number
            )
            assert len(client.get(item.url_path + "/api/assignments").json()) == 1
        apps = app.state.workspaces.apps
        assert (
            apps[items[0].id].state.scan_jobs.workers
            is apps[items[1].id].state.scan_jobs.workers
        )
        assert client.get("/api/assignments").status_code == 404
        assert client.post(items[0].url_path + "/api/shutdown").status_code == 404


def test_missing_or_locked_workspace_does_not_stop_another(tmp_path):
    app = create_app(tmp_path / "server", process_jobs=False)
    good = app.state.registry.register(Workspace.create(tmp_path / "good").root)
    missing = app.state.registry.register(Workspace.create(tmp_path / "missing").root)
    shutil.rmtree(missing.path)
    with local_client(app) as client:
        assert client.get(missing.url_path + "/api/workspace").status_code == 409
        assert not Path(missing.path).exists()
        with exclusive_lock(Path(good.path) / ".opengrader/service.lock"):
            assert client.get(good.url_path + "/api/workspace").status_code == 409
        assert client.get(good.url_path + "/api/workspace").status_code == 200


def test_unavailable_course_pages_load_the_app_and_apis_stay_json(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>Opengrader</title>")
    app = create_app(tmp_path / "server", web_dist=dist, process_jobs=False)
    missing = app.state.registry.register(Workspace.create(tmp_path / "missing").root)
    shutil.rmtree(missing.path)
    with local_client(app) as client:
        page = client.get("/courses/unknown/assignments/quiz/grade")
        assert page.status_code == 404
        assert page.text == "<title>Opengrader</title>"
        api = client.get("/courses/unknown/api/workspace")
        assert api.status_code == 404
        assert api.json() == {"detail": "This course isn't registered with this server."}
        page = client.get(missing.url_path + "/assignments")
        assert page.status_code == 409
        assert page.text == "<title>Opengrader</title>"


def cookie(settings, email):
    return (
        TimestampSigner(settings.session_secret.get_secret_value())
        .sign(
            b64encode(
                json.dumps(
                    {"user": {"id": email, "email": email, "name": "Grader"}}
                ).encode()
            )
        )
        .decode()
    )


def test_hosted_access_filters_catalog_and_every_workspace_resource(tmp_path):
    settings = ServerSettings(
        _env_file=None,
        auth_enabled=True,
        google_client_id="id",
        google_client_secret="secret",
        session_secret="session",
        instructors="prof@sjsu.edu",
    )
    app = create_app(tmp_path / "server", settings=settings, process_jobs=False)
    a = Workspace.create(tmp_path / "A")
    b = Workspace.create(tmp_path / "B")
    a.add_grader("grader@sjsu.edu")
    first, second = [app.state.registry.register(w.root) for w in [a, b]]
    with TestClient(app) as client:
        assert client.get(first.url_path + "/api/workspace").status_code == 401
        client.cookies.set("opengrader_session", cookie(settings, "grader@sjsu.edu"))
        assert [item["id"] for item in client.get("/api/workspaces").json()] == [
            first.id
        ]
        assert client.get(first.url_path + "/api/workspace").status_code == 200
        for path in [
            "/api/workspace",
            "/api/assignments",
            "/api/assets/secret.png",
            "/api/events",
            "/api/jobs",
            "/auth/me",
            "/assignments",
        ]:
            assert client.get(second.url_path + path).status_code == 403, path
        assert second.id not in app.state.workspaces.apps
        assert (
            client.get(first.url_path + "/api/workspace").status_code == 200
        )  # Denial did not log out the grader.
        course = {"number": "CS101", "name": "Unauthorized"}
        assert client.post("/api/workspaces", json=course).status_code == 403
        assert client.put(f"/api/workspaces/{first.id}", json=course).status_code == 403
        assert client.delete(f"/api/workspaces/{first.id}").status_code == 403
        assert (
            client.post(
                "/api/server/workspaces/register", json={"path": str(b.root)}
            ).status_code
            == 404
        )
        assert client.post("/api/shutdown").status_code == 404
        a.remove_grader("grader@sjsu.edu")
        assert client.get(first.url_path + "/api/workspace").status_code == 401


def test_register_requires_control_token_and_browser_creation_uses_default(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "opengrader.server.registry.default_workspaces_dir",
        lambda: tmp_path / "Opengrader",
    )
    app = create_app(tmp_path / "server", process_jobs=False)
    workspace = Workspace.create(tmp_path / "existing")
    with local_client(app) as client:
        assert (
            client.post(
                "/api/server/workspaces/register", json={"path": str(workspace.root)}
            ).status_code
            == 404
        )
        response = client.post(
            "/api/server/workspaces/register",
            json={"path": str(workspace.root)},
            headers={"Authorization": f"Bearer {app.state.shutdown_token}"},
        )
        assert response.status_code == 200
        course = {"number": "CS101", "name": "Intro", "term": "fall", "year": 2026}
        assert (
            client.post("/api/workspaces", json={"name": "Intro"}).status_code == 422
        )
        created = client.post("/api/workspaces", json=course)
        assert created.status_code == 201
        assert "path" not in created.json()
        assert (
            tmp_path / "Opengrader/CS101 Fall 2026/.opengrader/workspace.sqlite3"
        ).is_file()
        duplicate = client.post("/api/workspaces", json=course)
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"].startswith("CS101 Fall 2026 already exists.")
        updated = client.put(
            f"/api/workspaces/{created.json()['id']}",
            json={**course, "name": "Programming", "term": None},
        )
        assert updated.status_code == 200, updated.text
        assert [
            (item["number"], item["name"], item["term"], item["year"])
            for item in client.get("/api/workspaces").json()
            if item["id"] == created.json()["id"]
        ] == [("CS101", "Programming", None, 2026)]
        assert client.put("/api/workspaces/unknown", json=course).status_code == 404
        course_id = created.json()["id"]
        assert client.delete(f"/api/workspaces/{course_id}").status_code == 204
        assert course_id not in [item["id"] for item in client.get("/api/workspaces").json()]
        assert client.get(f"/courses/{course_id}/api/workspace").status_code == 404
        assert (
            tmp_path / "Opengrader/CS101 Fall 2026/.opengrader/workspace.sqlite3"
        ).is_file()
        assert client.delete(f"/api/workspaces/{course_id}").status_code == 404
        recreated = client.post("/api/workspaces", json=course)
        assert recreated.status_code == 409
        assert recreated.json()["detail"].startswith(
            "Files for CS101 Fall 2026 already exist."
        )


def test_explicit_cli_create_and_register(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    runner = CliRunner(mix_stderr=False)
    root = tmp_path / "chosen"
    shown = Path("~", "chosen")

    def courses():
        return [
            (item["number"], item["name"], item["term"], item["year"], item["path"])
            for item in json.loads(runner.invoke(command, ["list", "--json"]).stdout)
        ]

    details = ["--number", "CS 101", "--name", "Intro", "--term", "Spring", "--year", "2026"]
    result = runner.invoke(command, ["create", *details, "--directory", str(root), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Spring 2026" in result.stdout and str(shown) in result.stdout
    assert not root.exists()
    assert courses() == []
    result = runner.invoke(command, ["create", *details, "--directory", str(root)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(command, ["add", str(root), "--name", "Renamed"])
    assert "Intro" in result.stdout and "Renamed" in result.stdout
    result = runner.invoke(command, ["edit", str(root), "--year", "2030", "--dry-run"])
    assert "Spring 2030" in result.stdout
    result = runner.invoke(command, ["remove", str(root), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert courses() == [("CS 101", "Renamed", "spring", 2026, str(root))]
    result = runner.invoke(command, ["create", *details, "--directory", str(root), "--dry-run"])
    assert result.exit_code == 1 and "already registered as another course" in result.stderr
    (root / ".opengrader").rename(root / ".hidden")
    result = runner.invoke(command, ["create", *details, "--directory", str(root)])
    assert result.exit_code == 1 and "already registered as another course" in result.stderr
    assert not (root / ".opengrader").exists()
    (root / ".hidden").rename(root / ".opengrader")
    (root / "notes").mkdir()
    result = runner.invoke(command, ["edit", str(root / "notes"), "--year", "2027"])
    assert result.exit_code == 0, result.output
    assert courses() == [("CS 101", "Renamed", "spring", 2027, str(root))]
    filtered = runner.invoke(command, ["list", "--term", "fall", "--year", "2027"])
    assert filtered.stdout == "No courses match.\n"
    assert "CS 101" in runner.invoke(command, ["list", "--term", "Spring"]).stdout
    assert runner.invoke(command, ["edit", str(root), "--number", " "]).exit_code == 2
    shutil.rmtree(root / "notes")
    shutil.move(root, tmp_path / "moved")
    assert runner.invoke(command, ["list"]).stdout.endswith(f"{shown} (unavailable)\n")
    result = runner.invoke(command, ["remove", str(root)])
    assert result.exit_code == 0, result.output
    assert courses() == []
    assert runner.invoke(command, ["remove", str(root)]).exit_code == 1
    root = tmp_path / "moved"
    assert _root(root) == root


def test_cli_add_reports_a_moved_course_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    runner = CliRunner(mix_stderr=False)
    details = ["--number", "CS 101", "--name", "Intro", "--term", "fall", "--year", "2026"]
    runner.invoke(command, ["create", *details, "--directory", str(tmp_path / "old")])
    shutil.move(tmp_path / "old", tmp_path / "new")
    result = runner.invoke(command, ["add", str(tmp_path / "new")])
    assert str(Path("~", "old")) in result.stdout and str(Path("~", "new")) in result.stdout


def test_server_configuration_is_independent_of_cwd(tmp_path, monkeypatch):
    home = tmp_path / "server"
    home.mkdir()
    (home / "server.env").write_text("OPENGRADER_GOOGLE_DOMAIN=example.edu\n")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / ".env").write_text("OPENGRADER_AUTH_ENABLED=invalid-boolean\n")
    monkeypatch.chdir(cwd)
    app = create_app(home, process_jobs=False)
    assert app.state.workspaces.settings.google_domain == "example.edu"
    assert not app.state.workspaces.settings.auth_enabled


def test_mounted_folder_session_still_refuses_assignment_changes(tmp_path):
    from opengrader.core.workspace.folder_session import start_folder_session

    app = create_app(tmp_path / "server", process_jobs=False)
    workspace = Workspace.create(tmp_path / "data")
    assignment = workspace.create_assignment("- (1 point) Question\n")
    start_folder_session(workspace.root, assignment.id)
    item = app.state.registry.register(workspace.root)
    with local_client(app) as client:
        response = client.post(
            item.url_path + "/api/assignments",
            json={"qa_markdown": "- (2 points) Other\n"},
        )
        assert response.status_code == 409
        assert "folder" in response.json()["detail"]


def test_two_tabs_open_workspace_once_and_resume_pending_feedback(
    tmp_path, monkeypatch
):
    from threading import Event

    from opengrader.server.workspace.jobs import FeedbackJobs

    app = create_app(tmp_path / "server")
    workspace = Workspace.create(tmp_path / "data")
    item = app.state.registry.register(workspace.root)
    recovered = Event()
    original = FeedbackJobs.__init__

    def initialize(self, saved):
        original(self, saved)
        recovered.set()

    monkeypatch.setattr(FeedbackJobs, "__init__", initialize)
    # A saved feedback queue must be resumed without opening a browser first.
    monkeypatch.setattr(
        Workspace, "jobs", lambda self: [type("Job", (), {"status": "running"})()]
    )
    with local_client(app) as client:
        assert recovered.wait(timeout=3)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda _: client.get(item.url_path + "/api/workspace"), range(2)
                )
            )
        assert all(result.status_code == 200 for result in results)
        assert len(app.state.workspaces.apps) == 1
        assert len(app.state.workspaces.feedback.jobs) == 1
