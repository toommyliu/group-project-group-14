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
