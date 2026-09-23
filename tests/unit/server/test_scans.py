"""Intake boundaries: lossless plans, durable publication, and safe retries."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import fitz
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from opengrader.core.submissions.scan_plan import ScanPage, validate_scan_plan, write_scan_submission
from opengrader.core.submissions.scan_check import build_scan_report, PageScanFeatures
from opengrader.server.workspace.app import create_app
from opengrader.server.scan_store import ScanStore


QA = """---
title: Scan test
---

- (2 points) What is two plus two?
  - **Answer**
    - 4
"""


def pdf_bytes(pages: int = 4) -> bytes:
    with fitz.open() as document:
        for index in range(pages):
            page = document.new_page()
            page.insert_text((72, 72), f"Source page {index + 1}")
        return document.tobytes()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from opengrader.server import scan_jobs

    monkeypatch.setattr(scan_jobs, "ProcessPoolExecutor", lambda **_: ThreadPoolExecutor(max_workers=1))
    monkeypatch.setattr(scan_jobs, "analyze_scan_pdf", lambda path, **_: build_scan_report([
        PageScanFeatures(page=index + 1) for index in range(len(PdfReader(path).pages))
    ], source=str(path)))
    app = create_app(tmp_path)
    with TestClient(app) as client:
        result = client.post("/assignments", json={"qa_markdown": QA})
        assert result.status_code == 201, result.text
        assignment_id = result.json()["id"]
        yield client, app, assignment_id, tmp_path


def upload(client, assignment_id, content=None, name="scans.pdf"):
    response = client.post(f"/assignments/{assignment_id}/scans", params={"filename": name}, content=content or pdf_bytes())
    assert response.status_code == 200, response.text
    return response.json()


def batch_url(assignment_id, batch):
    return f"/assignments/{assignment_id}/scans/{batch['id']}"


def current(client, assignment_id, batch):
    return next(item for item in client.get(f"/assignments/{assignment_id}/scans").json()["batches"] if item["id"] == batch["id"])


def drain(app):
    # A barrier in the single-worker queue proves all earlier accepted work finished.
    app.state.scan_jobs.executor.submit(lambda: None).result(timeout=10)


def analyze(client, app, assignment_id, batch):
    result = client.post(batch_url(assignment_id, batch) + "/analyze", json={"expected_revision": batch["revision"], "mode": "fixed", "pages_per_submission": 2})
    assert result.status_code == 202, result.text
    drain(app)
    result = current(client, assignment_id, batch)
    assert result["status"] == "review", result
    return result


def fake_extract(pdf, output, **_):
    directory = output / pdf.stem
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{pdf.stem}.submission.md").write_text("""---
student_name: Test student
student_id: '123'
assignment_title: Scan test
---
- q1:
  - Prompt: What is two plus two?
  - Possible points: 2
  - Response text: 4
""")
    return directory


def test_upload_is_deduplicated_and_retains_original(workspace):
    client, app, assignment_id, root = workspace
    content = pdf_bytes()
    first = upload(client, assignment_id, content)
    again = upload(client, assignment_id, content, "renamed.pdf")
    assert first["id"] == again["id"]
    assert client.get(batch_url(assignment_id, first) + "/pdf").content == content
    assert client.get(batch_url(assignment_id, first) + "/pages/1").headers["content-type"] == "image/png"
    assert client.get(batch_url(assignment_id, first) + "/pages/5").status_code == 404
    assert client.put(f"/assignments/{assignment_id}", json={"qa_markdown": QA, "expected_qa_markdown": QA}).status_code == 409
    other = client.post("/assignments", json={"qa_markdown": QA}).json()["id"]
    assert client.get(batch_url(other, first) + "/pdf").status_code == 404
    with TestClient(create_app(root)) as restarted:
        assert current(restarted, assignment_id, first)["sha256"] == first["sha256"]


def test_review_rejects_lost_pages_duplicates_and_stale_writes(workspace):
    client, app, assignment_id, _ = workspace
    batch = analyze(client, app, assignment_id, upload(client, assignment_id))
    url = batch_url(assignment_id, batch) + "/review"
    for numbers in ([1, 2, 3], [1, 2, 3, 3], [1, 2, 3, 5]):
        response = client.put(url, json={"expected_revision": batch["revision"], "groups": [[{"number": page} for page in numbers]]})
        assert response.status_code == 422
    good = {"expected_revision": batch["revision"], "groups": [[{"number": 3, "rotation": 90}, {"number": 1}], [{"number": 4}]], "excluded_pages": [2]}
    assert client.put(url, json=good).status_code == 200
    assert client.put(url, json=good).status_code == 409
    saved = current(client, assignment_id, batch)
    assert saved["groups"][0]["pages"] == [{"number": 3, "rotation": 90}, {"number": 1, "rotation": 0}]


def test_remove_and_restore_keep_review_and_original(workspace):
    client, app, assignment_id, _ = workspace
    content = pdf_bytes()
    batch = analyze(client, app, assignment_id, upload(client, assignment_id, content))
    url = batch_url(assignment_id, batch)
    removed = client.post(url + "/remove", json={"expected_revision": batch["revision"]}).json()
    assert removed["status"] == "removed"
    assert client.post(url + "/process", json={"expected_revision": removed["revision"], "acknowledge_findings": True}).status_code == 409
    assert client.post(url + "/restore", json={"expected_revision": batch["revision"]}).status_code == 409
    restored = client.post(url + "/restore", json={"expected_revision": removed["revision"]}).json()
    assert restored["status"] == "review"
    assert restored["groups"] == batch["groups"]
    assert client.get(url + "/pdf").content == content
    assert upload(client, assignment_id, content)["id"] == batch["id"]


@pytest.mark.parametrize("removed", [False, True])
def test_delete_scan_erases_files_and_unlocks_empty_assignment(workspace, removed):
    client, app, assignment_id, root = workspace
    content = pdf_bytes()
    batch = analyze(client, app, assignment_id, upload(client, assignment_id, content))
    url = batch_url(assignment_id, batch)
    assert client.get(url + "/pages/1").status_code == 200
    if removed:
        batch = client.post(url + "/remove", json={"expected_revision": batch["revision"]}).json()
    assert client.delete(url, params={"expected_revision": batch["revision"] - 1}).status_code == 409
    assert client.get(url + "/pdf").content == content
    assert client.delete(url, params={"expected_revision": batch["revision"]}).status_code == 204
    assert not ScanStore(root / ".opengrader" / "assignments").directory(assignment_id, batch["id"]).exists()
    assert client.get(url + "/pdf").status_code == 404
    assert client.get(f"/assignments/{assignment_id}/scans").json()["batches"] == []
    assert client.put(f"/assignments/{assignment_id}", json={"qa_markdown": QA, "expected_qa_markdown": QA}).status_code == 200
    replacement = upload(client, assignment_id, content)
    assert replacement["id"] != batch["id"]
    assert client.delete(batch_url(assignment_id, replacement), params={"expected_revision": replacement["revision"]}).status_code == 204
    assert client.delete(f"/assignments/{assignment_id}").status_code == 204


@pytest.mark.parametrize("published", [False, True])
def test_failed_scan_deletion_includes_published_files_even_without_recorded_ids(workspace, monkeypatch, published):
    client, app, assignment_id, root = workspace
    from opengrader.server import scan_jobs

    monkeypatch.setattr(scan_jobs.SubmissionIntake, "__init__", lambda *args, **kwargs: None)
    def fail(self, *args, **kwargs):
        raise ValueError("Exam version could not be matched.")
    monkeypatch.setattr(scan_jobs.SubmissionIntake, "extract", fail)
    batch = analyze(client, app, assignment_id, upload(client, assignment_id))
    url = batch_url(assignment_id, batch)
    assert client.post(url + "/process", json={"expected_revision": batch["revision"], "acknowledge_findings": True}).status_code == 202
    drain(app)
    batch = current(client, assignment_id, batch)
    assert batch["status"] == "failed"
    assert batch["started"]
    assert all(group["submission_id"] is None for group in batch["groups"])
    if published:
        destination = root / ".opengrader" / "assignments" / assignment_id / "submissions" / f"scan-{batch['id']}-{batch['groups'][0]['key']}"
        destination.mkdir()
        (destination / "work.submission.md").write_text("Student work")
    response = client.delete(url, params={"expected_revision": batch["revision"]})
    assert response.status_code == 204
    if published:
        assert not destination.exists()
    assert client.get(f"/assignments/{assignment_id}/scans").json()["batches"] == []


def test_partial_failure_retry_preserves_published_work_and_ids(workspace, monkeypatch):
    client, app, assignment_id, root = workspace
    from opengrader.server import scan_jobs

    calls = []
    def extract(intake, pdf, *args, **kwargs):
        calls.append((pdf.name, intake.assignment_version))
        if len(calls) == 2:
            raise ValueError("Unreadable page")
        return fake_extract(pdf, *args, **kwargs)
    monkeypatch.setattr(scan_jobs.SubmissionIntake, "__init__", lambda self, *args, assignment_version, **kwargs: setattr(self, "assignment_version", assignment_version))
    monkeypatch.setattr(scan_jobs.SubmissionIntake, "extract", lambda self, *args, **kwargs: extract(self, *args, **kwargs))
    batch = analyze(client, app, assignment_id, upload(client, assignment_id))
    url = batch_url(assignment_id, batch)
    assert client.post(url + "/process", json={"expected_revision": batch["revision"], "acknowledge_findings": True, "assignment_version": "A", "group_versions": {batch["groups"][1]["key"]: "B"}}).status_code == 202
    drain(app)
    failed = current(client, assignment_id, batch)
    assert failed["status"] == "failed"
    assert failed["groups"][0]["submission_id"] == 1
    first = next((root / ".opengrader" / "assignments" / assignment_id / "submissions").glob("*/*.submission.md"))
    answer_url = f"/assignments/{assignment_id}/submissions/1/answers/q1"
    answer = client.get(answer_url).json()
    from uuid import uuid4
    saved = client.put(answer_url, json={
        "request_id": str(uuid4()), "expected_revision": answer["revision"],
        "expected_context_revision": answer["context_revision"], "rubric_labels": [],
        "score_mode": "manual", "points_awarded": 1, "analysis": "Reviewed",
    })
    assert saved.status_code == 200
    before = first.read_bytes()
    assert client.post(url + "/process", json={"expected_revision": failed["revision"], "acknowledge_findings": True}).status_code == 202
    drain(app)
    ready = current(client, assignment_id, batch)
    assert ready["status"] == "ready"
    assert [version for _, version in calls] == ["A", "B", "B"]
    assert first.read_bytes() == before
    assert [group["submission_id"] for group in ready["groups"]] == [1, 2]
    assert client.post(url + "/analyze", json={"expected_revision": ready["revision"]}).status_code == 409
    removed = client.post(url + "/remove", json={"expected_revision": ready["revision"]}).json()
    assert removed["status"] == "removed"
    assert first.read_bytes() == before
    ready = client.post(url + "/restore", json={"expected_revision": removed["revision"]}).json()
    assert ready["status"] == "ready"
    later = analyze(client, app, assignment_id, upload(client, assignment_id, pdf_bytes(2), "late.pdf"))
    client.post(batch_url(assignment_id, later) + "/process", json={"expected_revision": later["revision"], "acknowledge_findings": True})
    drain(app)
    with TestClient(create_app(root)) as restarted:
        ids = restarted.get(f"/assignments/{assignment_id}/submissions").json()
        assert [item["id"] for item in ids] == [1, 2, 3]
        answer = restarted.get(f"/assignments/{assignment_id}/submissions/1/answers/q1").json()
        assert answer["points_awarded"] == 1
    assert client.delete(url, params={"expected_revision": ready["revision"]}).status_code == 204
    assert not first.exists()
    assert [item["id"] for item in client.get(f"/assignments/{assignment_id}/submissions").json()] == [3]
    assert client.get(f"/assignments/{assignment_id}/submissions/1/answers/q1").status_code == 404
    with TestClient(create_app(root)) as restarted:
        assert [item["id"] for item in restarted.get(f"/assignments/{assignment_id}/submissions").json()] == [3]


def test_accepted_job_does_not_block_requests_or_allow_duplicate_start(workspace, monkeypatch):
    client, app, assignment_id, _ = workspace
    from opengrader.server import scan_jobs

    entered, release = Event(), Event()
    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return fake_extract(*args, **kwargs)
    monkeypatch.setattr(scan_jobs.SubmissionIntake, "__init__", lambda *args, **kwargs: None)
    monkeypatch.setattr(scan_jobs.SubmissionIntake, "extract", lambda self, *args, **kwargs: held(*args, **kwargs))
    batch = analyze(client, app, assignment_id, upload(client, assignment_id))
    request = {"expected_revision": batch["revision"], "acknowledge_findings": True}
    try:
        assert client.post(batch_url(assignment_id, batch) + "/process", json=request).status_code == 202
        assert entered.wait(5)
        assert client.get("/assignments").status_code == 200
        assert client.post(batch_url(assignment_id, batch) + "/process", json=request).status_code == 409
        active = current(client, assignment_id, batch)
        assert active["status"] == "processing"
        assert client.delete(batch_url(assignment_id, active), params={"expected_revision": active["revision"]}).status_code == 409
        assert client.put(f"/assignments/{assignment_id}", json={"qa_markdown": QA, "expected_qa_markdown": QA}).status_code == 409
    finally:
        release.set()
    drain(app)
    complete = current(client, assignment_id, batch)
    assert complete["status"] == "ready"
    assert client.delete(batch_url(assignment_id, complete), params={"expected_revision": complete["revision"]}).status_code == 204
    assert client.get(batch_url(assignment_id, complete) + "/pdf").status_code == 404


def test_delete_rolls_back_submissions_if_scan_cannot_be_unpublished(workspace, monkeypatch):
    client, app, assignment_id, root = workspace
    from opengrader.server import scan_jobs

    monkeypatch.setattr(scan_jobs.SubmissionIntake, "__init__", lambda *args, **kwargs: None)
    monkeypatch.setattr(scan_jobs.SubmissionIntake, "extract", lambda self, *args, **kwargs: fake_extract(*args, **kwargs))
    batch = analyze(client, app, assignment_id, upload(client, assignment_id))
    url = batch_url(assignment_id, batch)
    assert client.post(url + "/process", json={"expected_revision": batch["revision"], "acknowledge_findings": True}).status_code == 202
    drain(app)
    batch = current(client, assignment_id, batch)
    directory = ScanStore(root / ".opengrader" / "assignments").directory(assignment_id, batch["id"])
    rename = Path.rename
    def fail_scan_rename(path, target):
        if path == directory:
            raise OSError("Storage unavailable")
        return rename(path, target)
    monkeypatch.setattr(Path, "rename", fail_scan_rename)
    assert client.delete(url, params={"expected_revision": batch["revision"]}).status_code == 500
    assert client.get(url + "/pdf").status_code == 200
    assert len(list((root / ".opengrader" / "assignments" / assignment_id / "submissions").glob("*/*.submission.md"))) == 2
    assert len(client.get(f"/assignments/{assignment_id}/submissions").json()) == 2


def test_restart_marks_interrupted_work_retryable(workspace):
    client, _, assignment_id, root = workspace
    batch = upload(client, assignment_id)
    store = ScanStore(root / ".opengrader" / "assignments")
    record = store.get(assignment_id, batch["id"])
    record.status = "checking"
    store.save(assignment_id, record)
    with TestClient(create_app(root)) as restarted:
        assert current(restarted, assignment_id, batch)["status"] == "failed"


def test_delete_assignment_removes_scans_submissions_and_grades_only_for_that_assignment(workspace, monkeypatch):
    client, app, assignment_id, root = workspace
    from opengrader.server import scan_jobs

    monkeypatch.setattr(scan_jobs.SubmissionIntake, "__init__", lambda *args, **kwargs: None)
    monkeypatch.setattr(scan_jobs.SubmissionIntake, "extract", lambda self, *args, **kwargs: fake_extract(*args, **kwargs))
    content = pdf_bytes()
    batch = analyze(client, app, assignment_id, upload(client, assignment_id, content))
    assert client.post(batch_url(assignment_id, batch) + "/process", json={"expected_revision": batch["revision"], "acknowledge_findings": True}).status_code == 202
    drain(app)
    directory = root / ".opengrader" / "assignments" / assignment_id
    first = next((directory / "submissions").glob("*/*.submission.md"))
    answer_url = f"/assignments/{assignment_id}/submissions/1/answers/q1"
    answer = client.get(answer_url).json()
    from uuid import uuid4
    saved = client.put(answer_url, json={
        "request_id": str(uuid4()), "expected_revision": answer["revision"],
        "expected_context_revision": answer["context_revision"], "rubric_labels": [],
        "score_mode": "manual", "points_awarded": 1, "analysis": "Reviewed",
    })
    assert saved.status_code == 200
    other = client.post("/assignments", json={"qa_markdown": QA}).json()["id"]
    other_batch = upload(client, other, content)
    assert client.put(f"/assignments/{assignment_id}", json={"qa_markdown": QA, "expected_qa_markdown": QA}).status_code == 409
    assert client.delete(f"/assignments/{assignment_id}").status_code == 204
    assert not directory.exists()
    assert client.get(f"/assignments/{assignment_id}/submissions").status_code == 404
    assert client.get(batch_url(assignment_id, batch) + "/pdf").status_code == 404
    assert client.get(batch_url(other, other_batch) + "/pdf").content == content
    replacement = client.post("/assignments", json={"qa_markdown": QA}).json()["id"]
    assert upload(client, replacement, content)["id"] != batch["id"]
    with TestClient(create_app(root)) as restarted:
        assert assignment_id not in {item["id"] for item in restarted.get("/assignments").json()}


def test_delete_assignment_stops_active_and_queued_work_before_removing_files(workspace, monkeypatch):
    client, app, assignment_id, root = workspace
    from opengrader.server import scan_jobs

    entered, release, stopping = Event(), Event(), Event()
    calls = []
    def held(self, pdf, output, **kwargs):
        calls.append(pdf)
        entered.set()
        assert release.wait(5)
        return fake_extract(pdf, output, **kwargs)
    monkeypatch.setattr(scan_jobs.SubmissionIntake, "__init__", lambda *args, **kwargs: None)
    monkeypatch.setattr(scan_jobs.SubmissionIntake, "extract", held)
    jobs = app.state.scan_jobs
    stop = jobs.stop_assignment
    def observed_stop(identifier):
        futures = stop(identifier)
        stopping.set()
        return futures
    monkeypatch.setattr(jobs, "stop_assignment", observed_stop)
    batch = analyze(client, app, assignment_id, upload(client, assignment_id))
    queued = analyze(client, app, assignment_id, upload(client, assignment_id, pdf_bytes(2)))
    assert client.post(batch_url(assignment_id, batch) + "/process", json={"expected_revision": batch["revision"], "acknowledge_findings": True}).status_code == 202
    assert entered.wait(5)
    assert client.post(batch_url(assignment_id, queued) + "/process", json={"expected_revision": queued["revision"], "acknowledge_findings": True}).status_code == 202
    with ThreadPoolExecutor(max_workers=1) as requests:
        deletion = requests.submit(client.delete, f"/assignments/{assignment_id}")
        try:
            assert stopping.wait(5)
            assert not deletion.done()
            assert client.get(f"/assignments/{assignment_id}/source").status_code == 409
            assert client.get("/assignments").status_code == 200
        finally:
            release.set()
        assert deletion.result(timeout=5).status_code == 204
    drain(app)
    assert len(calls) == 1
    assert not (root / ".opengrader" / "assignments" / assignment_id).exists()
    assert client.get(f"/assignments/{assignment_id}/submissions").status_code == 404


def test_upload_limits_and_invalid_pdf_leave_no_visible_batch(workspace, monkeypatch):
    client, _, assignment_id, root = workspace
    from opengrader.server import scans

    assert client.post(f"/assignments/{assignment_id}/scans?filename=bad.pdf", content=b"not pdf").status_code == 422
    monkeypatch.setattr(scans, "MAX_SCAN_BYTES", 10)
    assert client.post(f"/assignments/{assignment_id}/scans?filename=big.pdf", content=pdf_bytes()).status_code == 413
    assert client.get(f"/assignments/{assignment_id}/scans").json()["batches"] == []
    assert not list((root / ".opengrader" / "assignments").glob(".upload-*"))


def test_materialized_plan_preserves_source_and_page_ownership(tmp_path):
    source = tmp_path / "source.pdf"
    original = pdf_bytes()
    source.write_bytes(original)
    groups = [[ScanPage(3, 90), ScanPage(1)], [ScanPage(4)]]
    validate_scan_plan(4, groups, [2])
    output = tmp_path / "submission.pdf"
    write_scan_submission(source, output, groups[0])
    pages = PdfReader(output).pages
    assert "Source page 3" in pages[0].extract_text()
    assert pages[0].rotation == 90
    assert "Source page 1" in pages[1].extract_text()
    assert source.read_bytes() == original


def test_automatic_split_keeps_a_short_submission_and_preserves_its_warning(workspace, monkeypatch):
    from opengrader.core.submissions import boundary
    from opengrader.core.submissions.splitter import read_scan_warnings
    from opengrader.server.scan_jobs import SubmissionIntake

    client, app, assignment_id, root = workspace
    monkeypatch.setattr(boundary, "_analyze_pages", lambda *args, **kwargs: [
        (True, 1), (True, 3), (True, 1), (True, 2), (True, 3),
    ])
    batch = upload(client, assignment_id, pdf_bytes(5))
    url = batch_url(assignment_id, batch)
    response = client.post(url + "/analyze", json={
        "expected_revision": batch["revision"], "mode": "automatic", "pages_per_submission": 3,
    })
    assert response.status_code == 202
    drain(app)
    batch = current(client, assignment_id, batch)
    assert [[page["number"] for page in group["pages"]] for group in batch["groups"]] == [[1, 2], [3, 4, 5]]
    assert "2 of 3 expected pages" in batch["groups"][0]["scan_warnings"][0]
    assert "missing printed page(s) 2" in batch["groups"][0]["scan_warnings"][1]
    assert batch["groups"][1]["scan_warnings"] == []
    response = client.put(url + "/review", json={
        "expected_revision": batch["revision"], "groups": [group["pages"] for group in batch["groups"]],
    })
    assert response.status_code == 200
    batch = response.json()
    assert "missing printed page(s) 2" in batch["groups"][0]["scan_warnings"][1]
    seen = []
    def extract(_self, pdf, output, **kwargs):
        seen.append(read_scan_warnings(pdf))
        return fake_extract(pdf, output)
    monkeypatch.setattr(SubmissionIntake, "extract", extract)
    assert client.post(url + "/process", json={"expected_revision": batch["revision"], "acknowledge_findings": True}).status_code == 202
    drain(app)
    assert "2 of 3 expected pages" in seen[0][0]
    assert seen[1] == ()
    assert current(client, assignment_id, batch)["status"] == "ready"
