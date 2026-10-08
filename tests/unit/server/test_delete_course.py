from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from opengrader.core.workspace.store import Workspace
from opengrader.server.app import create_app

SAMPLE = Path(__file__).resolve().parents[3] / "samples" / "cs101-quiz5"


def test_deleting_a_course_stops_its_work_and_removes_only_opengrader_data(tmp_path, monkeypatch):
    from opengrader.server import scan_jobs

    monkeypatch.setattr("opengrader.server.registry.default_workspaces_dir", lambda: tmp_path / "Opengrader")
    monkeypatch.setattr(scan_jobs, "ProcessPoolExecutor", lambda **_: ThreadPoolExecutor(max_workers=1))
    app = create_app(tmp_path / "server", process_jobs=False)
    existing = Workspace.create(tmp_path / "class-folder")
    (existing.root / "roster.csv").write_text("SID,Name\n", encoding="utf-8")
    kept = app.state.registry.register(existing.root)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        created = client.post("/api/workspaces", json={"number": "CS 101", "name": "Intro"})
        assert created.status_code == 201, created.text
        course = created.json()["id"]
        api = f"/courses/{course}/api"
        assignment = client.post(f"{api}/assignments", json={"qa_markdown": (SAMPLE / "qa.md").read_text()})
        assert assignment.status_code == 201, assignment.text
        scans = f"{api}/assignments/{assignment.json()['id']}/scans"
        batch = client.post(scans, params={"filename": "submissions.pdf"}, content=(SAMPLE / "submissions.pdf").read_bytes()).json()
        analyzing = client.post(
            f"{scans}/{batch['id']}/analyze",
            json={"expected_revision": batch["revision"], "mode": "fixed", "pages_per_submission": 2},
        )
        assert analyzing.status_code == 202, analyzing.text

        folder = tmp_path / "Opengrader" / "cs-101"
        wrong = client.post(f"/api/workspaces/{course}/delete", json={"number": "CS 102"})
        assert wrong.status_code == 422
        assert wrong.json()["detail"] == "Type CS 101 to delete this course."
        assert (folder / ".opengrader/workspace.sqlite3").is_file()

        assert client.post(f"/api/workspaces/{course}/delete", json={"number": "CS 101"}).status_code == 204
        assert not folder.exists()
        assert client.get(f"{api}/workspace").status_code == 404
        assert [item["id"] for item in client.get("/api/workspaces").json()] == [kept.id]

        assert client.post(f"/api/workspaces/{kept.id}/delete", json={"number": "class-folder"}).status_code == 204
        assert [path.name for path in existing.root.iterdir()] == ["roster.csv"]
        assert client.get("/api/workspaces").json() == []


def test_deleting_a_course_stops_running_scans_and_cancels_queued_ones(tmp_path, monkeypatch):
    import threading
    import time

    from opengrader.server import scan_jobs

    monkeypatch.setattr("opengrader.server.registry.default_workspaces_dir", lambda: tmp_path / "Opengrader")
    monkeypatch.setattr(scan_jobs, "ProcessPoolExecutor", lambda **_: ThreadPoolExecutor(max_workers=1))
    started, release = threading.Event(), threading.Event()
    analyzed: list[str] = []
    analyze = scan_jobs.ScanJobs._analyze

    def held(self, assignment_id, batch):
        analyzed.append(batch.id)
        started.set()
        release.wait(10)
        analyze(self, assignment_id, batch)

    monkeypatch.setattr(scan_jobs.ScanJobs, "_analyze", held)
    app = create_app(tmp_path / "server", process_jobs=False)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        course = client.post("/api/workspaces", json={"number": "CS 101", "name": "Intro"}).json()["id"]
        api = f"/courses/{course}/api"
        assignment = client.post(f"{api}/assignments", json={"qa_markdown": (SAMPLE / "qa.md").read_text()}).json()["id"]
        scans = f"{api}/assignments/{assignment}/scans"
        batches = []
        for pdf in (SAMPLE / "submissions.pdf", SAMPLE / "variants/scratch-sheet-first/submissions.pdf"):
            batch = client.post(scans, params={"filename": pdf.name}, content=pdf.read_bytes()).json()
            analyzing = client.post(
                f"{scans}/{batch['id']}/analyze",
                json={"expected_revision": batch["revision"], "mode": "fixed", "pages_per_submission": 2},
            )
            assert analyzing.status_code == 202, analyzing.text
            batches.append(batch["id"])
        assert started.wait(10)

        with ThreadPoolExecutor(max_workers=1) as pool:
            deleting = pool.submit(client.post, f"/api/workspaces/{course}/delete", json={"number": "CS 101"})
            deadline = time.monotonic() + 10
            while course not in app.state.workspaces.deleting and not deleting.done() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert client.get(f"{api}/workspace").json() == {"detail": "This course is being deleted."}
            release.set()
            assert deleting.result(timeout=30).status_code == 204

        assert analyzed == batches[:1]
        assert not (tmp_path / "Opengrader" / "cs-101").exists()
