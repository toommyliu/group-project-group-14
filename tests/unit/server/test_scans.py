"""Intake boundaries: lossless plans, durable publication, and safe retries."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import fitz
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from opengrader.core.submissions.scan_plan import ScanPage, validate_scan_plan, write_scan_submission
from opengrader.server.workspace.app import create_app
from opengrader.server.scan_store import ScanStore


def pdf_bytes(pages: int = 4) -> bytes:
    with fitz.open() as document:
        for index in range(pages):
            page = document.new_page()
            page.insert_text((72, 72), f"Source page {index + 1}")
        return document.tobytes()


OUTLINE = {
    "name_box": {"page": 1, "bbox": [0.1, 0.05, 0.5, 0.05]},
    "id_box": None,
    "questions": [
        {"title": "What is two plus two?", "points": 2, "boxes": [{"page": 1, "bbox": [0.1, 0.2, 0.8, 0.3]}]},
        {"title": "Show your work", "points": 3, "boxes": [{"page": 2, "bbox": [0.1, 0.1, 0.8, 0.3]}]},
    ],
}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from opengrader.server import scan_jobs

    monkeypatch.setattr(scan_jobs, "ProcessPoolExecutor", lambda **_: ThreadPoolExecutor(max_workers=1))
    app = create_app(tmp_path)
    with TestClient(app) as client:
        result = client.post(
            "/api/assignments/from-template",
            params={"title": "Scan test", "filename": "template.pdf"},
            content=pdf_bytes(2),
        )
        assert result.status_code == 201, result.text
        assignment_id = result.json()["id"]
        outline = client.get(f"/api/assignments/{assignment_id}/outline").json()
        saved = client.put(
            f"/api/assignments/{assignment_id}/outline",
            json={"version": outline["version"], "revision": outline["revision"], "outline": OUTLINE},
        )
        assert saved.status_code == 200, saved.text
        yield client, app, assignment_id, tmp_path


def source(client, assignment_id):
    return client.get(f"/api/assignments/{assignment_id}/source").json()["qa_markdown"]


def edit(client, assignment_id):
    qa = source(client, assignment_id)
    return client.put(f"/api/assignments/{assignment_id}", json={"qa_markdown": qa, "expected_qa_markdown": qa})


def upload(client, assignment_id, content=None, name="scans.pdf"):
    response = client.post(f"/api/assignments/{assignment_id}/scans", params={"filename": name}, content=content or pdf_bytes())
    assert response.status_code == 200, response.text
    return response.json()


def batch_url(assignment_id, batch):
    return f"/api/assignments/{assignment_id}/scans/{batch['id']}"


def current(client, assignment_id, batch):
    return next(item for item in client.get(f"/api/assignments/{assignment_id}/scans").json()["batches"] if item["id"] == batch["id"])


def drain(app):
    # A barrier in the single-worker queue proves all earlier accepted work finished.
    app.state.scan_jobs.executor.submit(lambda: None).result(timeout=10)


def analyze(client, app, assignment_id, batch, pages=2):
    result = client.post(batch_url(assignment_id, batch) + "/analyze", json={"expected_revision": batch["revision"], "mode": "fixed", "pages_per_submission": pages})
    assert result.status_code == 202, result.text
    drain(app)
    result = current(client, assignment_id, batch)
    assert result["status"] == "review", result
    return result


def process(client, app, assignment_id, batch):
    response = client.post(batch_url(assignment_id, batch) + "/process", json={"expected_revision": batch["revision"], "acknowledge_findings": True})
    assert response.status_code == 202, response.text
    drain(app)
    return current(client, assignment_id, batch)


def test_upload_is_deduplicated_and_retains_original(workspace):
    client, app, assignment_id, root = workspace
    content = pdf_bytes()
    first = upload(client, assignment_id, content)
    again = upload(client, assignment_id, content, "renamed.pdf")
    assert first["id"] == again["id"]
    assert client.get(batch_url(assignment_id, first) + "/pdf").content == content
    assert client.get(batch_url(assignment_id, first) + "/pages/1").headers["content-type"] == "image/png"
    assert client.get(batch_url(assignment_id, first) + "/pages/5").status_code == 404
    assert edit(client, assignment_id).status_code == 409
    other = client.post("/api/assignments/from-template", params={"title": "Other", "filename": "t.pdf"}, content=pdf_bytes(1)).json()["id"]
    assert client.get(batch_url(other, first) + "/pdf").status_code == 404
    with TestClient(create_app(root)) as restarted:
        assert current(restarted, assignment_id, first)["sha256"] == first["sha256"]


def test_split_uses_page_counts_and_flags_short_submissions(workspace):
    client, app, assignment_id, _ = workspace
    batch = analyze(client, app, assignment_id, upload(client, assignment_id, pdf_bytes(5)))
    assert [[page["number"] for page in group["pages"]] for group in batch["groups"]] == [[1, 2], [3, 4], [5]]
    assert [bool(group["scan_warnings"]) for group in batch["groups"]] == [False, False, True]
    assert "1 of 2 expected pages" in batch["groups"][2]["scan_warnings"][0]
    whole = upload(client, assignment_id, pdf_bytes(3), "whole.pdf")
    response = client.post(batch_url(assignment_id, whole) + "/analyze", json={"expected_revision": whole["revision"], "mode": "individual"})
    assert response.status_code == 202
    drain(app)
    assert [[page["number"] for page in group["pages"]] for group in current(client, assignment_id, whole)["groups"]] == [[1, 2, 3]]
    stale = client.post(batch_url(assignment_id, whole) + "/analyze", json={"expected_revision": whole["revision"], "mode": "automatic"})
    assert stale.status_code == 422


def test_processing_crops_each_region_without_reading_text(workspace):
    client, app, assignment_id, root = workspace
    batch = process(client, app, assignment_id, analyze(client, app, assignment_id, upload(client, assignment_id)))
    assert batch["status"] == "ready", batch
    submissions = client.get(f"/api/assignments/{assignment_id}/submissions").json()
    assert [item["id"] for item in submissions] == [1, 2]
    assert submissions[0]["name_image_url"].startswith("/api/assets/")
    assert submissions[0]["id_image_url"] is None
    for question in ("q1", "q2"):
        answer = client.get(f"/api/assignments/{assignment_id}/submissions/1/answers/{question}").json()
        assert answer["response_text"] == ""
        assert answer["response_image_width"] > answer["response_image_height"] > 0
        assert client.get(answer["response_image_url"]).headers["content-type"] == "image/png"
    named = client.put(
        f"/api/assignments/{assignment_id}/submissions/2/student",
        json={"student_name": "  Ada   Lovelace ", "student_id": " 0042 "},
    )
    assert named.status_code == 200
    assert (named.json()["student_name"], named.json()["student_id"]) == ("Ada Lovelace", "0042")
    with TestClient(create_app(root)) as restarted:
        listed = restarted.get(f"/api/assignments/{assignment_id}/submissions").json()
        assert listed[1]["student_name"] == "Ada Lovelace"


def test_review_rejects_lost_pages_duplicates_and_stale_writes(workspace):
    client, app, assignment_id, _ = workspace
    batch = analyze(client, app, assignment_id, upload(client, assignment_id))
    url = batch_url(assignment_id, batch) + "/groups"
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
    assert client.get(f"/api/assignments/{assignment_id}/scans").json()["batches"] == []
    assert edit(client, assignment_id).status_code == 200
    replacement = upload(client, assignment_id, content)
    assert replacement["id"] != batch["id"]
    assert client.delete(batch_url(assignment_id, replacement), params={"expected_revision": replacement["revision"]}).status_code == 204
    assert client.delete(f"/api/assignments/{assignment_id}").status_code == 204


@pytest.mark.parametrize("published", [False, True])
def test_failed_scan_deletion_includes_published_files_even_without_recorded_ids(workspace, monkeypatch, published):
    client, app, assignment_id, root = workspace
    from opengrader.server import scan_jobs

    def fail(*args, **kwargs):
        raise ValueError("Unreadable scan.")
    monkeypatch.setattr(scan_jobs, "crop_submission", fail)
    batch = process(client, app, assignment_id, analyze(client, app, assignment_id, upload(client, assignment_id)))
    url = batch_url(assignment_id, batch)
    assert batch["status"] == "failed"
    assert batch["started"]
    assert all(group["submission_id"] is None for group in batch["groups"])
    assert batch["groups"][0]["error"] == "Unreadable scan."
    if published:
        destination = root / ".opengrader" / "assignments" / assignment_id / "submissions" / f"scan-{batch['id']}-{batch['groups'][0]['key']}"
        destination.mkdir()
        (destination / "work.submission.md").write_text("Student work")
    response = client.delete(url, params={"expected_revision": batch["revision"]})
    assert response.status_code == 204
    if published:
        assert not destination.exists()
    assert client.get(f"/api/assignments/{assignment_id}/scans").json()["batches"] == []


def test_partial_failure_retry_preserves_published_work_and_ids(workspace, monkeypatch):
    client, app, assignment_id, root = workspace
    from opengrader.server import scan_jobs

    calls = []
    crop = scan_jobs.crop_submission
    def flaky(pdf, *args, **kwargs):
        calls.append(pdf.name)
        if len(calls) == 2:
            raise ValueError("Unreadable page")
        return crop(pdf, *args, **kwargs)
    monkeypatch.setattr(scan_jobs, "crop_submission", flaky)
    batch = analyze(client, app, assignment_id, upload(client, assignment_id))
    url = batch_url(assignment_id, batch)
    failed = process(client, app, assignment_id, batch)
    assert failed["status"] == "failed"
    assert failed["groups"][0]["submission_id"] == 1
    first = next((root / ".opengrader" / "assignments" / assignment_id / "submissions").glob("*/*.submission.md"))
    answer_url = f"/api/assignments/{assignment_id}/submissions/1/answers/q1"
    answer = client.get(answer_url).json()
    from uuid import uuid4
    saved = client.put(answer_url, json={
        "request_id": str(uuid4()), "expected_revision": answer["revision"],
        "expected_context_revision": answer["context_revision"], "rubric_labels": [],
        "score_mode": "manual", "points_awarded": 1, "analysis": "Reviewed",
    })
    assert saved.status_code == 200
    before = first.read_bytes()
    ready = process(client, app, assignment_id, failed)
    assert ready["status"] == "ready"
    assert len(calls) == 3 and calls[1] == calls[2]
    assert first.read_bytes() == before
    assert [group["submission_id"] for group in ready["groups"]] == [1, 2]
    assert client.post(url + "/analyze", json={"expected_revision": ready["revision"]}).status_code == 409
    removed = client.post(url + "/remove", json={"expected_revision": ready["revision"]}).json()
    assert removed["status"] == "removed"
    assert first.read_bytes() == before
    ready = client.post(url + "/restore", json={"expected_revision": removed["revision"]}).json()
    assert ready["status"] == "ready"
    process(client, app, assignment_id, analyze(client, app, assignment_id, upload(client, assignment_id, pdf_bytes(2), "late.pdf")))
    with TestClient(create_app(root)) as restarted:
        ids = restarted.get(f"/api/assignments/{assignment_id}/submissions").json()
        assert [item["id"] for item in ids] == [1, 2, 3]
        answer = restarted.get(f"/api/assignments/{assignment_id}/submissions/1/answers/q1").json()
        assert answer["points_awarded"] == 1
    assert client.delete(url, params={"expected_revision": ready["revision"]}).status_code == 204
    assert not first.exists()
    assert [item["id"] for item in client.get(f"/api/assignments/{assignment_id}/submissions").json()] == [3]
    assert client.get(f"/api/assignments/{assignment_id}/submissions/1/answers/q1").status_code == 404
    with TestClient(create_app(root)) as restarted:
        assert [item["id"] for item in restarted.get(f"/api/assignments/{assignment_id}/submissions").json()] == [3]


def test_scans_uploaded_before_the_outline_wait_for_its_regions(workspace):
    client, app, _, _ = workspace
    bare = client.post("/api/assignments/from-template", params={"title": "Bare", "filename": "t.pdf"}, content=pdf_bytes(2)).json()["id"]
    batch = analyze(client, app, bare, upload(client, bare))
    response = client.post(batch_url(bare, batch) + "/process", json={"expected_revision": batch["revision"], "acknowledge_findings": True})
    assert response.status_code == 422
    assert response.json()["detail"] == "Draw the outline before creating submissions."
    assert current(client, bare, batch)["status"] == "review"
    outline = client.get(f"/api/assignments/{bare}/outline").json()
    assert outline["lock"] is None
    assert outline["versions_with_regions"] == []
    nan = client.put(
        f"/api/assignments/{bare}/outline",
        content='{"version": "", "revision": "%s", "outline": {"questions": [{"title": "", "points": 1, "boxes": [{"page": 1, "bbox": [NaN, 0.1, 0.2, 0.2]}]}]}}' % outline["revision"],
        headers={"content-type": "application/json"},
    )
    assert nan.status_code == 422
    assert nan.json()["detail"] == "Question 1 has a box with an invalid position."
    saved = client.put(f"/api/assignments/{bare}/outline", json={"version": "", "revision": outline["revision"], "outline": OUTLINE})
    assert saved.status_code == 200, saved.text
    assert saved.json()["versions_with_regions"] == [""]
    assert process(client, app, bare, current(client, bare, batch))["status"] == "ready"
    assert client.get(f"/api/assignments/{bare}/outline").json()["lock"]["scope"] == "questions"


def test_moved_boxes_crop_again_after_a_restart_a_failure_and_a_deleted_scan(workspace, monkeypatch):
    client, app, assignment_id, root = workspace
    from opengrader.server import scan_jobs

    batch = process(client, app, assignment_id, analyze(client, app, assignment_id, upload(client, assignment_id)))
    url = f"/api/assignments/{assignment_id}/outline"

    def move(height):
        current = client.get(url).json()
        outline = current["outline"]
        outline["questions"][1]["boxes"][0]["bbox"][3] = height
        return client.put(url, json={"version": "", "revision": current["revision"], "outline": outline})

    def heights(api=client):
        return [
            api.get(f"/api/assignments/{assignment_id}/submissions/{number}/answers/q2").json()["response_image_height"]
            for number in (1, 2)
        ]

    original = heights()
    paused = Event()
    app.state.scan_jobs.executor.submit(paused.wait, 30)
    try:
        saved = move(0.6)
        assert saved.status_code == 200, saved.text
        assert (saved.json()["lock"]["scope"], saved.json()["recut"]) == ("outline", {"done": 0, "remaining": 2, "failures": []})
        with TestClient(create_app(root)) as restarted:
            drain(restarted.app)
            assert restarted.get(url).json()["recut"] is None
            assert [round(new / old, 2) for new, old in zip(heights(restarted), original)] == [2.0, 2.0]
    finally:
        paused.set()
    drain(app)

    calls = []
    crop = scan_jobs.crop_submission

    def unreadable_once(*args):
        calls.append(args)
        if len(calls) == 1:
            raise ValueError("Could not read this scan.")
        return crop(*args)

    monkeypatch.setattr(scan_jobs, "crop_submission", unreadable_once)
    assert move(0.45).status_code == 200
    drain(app)
    failed = client.get(url).json()
    assert (failed["lock"]["scope"], failed["recut"]) == (
        "questions",
        {"done": 1, "remaining": 0, "failures": [{"submission_id": 1, "student_name": "", "error": "Could not read this scan."}]},
    )
    assert sorted(round(new / old, 2) for new, old in zip(heights(), original)) == [1.5, 2.0]
    retried = client.post(url + "/recut")
    assert retried.status_code == 200, retried.text
    drain(app)
    assert client.get(url).json()["recut"] is None
    assert [round(new / old, 2) for new, old in zip(heights(), original)] == [1.5, 1.5]

    paused = Event()
    app.state.scan_jobs.executor.submit(paused.wait, 30)
    try:
        assert move(0.3).status_code == 200
        batch = current(client, assignment_id, batch)
        assert client.delete(batch_url(assignment_id, batch), params={"expected_revision": batch["revision"]}).status_code == 204
        assert client.get(url).json()["lock"] is None
    finally:
        paused.set()
    drain(app)
    assert client.get(f"/api/assignments/{assignment_id}/submissions").json() == []


def test_accepted_job_does_not_block_requests_or_allow_duplicate_start(workspace, monkeypatch):
    client, app, assignment_id, _ = workspace
    from opengrader.server import scan_jobs

    entered, release = Event(), Event()
    crop = scan_jobs.crop_submission
    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return crop(*args, **kwargs)
    monkeypatch.setattr(scan_jobs, "crop_submission", held)
    batch = analyze(client, app, assignment_id, upload(client, assignment_id))
    request = {"expected_revision": batch["revision"], "acknowledge_findings": True}
    try:
        assert client.post(batch_url(assignment_id, batch) + "/process", json=request).status_code == 202
        assert entered.wait(5)
        assert client.get("/api/assignments").status_code == 200
        assert client.post(batch_url(assignment_id, batch) + "/process", json=request).status_code == 409
        active = current(client, assignment_id, batch)
        assert active["status"] == "processing"
        assert client.delete(batch_url(assignment_id, active), params={"expected_revision": active["revision"]}).status_code == 409
        assert edit(client, assignment_id).status_code == 409
    finally:
        release.set()
    drain(app)
    complete = current(client, assignment_id, batch)
    assert complete["status"] == "ready"
    assert client.delete(batch_url(assignment_id, complete), params={"expected_revision": complete["revision"]}).status_code == 204
    assert client.get(batch_url(assignment_id, complete) + "/pdf").status_code == 404


def test_delete_rolls_back_submissions_if_scan_cannot_be_unpublished(workspace, monkeypatch):
    client, app, assignment_id, root = workspace
    batch = process(client, app, assignment_id, analyze(client, app, assignment_id, upload(client, assignment_id)))
    url = batch_url(assignment_id, batch)
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
    assert len(client.get(f"/api/assignments/{assignment_id}/submissions").json()) == 2


def test_restart_marks_interrupted_work_retryable(workspace):
    client, _, assignment_id, root = workspace
    batch = upload(client, assignment_id)
    store = ScanStore(root / ".opengrader" / "assignments")
    record = store.get(assignment_id, batch["id"])
    record.status = "checking"
    store.save(assignment_id, record)
    with TestClient(create_app(root)) as restarted:
        assert current(restarted, assignment_id, batch)["status"] == "failed"


def test_delete_assignment_removes_scans_submissions_and_grades_only_for_that_assignment(workspace):
    client, app, assignment_id, root = workspace
    content = pdf_bytes()
    batch = process(client, app, assignment_id, analyze(client, app, assignment_id, upload(client, assignment_id, content)))
    directory = root / ".opengrader" / "assignments" / assignment_id
    answer_url = f"/api/assignments/{assignment_id}/submissions/1/answers/q1"
    answer = client.get(answer_url).json()
    from uuid import uuid4
    saved = client.put(answer_url, json={
        "request_id": str(uuid4()), "expected_revision": answer["revision"],
        "expected_context_revision": answer["context_revision"], "rubric_labels": [],
        "score_mode": "manual", "points_awarded": 1, "analysis": "Reviewed",
    })
    assert saved.status_code == 200
    other = client.post("/api/assignments/from-template", params={"title": "Other", "filename": "t.pdf"}, content=pdf_bytes(2)).json()["id"]
    other_batch = upload(client, other, content)
    assert edit(client, assignment_id).status_code == 409
    assert client.delete(f"/api/assignments/{assignment_id}").status_code == 204
    assert not directory.exists()
    assert client.get(f"/api/assignments/{assignment_id}/submissions").status_code == 404
    assert client.get(f"/api/assignments/{assignment_id}/outline").status_code == 404
    assert client.get(batch_url(assignment_id, batch) + "/pdf").status_code == 404
    assert client.get(batch_url(other, other_batch) + "/pdf").content == content
    replacement = client.post("/api/assignments/from-template", params={"title": "Again", "filename": "t.pdf"}, content=pdf_bytes(2)).json()["id"]
    assert upload(client, replacement, content)["id"] != batch["id"]
    with TestClient(create_app(root)) as restarted:
        assert assignment_id not in {item["id"] for item in restarted.get("/api/assignments").json()}


def test_delete_assignment_stops_active_and_queued_work_before_removing_files(workspace, monkeypatch):
    client, app, assignment_id, root = workspace
    from opengrader.server import scan_jobs

    entered, release, stopping = Event(), Event(), Event()
    calls = []
    crop = scan_jobs.crop_submission
    def held(pdf, *args, **kwargs):
        calls.append(pdf)
        entered.set()
        assert release.wait(5)
        return crop(pdf, *args, **kwargs)
    monkeypatch.setattr(scan_jobs, "crop_submission", held)
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
        deletion = requests.submit(client.delete, f"/api/assignments/{assignment_id}")
        try:
            assert stopping.wait(5)
            assert not deletion.done()
            assert client.get(f"/api/assignments/{assignment_id}/source").status_code == 409
            assert client.get("/api/assignments").status_code == 200
        finally:
            release.set()
        assert deletion.result(timeout=5).status_code == 204
    drain(app)
    assert len(calls) == 1
    assert not (root / ".opengrader" / "assignments" / assignment_id).exists()
    assert client.get(f"/api/assignments/{assignment_id}/submissions").status_code == 404


def test_upload_limits_and_invalid_pdf_leave_no_visible_batch(workspace, monkeypatch):
    client, _, assignment_id, root = workspace
    from opengrader.server import scans

    assert client.post(f"/api/assignments/{assignment_id}/scans?filename=bad.pdf", content=b"not pdf").status_code == 422
    monkeypatch.setattr(scans, "MAX_SCAN_BYTES", 10)
    assert client.post(f"/api/assignments/{assignment_id}/scans?filename=big.pdf", content=pdf_bytes()).status_code == 413
    assert client.get(f"/api/assignments/{assignment_id}/scans").json()["batches"] == []
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
