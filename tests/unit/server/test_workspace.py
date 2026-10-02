from concurrent.futures import ThreadPoolExecutor
import shutil
import sqlite3
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from opengrader.core.grading.edits import GradeEdit
from opengrader.core.workspace.store import Workspace, WorkspaceConflict
from opengrader.core.workspace.intake import publish_submission
from opengrader.core.submissions import load_student_submission_markdown
from opengrader.server.workspace.app import create_app
from opengrader.server.workspace.jobs import FeedbackJobs


@pytest.fixture
def managed(tmp_path):
    source = tmp_path / "original"
    source.mkdir()
    qa = source / "qa.md"
    qa.write_text(
        "- (10 points) First question\n  - **Answer**\n    - Yes\n- (2 points) Second question\n  - **Answer**\n    - No\n"
    )
    submission = source / "one.submission.md"
    submission.write_text("""---
student_id: '123'
student_name: Alice
---
- q1:
  - Response text: Yes
  - Possible points: 10
  - Points: 7
  - Analysis: Extracted feedback
- q2:
  - Response text: No
  - Possible points: 2
  - Points:
""")
    workspace = Workspace.create(tmp_path / "managed", actor="tommy")
    assignment = workspace.create_assignment(qa.read_text())
    publish_submission(
        workspace,
        assignment.id,
        load_student_submission_markdown(submission),
        asset_root=submission.parent,
        source_key="one",
    )
    return workspace, assignment.id, submission


def command(answer, **changes):
    return GradeEdit(
        request_id=uuid4(),
        expected_revision=answer.revision,
        expected_context_revision=answer.context_revision,
        rubric_labels=["Correct"],
        analysis="Saved feedback",
        **changes,
    )


def test_assignments_stay_in_creation_order_after_editing_or_deleting(tmp_path):
    workspace = Workspace.create(tmp_path)
    source = "---\ntitle: Z exam\n---\n- (2 points) Question\n"
    older = workspace.create_assignment(source)
    newer = workspace.create_assignment(
        "---\ntitle: A exam\n---\n- (3 points) Another question\n"
    )
    workspace.update_assignment(
        older.id, source.replace("Z exam", "Edited exam"), source
    )
    assert [item.id for item in Workspace(tmp_path).assignments()] == [
        older.id,
        newer.id,
    ]
    workspace.delete_assignment(newer.id)
    assert [item.id for item in workspace.assignments()] == [older.id]


def test_pipeline_publication_reuses_assignment_and_preserves_grades(managed):
    workspace, original, source = managed
    url = f"/api/assignments/{workspace.assignment_id_for_folder(source.parent)}/imports"
    body = {
        "qa_markdown": workspace.assignment_source(original),
        "submissions_dir": str(source.parent.resolve()),
    }
    with TestClient(create_app(workspace.root, process_jobs=False)) as client:
        first = client.post(url, json=body)
        assert first.status_code == 200, first.text
        assignment = first.json()["assignment"]["id"]
        submission = workspace.submissions(assignment)[0].id
        workspace.save_grade(
            assignment,
            submission,
            "q1",
            command(workspace.answer(assignment, submission, "q1")),
        )
        cursor = workspace.info().event_cursor
        extracted = source.read_text()
        source.write_text("invalid output from a later run")
        retried = client.post(url, json=body)
        assert retried.json() == first.json()
        assert workspace.info().event_cursor == cursor
        assert (
            workspace.answer(assignment, submission, "q1").analysis == "Saved feedback"
        )
        assert len(workspace.assignments()) == 2

        (source.parent / "two.submission.md").write_text(
            extracted.replace("Alice", "Bob")
        )
        assert (
            client.post(url, json=body).json()["submission_count"]
            == 2
        )
        assert len(workspace.submissions(assignment)) == 2
        assert (
            workspace.root / ".opengrader/assignments" / assignment / "qa.md"
        ).read_text() == body["qa_markdown"]

        changed_key = {
            **body,
            "qa_markdown": body["qa_markdown"].replace(
                "First question", "Different question"
            ),
        }
        assert client.post(url, json=changed_key).status_code == 409
        assert workspace.assignment_source(assignment) == body["qa_markdown"]


def test_pipeline_can_retry_after_partially_publishing(managed):
    workspace, original, source = managed
    extracted = source.read_text()
    broken = source.parent / "two.submission.md"
    broken.write_text("not a submission")
    url = f"/api/assignments/{workspace.assignment_id_for_folder(source.parent)}/imports"
    body = {
        "qa_markdown": workspace.assignment_source(original),
        "submissions_dir": str(source.parent.resolve()),
    }
    with TestClient(create_app(workspace.root, process_jobs=False)) as client:
        failed = client.post(url, json=body)
        assert failed.status_code == 422, failed.text
        assignment = workspace.assignments()[-1].id
        first = workspace.submissions(assignment)[0].id
        workspace.save_grade(
            assignment, first, "q1", command(workspace.answer(assignment, first, "q1"))
        )
        broken.write_text(extracted)
        retried = client.post(url, json=body)
        assert retried.status_code == 200, retried.text
        assert retried.json()["assignment"]["id"] == assignment
        assert retried.json()["submission_count"] == 2
        assert workspace.submissions(assignment)[0].id == first
        assert workspace.answer(assignment, first, "q1").analysis == "Saved feedback"


def test_published_grades_are_independent_of_extraction_files(managed):
    workspace, assignment, source = managed
    before = source.read_bytes()
    answer = workspace.answer(assignment, 1, "q1")
    assert answer.points_awarded == 7
    workspace.save_grade(assignment, 1, "q1", command(answer))
    assert source.read_bytes() == before
    source.write_text("invalid edited original")
    restarted = Workspace(workspace.root)
    assert restarted.answer(assignment, 1, "q1").points_awarded == 10
    assert restarted.submissions(assignment)[0].question_scores == {
        "q1": 10,
        "q2": None,
    }


def test_receipt_recovers_lost_acknowledgment_even_after_another_save(managed):
    workspace, assignment, _ = managed
    request = command(workspace.answer(assignment, 1, "q1"))
    first = workspace.save_grade(assignment, 1, "q1", request)
    later = command(first.answer).model_copy(update={"rubric_labels": ["Incorrect"]})
    workspace.save_grade(assignment, 1, "q1", later)
    restarted = Workspace(workspace.root)
    assert restarted.save_grade(assignment, 1, "q1", request) == first
    assert restarted.answer(assignment, 1, "q1").points_awarded == 0
    assert len(restarted.history(assignment)) == 2
    assert len([event for event in restarted.events() if event.kind == "grade"]) == 2
    assert restarted.jobs()[0].generation == 2
    with pytest.raises(WorkspaceConflict):
        restarted.save_grade(
            assignment,
            1,
            "q1",
            request.model_copy(update={"analysis": "different payload"}),
        )


def test_save_rolls_back_grade_history_events_and_job_when_receipt_fails(managed):
    workspace, assignment, _ = managed
    answer = workspace.answer(assignment, 1, "q1")
    with workspace.connect(write=True) as db:
        db.execute(
            "CREATE TRIGGER reject_receipt BEFORE INSERT ON receipts BEGIN SELECT RAISE(ABORT, 'disk failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="disk failure"):
        workspace.save_grade(assignment, 1, "q1", command(answer))
    assert workspace.answer(assignment, 1, "q1") == answer
    assert workspace.history(assignment) == []
    assert [event for event in workspace.events() if event.kind == "grade"] == []
    assert workspace.jobs() == []


@pytest.mark.parametrize(
    "questions,expected", [(("q1", "q1"), [200, 409]), (("q1", "q2"), [200, 200])]
)
def test_concurrent_clients_conflict_only_on_the_same_answer(
    managed, questions, expected
):
    workspace, assignment, _ = managed
    barrier = Barrier(2)
    requests = [
        (question, command(workspace.answer(assignment, 1, question)))
        for question in questions
    ]

    def save(item):
        question, request = item
        independent = Workspace(workspace.root)
        barrier.wait(timeout=5)
        try:
            independent.save_grade(assignment, 1, question, request)
            return 200
        except WorkspaceConflict:
            return 409

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, requests)) == expected
    assert len(workspace.history(assignment)) == expected.count(200)


def test_api_returns_current_answer_for_conflict_resolution(managed):
    workspace, assignment, _ = managed
    path = f"/api/assignments/{assignment}/submissions/1/answers/q1"
    with TestClient(create_app(workspace.root, process_jobs=False)) as client:
        original = workspace.answer(assignment, 1, "q1")
        request = command(original)
        assert client.put(path, json=request.model_dump(mode="json")).status_code == 200
        result = client.put(path, json=command(original).model_dump(mode="json"))
        assert result.status_code == 409
        assert result.json()["current"]["revision"] == "1"
        assert workspace.history(assignment)[0]["actor"] == "tommy"


def test_feedback_failure_does_not_change_grade_and_old_job_cannot_publish(
    managed, monkeypatch
):
    workspace, assignment, _ = managed
    first = workspace.save_grade(
        assignment, 1, "q1", command(workspace.answer(assignment, 1, "q1"))
    )
    jobs = FeedbackJobs(workspace)
    jobs.process(workspace.jobs()[0])
    assert workspace.jobs()[0].status == "failed"
    assert workspace.answer(assignment, 1, "q1") == first.answer
    workspace.save_grade(assignment, 1, "q1", command(first.answer))
    second = workspace.jobs()[0]

    def render(_job):
        workspace.save_grade(
            assignment, 1, "q1", command(workspace.answer(assignment, 1, "q1"))
        )
        return workspace.root / ".opengrader/feedback/old.pdf"

    monkeypatch.setattr(jobs, "_render", render)
    jobs.process(second)
    current = workspace.jobs()[0]
    assert current.generation == 3
    assert current.status == "pending"
    assert current.artifact is None


def test_submission_assets_survive_workspace_move(tmp_path):
    from PIL import Image

    image = tmp_path / "answer.png"
    Image.new("RGB", (10, 20)).save(image)
    qa = tmp_path / "qa.md"
    qa.write_text("- (1 point) First\n  - **Answer**\n    - Yes\n")
    submission = tmp_path / "one.submission.md"
    submission.write_text(
        f"---\n---\n- q1:\n  - Response text: Yes\n  - Response image: {image}\n  - Points: 0\n"
    )
    workspace = Workspace.create(tmp_path / "before")
    assignment = workspace.create_assignment(qa.read_text())
    publish_submission(
        workspace,
        assignment.id,
        load_student_submission_markdown(submission),
        asset_root=submission.parent,
        source_key="one",
    )
    assert workspace.answer(assignment.id, 1, "q1").response_image_width == 10
    shutil.move(workspace.root, tmp_path / "after")
    image.unlink()
    moved = Workspace(tmp_path / "after")
    document = moved.document(assignment.id, 1)
    assert moved.asset(document.questions[0].response_image_path).is_file()


def test_missing_extraction_asset_does_not_publish_partial_submission(managed):
    from dataclasses import replace

    workspace, assignment, _ = managed
    document = workspace.document(assignment, 1)
    document = replace(
        document,
        questions=[replace(document.questions[0], response_image_path="missing.png")],
    )
    with pytest.raises(ValueError, match="extraction asset is missing"):
        publish_submission(
            workspace,
            assignment,
            document,
            asset_root=workspace.root,
            source_key="broken-scan",
        )
    assert len(workspace.submissions(assignment)) == 1


def test_terminal_recovers_lost_response_without_saving_twice(managed):
    from opengrader.server.workspace.client import (
        ServiceConnection,
        ServiceError,
        WorkspaceClient,
    )
    from opengrader.tui.data.workspace_provider import WorkspaceProvider

    workspace, assignment, _ = managed
    with TestClient(create_app(workspace.root, process_jobs=False)) as http:

        class Client(WorkspaceClient):
            lose_response = True

            def request(self, method, path, body=None, **kwargs):
                response = http.request(
                    method, path, json=body.model_dump(mode="json") if body else None
                )
                if method == "PUT" and self.lose_response:
                    self.lose_response = False
                    raise ServiceError(0, "Connection dropped after commit")
                assert response.status_code < 400, response.text
                return response.content

        client = Client(
            ServiceConnection(
                root=str(workspace.root),
                url="http://testserver",
                pid=0,
                workspace_id=workspace.info().id,
            )
        )
        provider = WorkspaceProvider(client, workspace.assignments()[0])
        provider.build_question_seeds(student_item_id="s1")
        provider.stage_question_analysis(
            student_item_id="s1", question_id="q1", analysis="Recovered feedback"
        )
        with pytest.raises(ServiceError):
            provider.save_question_grade(
                student_item_id="s1",
                question_id="q1",
                possible_points=10,
                points_awarded=10,
                rubric_labels=("Correct",),
            )
        pending_id = provider.journal.value.answers["1/q1"].pending.request_id
        provider.close()
        resumed = WorkspaceProvider(client, workspace.assignments()[0])
        try:
            seed = resumed.build_question_seeds(student_item_id="s1")[0]
            assert seed.analysis == "Recovered feedback"
            assert (
                resumed.journal.value.answers["1/q1"].pending.request_id == pending_id
            )
            resumed.save_question_grade(
                student_item_id="s1",
                question_id="q1",
                possible_points=10,
                points_awarded=10,
                rubric_labels=("Correct",),
            )
            assert not resumed.journal.value.answers
            assert len(workspace.history(assignment)) == 1
        finally:
            resumed.close()


def test_concurrent_terminal_sessions_do_not_take_each_others_drafts(managed):
    from opengrader.server.workspace.drafts import Draft, DraftJournal

    workspace, assignment, _ = managed
    directory = workspace.root / "drafts"
    first = DraftJournal(directory, assignment)
    answer = workspace.answer(assignment, 1, "q1")
    first.value.answers["1/q1"] = Draft(base=answer, edit=command(answer))
    first.write()
    second = DraftJournal(directory, assignment)
    try:
        assert second.path != first.path
        assert not second.value.answers
        first.close()
        recovered = DraftJournal(directory, assignment)
        try:
            assert recovered.path == first.path
            assert recovered.value == first.value
        finally:
            recovered.close()
    finally:
        first.close()
        second.close()


def test_launchers_share_service_and_events_resume_after_restart(tmp_path):
    from queue import Queue
    import time

    from opengrader.core.workspace.models import Assignment, WorkspaceInfo
    from opengrader.server.assignment_qa import AssignmentInput
    from opengrader.server.workspace.client import WorkspaceClient, ensure_service

    root = tmp_path / "service"
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(ensure_service, [root, root]))
    assert first.pid == second.pid
    client = WorkspaceClient(first)
    cursor = WorkspaceInfo.model_validate_json(
        client.request("GET", "/api/workspace")
    ).event_cursor
    events = Queue()
    stop = client.observe(cursor, events.put)

    def shutdown(connection):
        WorkspaceClient(connection).request("POST", "/api/shutdown")
        deadline = time.monotonic() + 10
        while (
            root / ".opengrader/service.json"
        ).exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not (root / ".opengrader/service.json").exists()

    active = first
    replay_stop = lambda: None
    try:
        created = Assignment.model_validate_json(
            client.request(
                "POST",
                "/api/assignments",
                AssignmentInput(
                    qa_markdown="- (1 point) Question\n  - **Answer**\n    - Yes\n"
                ),
            )
        )
        event = events.get(timeout=5)
        assert event.assignment_id == created.id
        assert event.kind == "assignment"
        shutdown(first)
        restarted = ensure_service(root)
        active = restarted
        assert restarted.url == first.url
        another = Assignment.model_validate_json(
            WorkspaceClient(restarted).request(
                "POST",
                "/api/assignments",
                AssignmentInput(qa_markdown="- (2 points) Another question\n"),
            )
        )
        resumed = events.get(timeout=5)
        assert resumed.assignment_id == another.id
        assert resumed.cursor > event.cursor
        replayed = Queue()
        replay_stop = WorkspaceClient(restarted).observe(cursor, replayed.put)
        assert replayed.get(timeout=5) == event
    finally:
        stop()
        replay_stop()
        shutdown(active)


def test_event_listener_resumes_after_a_truncated_http_stream(monkeypatch, tmp_path):
    from contextlib import contextmanager
    from http.client import IncompleteRead
    from queue import Queue

    from opengrader.core.workspace.models import WorkspaceEvent
    from opengrader.server.workspace.client import ServiceConnection, WorkspaceClient

    first = WorkspaceEvent(
        cursor=7,
        assignment_id="assignment",
        submission_id=1,
        question_id="q1",
        revision="1",
    )
    second = first.model_copy(update={"cursor": 8, "revision": "2"})
    urls = Queue()
    events = Queue()

    def interrupted():
        yield f"data: {first.model_dump_json()}\n".encode()
        raise IncompleteRead(b"partial chunk")

    @contextmanager
    def open_stream(url, *, timeout):
        urls.put(url)
        yield (
            interrupted()
            if url.endswith("after=6")
            else iter([f"data: {second.model_dump_json()}\n".encode()])
        )

    monkeypatch.setattr("opengrader.server.workspace.client.urlopen", open_stream)
    client = WorkspaceClient(
        ServiceConnection(
            root=str(tmp_path), url="http://workspace", pid=0, workspace_id="workspace"
        )
    )
    stop = client.observe(6, events.put)
    try:
        assert events.get(timeout=3) == first
        assert events.get(timeout=3) == second
        assert urls.get_nowait() == "http://workspace/api/events?after=6"
        assert urls.get_nowait() == "http://workspace/api/events?after=7"
    finally:
        stop()


def test_a_service_that_dies_on_start_reports_why(monkeypatch, tmp_path):
    from opengrader.server.workspace import client

    root = tmp_path / "course"
    (root / ".opengrader").mkdir(parents=True)
    log = root / ".opengrader/service.log"
    log.write_text("PermissionError: an earlier, unrelated failure\n")

    class Crashed:
        def __init__(self, argv, *, stdout, **kwargs):
            stdout.write(
                b"Traceback (most recent call last):\n"
                b'  File "auth.py", line 7, in <module>\n'
                b"ModuleNotFoundError: No module named 'httpx'\n\n"
            )
            stdout.flush()

        def poll(self):
            return 1

    monkeypatch.setattr(client.subprocess, "Popen", Crashed)

    with pytest.raises(RuntimeError) as failure:
        client.ensure_service(root)

    message = str(failure.value)
    assert "No module named 'httpx'" in message
    assert "earlier, unrelated" not in message
    assert str(log.resolve()) in message


def test_a_service_that_dies_silently_does_not_blame_an_earlier_run(
    monkeypatch, tmp_path
):
    from opengrader.server.workspace import client

    root = tmp_path / "course"
    (root / ".opengrader").mkdir(parents=True)
    (root / ".opengrader/service.log").write_text("PermissionError: last week\n")

    class Silent:
        def __init__(self, argv, **kwargs):
            pass

        def poll(self):
            return 1

    monkeypatch.setattr(client.subprocess, "Popen", Silent)

    with pytest.raises(RuntimeError, match=r"could not start\. Check ") as failure:
        client.ensure_service(root)

    assert "last week" not in str(failure.value)


def test_feedback_without_a_pdf_fails_quietly_and_keeps_the_saved_grade(managed):
    from structlog.contextvars import get_contextvars, merge_contextvars
    from structlog.testing import capture_logs

    workspace, assignment, _ = managed
    workspace.save_grade(assignment, 1, "q1", command(workspace.answer(assignment, 1, "q1")))
    with capture_logs(processors=[merge_contextvars]) as events:
        FeedbackJobs(workspace).process(workspace.jobs()[0])
    assert events == [{
        "event": "feedback.unavailable", "log_level": "debug", "reason": "no_source_pdf",
        "assignment_id": assignment, "submission_id": 1, "generation": 1,
    }]
    assert workspace.jobs()[0].status == "failed"
    assert workspace.jobs()[0].error == "This submission has no source PDF. Its grade is saved."
    assert workspace.answer(assignment, 1, "q1").analysis == "Saved feedback"
    assert "assignment_id" not in get_contextvars()
