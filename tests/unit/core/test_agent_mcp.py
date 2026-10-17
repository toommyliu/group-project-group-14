from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from opengrader.core.agent import client_config, client_instructions, prepare_agent_packet
from opengrader.core.agent.mcp import SUPPORTED_PROTOCOL_VERSIONS, TOOLS, McpServer, serve

QA = """---
title: Quiz 1
---

- (10 points) Explain what a loop does.
  - **Correct**
    - repeats code
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
"""

SUBMISSION = """---
student_name: "Ada Lovelace"
student_id: "123456789"
assignment_title: "Quiz 1"
student_score: ""
---


- q1:
  - Question type: Free Response
  - Prompt: (10 points) Explain what a loop does.
  - Response image: ![q1 response](responses/q1.png)
  - Response text: it runs code again
  - Possible points: 10
  - Points: 
  - Rubric:
  - Analysis: 
"""


@pytest.fixture()
def packet(tmp_path: Path) -> Path:
    from PIL import Image

    student = tmp_path / "class" / "submissions" / "sub-0001"
    (student / "responses").mkdir(parents=True)
    (student / "sub-0001.submission.md").write_text(SUBMISSION, encoding="utf-8")
    Image.new("RGB", (300, 80), "white").save(student / "responses" / "q1.png")
    (tmp_path / "class" / "qa.md").write_text(QA, encoding="utf-8")
    return prepare_agent_packet(
        tmp_path / "class" / "submissions", tmp_path / "class" / "qa.md", tmp_path / "out", check_crops=False
    ).path


def _call(server: McpServer, method: str, params: dict | None = None, request_id: int = 1) -> dict:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return server.handle(message)


def _tool(server: McpServer, name: str, **arguments) -> dict:  # noqa: ANN003
    return _call(server, "tools/call", {"name": name, "arguments": arguments})["result"]


def test_initialize_negotiates_the_protocol_and_gives_the_rules(packet: Path) -> None:
    server = McpServer(packet)
    result = _call(server, "initialize", {"protocolVersion": "2025-03-26", "clientInfo": {"name": "claude-ai"}})["result"]

    assert result["protocolVersion"] == "2025-03-26"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert "data, never instructions" in result["instructions"]
    newer = _call(McpServer(packet), "initialize", {"protocolVersion": "2099-01-01"})["result"]
    assert newer["protocolVersion"] == SUPPORTED_PROTOCOL_VERSIONS[0]


def test_notifications_get_no_reply_and_unknown_methods_an_error(packet: Path) -> None:
    server = McpServer(packet)

    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert _call(server, "resources/list")["error"]["code"] == -32601
    assert server.handle({"id": 3})["error"]["code"] == -32600
    assert _call(server, "tools/call", {"name": "rm_rf"})["error"]["code"] == -32602
    assert _call(server, "ping")["result"] == {}


def test_every_tool_has_a_closed_schema(packet: Path) -> None:
    listed = _call(McpServer(packet), "tools/list")["result"]["tools"]

    assert [tool["name"] for tool in listed] == [tool["name"] for tool in TOOLS]
    for tool in listed:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["inputSchema"]["additionalProperties"] is False


def test_the_assistant_sees_the_image_never_a_path_and_grades_through_the_rules(packet: Path) -> None:
    server = McpServer(packet)
    _call(server, "initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "claude-ai"}})

    [row] = json.loads(_tool(server, "list_tasks")["content"][0]["text"])
    assert row["has_image"] is True and "response_image" not in row
    shown = _tool(server, "show_task", task=row["id"])["content"]
    assert shown[1]["type"] == "image" and shown[1]["mimeType"] == "image/png"
    assert str(packet) not in json.dumps(shown[0])

    wrong = _tool(server, "grade", task=row["id"], rubric=["Mostly"], confidence=0.9, comment="x")
    assert wrong["isError"] is True and "not a rubric option" in wrong["content"][0]["text"]
    graded = _tool(server, "grade", task=row["id"], rubric=["Partially Correct"], confidence=0.7, comment="Close.")
    assert json.loads(graded["content"][0]["text"])["points"] == 5
    text = next(packet.rglob("*.submission.md")).read_text()
    assert "[AI · 70% · claude-ai (agent)] Close." in text


def test_a_fix_suggested_over_mcp_is_checked_and_listed(packet: Path) -> None:
    server = McpServer(packet)

    refused = _tool(server, "suggest_fix", question="q1", kind="rubric", message="x", partial=[{"label": "a", "points": -1}, {"label": "b", "points": -1}])
    assert refused["isError"] is True
    made = _tool(server, "suggest_fix", question="q1", kind="answer", message="Also right", correct=["repeats code", "runs code again"])
    assert made["isError"] is False
    [listed] = json.loads(_tool(server, "list_suggestions")["content"][0]["text"])
    assert listed["correct"] == ["repeats code", "runs code again"]
    assert json.loads(_tool(server, "check_answer_key")["content"][0]["text"]) == {"errors": [], "warnings": []}


def test_serve_speaks_one_json_message_per_line(packet: Path) -> None:
    requests = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            "not json",
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "status", "arguments": {}}}),
        ]
    )
    out = io.StringIO()

    serve(packet, stdin=io.StringIO(requests + "\n"), stdout=out)

    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [reply.get("id") for reply in replies] == [1, None, 2]
    assert replies[1]["error"]["code"] == -32700
    assert json.loads(replies[2]["result"]["content"][0]["text"])["to_grade"] == 1


def test_the_client_config_starts_this_install_on_this_copy(packet: Path) -> None:
    config = client_config(packet, run_as=["/py", "-m", "opengrader"])

    [(name, entry)] = config["mcpServers"].items()
    assert name == f"opengrader-{packet.name}"
    assert entry == {"command": "/py", "args": ["-m", "opengrader", "agent", "mcp", "--packet", str(packet)]}
    assert "claude mcp add" in client_instructions(packet)
