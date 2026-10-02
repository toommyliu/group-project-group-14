import json
import subprocess
import sys
import textwrap


def test_detached_service_logs_only_to_json_lines(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent('''\
                import logging
                import sys
                from pathlib import Path

                import structlog
                import uvicorn

                from opengrader.server.logs import configure

                configure("info", workspace=Path(sys.argv[1]), detached=True)
                uvicorn.Config(lambda scope, receive, send: None, log_config=None, access_log=False)
                structlog.stdlib.get_logger("opengrader.server.test").info("assignment.created", assignment_id="a1")
                logging.getLogger("uvicorn.error").info("startup chatter")
                logging.getLogger("opengrader.core.test").warning("storage warning")
            '''),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    events = [
        json.loads(line)
        for line in (tmp_path / ".opengrader/logs/server.jsonl").read_text().splitlines()
    ]
    for event in events:
        assert event.pop("timestamp").endswith("Z")
    assert events == [
        {
            "level": "info",
            "event": "assignment.created",
            "logger": "opengrader.server.test",
            "assignment_id": "a1",
        },
        {
            "level": "warning",
            "event": "storage warning",
            "logger": "opengrader.core.test",
        },
    ]
    assert result.stderr == ""
    assert result.stdout == ""


def test_terminal_clients_do_not_import_structlog():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent('''\
                import sys

                import opengrader.tui.main
                import opengrader.cli.main
                import opengrader.cli.run
                import opengrader.cli.web
                import opengrader.server.workspace.client
                import opengrader.server.workspace.drafts
                import opengrader.core.workspace.folder_session
                import opengrader.core.workspace.store

                assert "structlog" not in sys.modules
                print("terminal imports retain stdlib logging")
            '''),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "terminal imports retain stdlib logging\n"
    assert result.stderr == ""


def test_unexpected_error_returns_the_logged_reference(tmp_path, monkeypatch):
    import re

    from fastapi.testclient import TestClient
    from structlog.contextvars import bind_contextvars, clear_contextvars, merge_contextvars
    from structlog.testing import capture_logs

    from opengrader.core.workspace.store import Workspace
    from opengrader.server.auth import RemoteAuthGuard
    from opengrader.server.logs import observe
    from opengrader.server.workspace.app import create_app

    app = create_app(tmp_path, process_jobs=False)
    app.add_middleware(RemoteAuthGuard)

    def broken(_self):
        raise RuntimeError("boom")

    bind_contextvars(user="stale@example.com", batch_id="old-batch")
    try:
        with capture_logs(processors=[merge_contextvars]) as events:
            with TestClient(observe(app), base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
                ok = client.get("/api/workspace?private=student", headers={"x-request-id": "untrusted"})
                monkeypatch.setattr(Workspace, "assignments", broken)
                bad = client.get("/api/assignments")
                denied = client.get("/api/workspace", headers={"sec-fetch-site": "cross-site"})
    finally:
        clear_contextvars()
    assert ok.status_code == 200
    assert bad.status_code == 500
    assert denied.status_code == 403
    requests = [event for event in events if event["event"] == "http.request"]
    assert len(requests) == 3
    normal, error, refusal = requests
    reference = error["request_id"]
    assert len({event["request_id"] for event in requests}) == 3
    for event in requests:
        assert re.fullmatch(r"[0-9a-f]{8}", event.pop("request_id"))
        assert isinstance(event.pop("duration_ms"), float)
        assert "user" not in event
        assert "batch_id" not in event
    assert bad.json() == {
        "detail": "The server couldn't finish this request. If it keeps happening, "
        f"give this reference to whoever set up Opengrader: {reference}."
    }
    assert error.pop("exc_info") is True
    assert normal == {
        "event": "http.request", "log_level": "debug", "status": 200,
        "route": "/api/workspace", "client": "127.0.0.1", "method": "GET", "path": "/api/workspace",
    }
    assert error == {
        "event": "http.request", "log_level": "error", "status": 500,
        "route": "/api/assignments", "client": "127.0.0.1", "method": "GET", "path": "/api/assignments",
    }
    assert refusal == {
        "event": "http.request", "log_level": "debug", "status": 403,
        "route": None, "client": "127.0.0.1", "method": "GET", "path": "/api/workspace",
    }


def test_pdf_storage_error_returns_the_logged_reference(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from structlog.contextvars import clear_contextvars, merge_contextvars
    from structlog.testing import capture_logs

    from opengrader.server import assignment_pdf
    from opengrader.server.logs import observe
    from opengrader.server.workspace.app import create_app

    cause = OSError("storage unavailable")

    def broken(**_kwargs):
        raise cause

    monkeypatch.setattr(assignment_pdf, "generate_template_pdf", broken)
    try:
        with capture_logs(processors=[merge_contextvars]) as events:
            with TestClient(observe(create_app(tmp_path, process_jobs=False))) as client:
                response = client.post(
                    "/api/drafts/pdf",
                    json={"qa_markdown": "- (1 point) Question\n  - **Answer**\n    - Yes\n"},
                )
    finally:
        clear_contextvars()

    assert response.status_code == 500
    failure, request = [
        event for event in events if event["event"] in {"request.failed", "http.request"}
    ]
    reference = failure["request_id"]
    assert response.json() == {
        "detail": "Could not generate the PDF. Check server storage and try again. "
        "If it keeps happening, give this reference to whoever set up Opengrader: "
        f"{reference}."
    }
    assert failure == {
        "event": "request.failed", "log_level": "error", "status": 500, "exc_info": cause,
        "request_id": reference, "method": "POST", "path": "/api/drafts/pdf",
    }
    assert request["event"] == "http.request"
    assert request["status"] == 500
    assert request["request_id"] == reference


def test_rejected_requests_log_parse_and_value_errors_at_debug(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from structlog.testing import capture_logs

    from opengrader.core.qa_file.errors import QAParseError
    from opengrader.core.workspace.store import Workspace
    from opengrader.server.workspace.app import create_app

    app = create_app(tmp_path, process_jobs=False)

    def broken(_self):
        raise ValueError("unexpected value")

    with capture_logs() as events:
        with TestClient(app) as client:
            parsed = client.post(
                "/api/drafts/preview",
                json={"qa_markdown": "- (10 points) Question\n  - **Choices**\n    - A. Only choice\n"},
            )
            monkeypatch.setattr(Workspace, "assignments", broken)
            rejected = client.get("/api/assignments")
    assert parsed.status_code == 422
    assert parsed.json()["location"] == {
        "line": 1, "version": "", "question_path": [0], "rubric_path": None,
    }
    assert rejected.status_code == 422
    assert rejected.json() == {"detail": "unexpected value"}
    parse, value = [event for event in events if event["event"] == "request.rejected"]
    assert isinstance(parse.pop("exc_info"), QAParseError)
    assert parse == {"event": "request.rejected", "error": "QAParseError", "log_level": "debug"}
    assert isinstance(value.pop("exc_info"), ValueError)
    assert value == {"event": "request.rejected", "error": "ValueError", "log_level": "debug"}
