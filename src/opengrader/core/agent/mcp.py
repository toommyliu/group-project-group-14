"""The grading packet as an MCP server, for assistants that cannot run commands.

Claude Desktop, ChatGPT and other chat apps speak the Model Context Protocol
(https://modelcontextprotocol.io) but have no shell. `serve` speaks it over
stdio — one JSON-RPC message per line, nothing else on stdout — and offers
the same operations as `opengrader agent …` as tools, bound to one packet.

It is also the strictest way to hand work to an assistant: one that only
has these tools has no file access at all. It cannot open the class folder,
because nothing it can call reaches outside the packet, so "stay in this
folder" stops being an instruction it has to follow.

Written on the standard library, not the `mcp` package: the installed app
carries no development or optional dependencies, and the subset a tools-only
server needs (initialize, tools/list, tools/call, ping) is small. Every tool
is a thin call into `tasks` and `suggestions`, so the rules — rubric labels
checked, a teacher's grade never changed — are the same ones the commands
enforce.
"""

from __future__ import annotations

import base64
import json
import shlex
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO, Any

from opengrader.core.qa_file.check import check_answer_key

from .common import AgentError, guide_text, packet_qa_path, read_marker, self_argv
from .suggestions import SUGGESTION_KINDS, list_suggestions, suggest
from .tasks import load_tasks, packet_status, record_grade, record_transcription

__all__ = ["SUPPORTED_PROTOCOL_VERSIONS", "McpServer", "client_config", "client_instructions", "serve"]

# Newest first; a client asking for one of these gets it back, any other
# gets the newest (the spec's negotiation rule).
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_NAME = "opengrader"

_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603

_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
_TASK = {"type": "string", "description": "A task id from list_tasks, e.g. og-1a2b3c4d/q2."}


def _tool(name: str, description: str, properties: Mapping[str, object], required: tuple[str, ...] = ()) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": dict(properties),
            "required": list(required),
            "additionalProperties": False,
        },
    }


TOOLS: tuple[dict, ...] = (
    _tool("status", "How many answers are still to grade, suggested by you, or graded by the teacher.", {}),
    _tool(
        "list_tasks",
        "The answers still to grade, one question at a time: prompt, correct answers, rubric "
        "options, and the OCR text. Call show_task to see what the student actually wrote.",
        {
            "question": {"type": "string", "description": "Only this question, e.g. q2."},
            "include_graded": {"type": "boolean", "description": "Also list answers already graded."},
            "limit": {"type": "integer", "minimum": 1},
        },
    ),
    _tool(
        "show_task",
        "One task, with the image of the student's answer. The OCR text is often wrong for "
        "handwriting: grade from the image.",
        {"task": _TASK},
        ("task",),
    ),
    _tool(
        "transcribe",
        "Record exactly what the student wrote, read from the image, when the OCR text is wrong or empty.",
        {"task": _TASK, "text": {"type": "string"}},
        ("task", "text"),
    ),
    _tool(
        "grade",
        "Suggest a grade by choosing rubric option labels exactly as listed. Opengrader works out "
        "the points; the teacher confirms every suggestion. Never used on an answer the teacher graded.",
        {
            "task": _TASK,
            "rubric": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "comment": {"type": "string", "description": "One short sentence for the teacher: why."},
        },
        ("task", "rubric", "confidence", "comment"),
    ),
    _tool(
        "check_answer_key",
        "Problems Opengrader itself finds in the answer key (duplicate questions or rubric labels, "
        "questions with no answer). Read them before grading.",
        {},
    ),
    _tool(
        "suggest_fix",
        "Tell the teacher something is wrong with the answer key, instead of grading around it. "
        "kind 'answer' with 'correct' proposes the answers the key should accept; kind 'rubric' "
        "with 'partial' proposes a complete partial-credit set (checklist options must add up to "
        "the question's full points or full deduction). Opengrader checks the fix now; the "
        "teacher accepts or dismisses it.",
        {
            "question": {"type": "string"},
            "kind": {"type": "string", "enum": list(SUGGESTION_KINDS)},
            "message": {"type": "string"},
            "version": {"type": "string", "description": "Only for an answer key with versions."},
            "correct": {"type": "array", "items": {"type": "string"}},
            "partial": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"label": {"type": "string"}, "points": {"type": "number"}},
                    "required": ["label", "points"],
                },
            },
        },
        ("question", "kind", "message"),
    ),
    _tool("list_suggestions", "The fixes you have suggested and whether the teacher accepted them.", {}),
)


class McpServer:
    def __init__(self, packet: Path) -> None:
        self.packet = packet
        self.client = ""
        self._handlers: dict[str, Callable[[Mapping[str, Any]], dict]] = {
            "status": self._status,
            "list_tasks": self._list_tasks,
            "show_task": self._show_task,
            "transcribe": self._transcribe,
            "grade": self._grade,
            "check_answer_key": self._check_key,
            "suggest_fix": self._suggest,
            "list_suggestions": self._list_suggestions,
        }

    # -- JSON-RPC -------------------------------------------------------------

    def handle(self, message: object) -> object | None:
        """One message in, the reply out (None for a notification)."""
        if isinstance(message, list):  # a batch (protocol 2025-03-26)
            replies = [reply for item in message if (reply := self.handle(item)) is not None]
            return replies or None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
            return _error(message.get("id") if isinstance(message, dict) else None, _INVALID_REQUEST, "not a JSON-RPC 2.0 request")
        has_id = "id" in message
        request_id = message.get("id")
        method = str(message["method"])
        params = message.get("params") or {}
        if not has_id:
            return None  # notifications/initialized, notifications/cancelled, …
        try:
            if method == "initialize":
                return _result(request_id, self._initialize(params))
            if method == "ping":
                return _result(request_id, {})
            if method == "tools/list":
                return _result(request_id, {"tools": list(TOOLS)})
            if method == "tools/call":
                return self._call(request_id, params)
            return _error(request_id, _METHOD_NOT_FOUND, f"no method {method}")
        except Exception as exc:  # noqa: BLE001 - a server answers, it does not die
            return _error(request_id, _INTERNAL_ERROR, str(exc))

    def _initialize(self, params: Mapping[str, Any]) -> dict:
        info = params.get("clientInfo") or {}
        self.client = str(info.get("name", "")) if isinstance(info, Mapping) else ""
        asked = str(params.get("protocolVersion", ""))
        version = asked if asked in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": _package_version()},
            "instructions": guide_text("agent-mcp.md"),
        }

    def _call(self, request_id: object, params: Mapping[str, Any]) -> dict:
        name = str(params.get("name", ""))
        handler = self._handlers.get(name)
        if handler is None:
            return _error(request_id, _INVALID_PARAMS, f"no tool {name!r}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, Mapping):
            return _error(request_id, _INVALID_PARAMS, "arguments must be an object")
        try:
            return _result(request_id, handler(arguments))
        except (AgentError, ValueError, TypeError, KeyError) as exc:
            # A tool's own failure goes back to the model, which can fix its call.
            return _result(request_id, {"content": [_text(str(exc))], "isError": True})

    # -- tools ----------------------------------------------------------------

    def _status(self, _args: Mapping[str, Any]) -> dict:
        status = packet_status(self.packet)
        return _json_content(
            {
                "students": status.students,
                "to_grade": status.ungraded,
                "suggested_by_you": status.graded_by_agent,
                "graded_by_teacher": status.graded,
                "papers_without_a_known_version": len(status.unknown_version),
            }
        )

    def _list_tasks(self, args: Mapping[str, Any]) -> dict:
        tasks = [
            task
            for task in load_tasks(self.packet, question=str(args.get("question", "")))
            if args.get("include_graded") or task.status == "ungraded"
        ]
        limit = int(args.get("limit") or 0)
        if limit > 0:
            tasks = tasks[:limit]
        rows = []
        for task in tasks:
            row = task.to_json()
            # The image goes out through show_task, as an image, not a path.
            row["has_image"] = bool(row.pop("response_image"))
            rows.append(row)
        return _json_content(rows)

    def _show_task(self, args: Mapping[str, Any]) -> dict:
        wanted = str(args["task"]).strip()
        for task in load_tasks(self.packet):
            if task.id != wanted:
                continue
            row = task.to_json()
            image = Path(str(row.pop("response_image") or ""))
            content = [_text(json.dumps(row, ensure_ascii=False, indent=2))]
            mime = _IMAGE_TYPES.get(image.suffix.lower())
            if image.is_file() and mime:
                content.append(
                    {"type": "image", "data": base64.b64encode(image.read_bytes()).decode("ascii"), "mimeType": mime}
                )
            else:
                content.append(_text("(no image for this answer: grade from the text, or leave it for the teacher)"))
            return {"content": content, "isError": False}
        raise AgentError(f"no task {wanted!r}; call list_tasks")

    def _transcribe(self, args: Mapping[str, Any]) -> dict:
        record_transcription(self.packet, str(args["task"]), str(args["text"]))
        return _json_content({"saved": True})

    def _grade(self, args: Mapping[str, Any]) -> dict:
        labels = args["rubric"]
        if isinstance(labels, str):
            labels = [labels]
        suggestion = record_grade(
            self.packet,
            str(args["task"]),
            [str(label) for label in labels],
            confidence=float(args["confidence"]),
            rationale=str(args.get("comment", "")),
            model=self.client,
        )
        return _json_content({"points": suggestion.points_awarded, "rubric": list(suggestion.rubric_labels)})

    def _check_key(self, _args: Mapping[str, Any]) -> dict:
        result = check_answer_key(packet_qa_path(self.packet).read_text(encoding="utf-8"))
        return _json_content({"errors": result.errors, "warnings": result.warnings})

    def _suggest(self, args: Mapping[str, Any]) -> dict:
        partial = tuple(
            (str(item["label"]), float(item["points"])) for item in args.get("partial") or []
        )
        made = suggest(
            self.packet,
            question=str(args["question"]),
            kind=str(args["kind"]),
            message=str(args["message"]),
            version=str(args.get("version", "")),
            correct=[str(value) for value in args.get("correct") or []],
            partial=partial,
            model=self.client,
        )
        return _json_content({"id": made.id, "recorded": True})

    def _list_suggestions(self, _args: Mapping[str, Any]) -> dict:
        return _json_content([s.to_json() for s in list_suggestions(self.packet)])


def _package_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("opengrader")
    except PackageNotFoundError:
        return "source"


def _text(text: str) -> dict:
    return {"type": "text", "text": text}


def _json_content(value: object) -> dict:
    return {"content": [_text(json.dumps(value, ensure_ascii=False, indent=2))], "isError": False}


def _result(request_id: object, result: object) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(packet: Path, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> None:
    """Answer MCP messages on stdin until it closes."""
    if str(read_marker(packet).get("kind", "grading")) != "grading":
        raise AgentError(f"{packet} is not a grading packet")
    server = McpServer(packet)
    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    for raw in source:
        if not raw.strip():
            continue
        try:
            message = json.loads(raw)
        except ValueError:
            reply: object | None = _error(None, _PARSE_ERROR, "not JSON")
        else:
            reply = server.handle(message)
        if reply is not None:
            sink.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sink.flush()


def client_config(packet: Path, *, run_as: list[str] | None = None) -> dict:
    """What a teacher pastes into an MCP client to give it this packet."""
    command, *args = run_as or self_argv()
    return {
        "mcpServers": {
            f"opengrader-{packet.name}": {
                "command": command,
                "args": [*args, "agent", "mcp", "--packet", str(packet)],
            }
        }
    }


def client_instructions(packet: Path) -> str:
    """How to give an MCP app this copy, in words a teacher can follow."""
    config = client_config(packet)
    [(name, entry)] = config["mcpServers"].items()
    command_line = " ".join(shlex.quote(part) for part in [entry["command"], *entry["args"]])
    return "\n".join(
        [
            "Claude Desktop: Settings → Developer → Edit Config, and add this to claude_desktop_config.json:",
            "",
            json.dumps(config, indent=2),
            "",
            f"Claude Code:  claude mcp add {name} -- {command_line}",
            "",
            "Other MCP apps (ChatGPT, Cursor, …): add a local (stdio) server with this command:",
            f"  {command_line}",
            "",
            "Through MCP the assistant has no file access: it can only call the grading tools for this copy.",
        ]
    )
