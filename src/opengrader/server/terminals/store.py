"""Bounded session metadata and transcripts, independent of live processes."""

import json
from collections import deque
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class TerminalRecord(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[0-9a-f]{32}$")
    assignment_id: str
    kind: Literal["shell"] = "shell"
    position: int = 0
    title: str
    cwd: str
    status: Literal["running", "stopped"] = "stopped"
    message: str = "Ready to start."


class OutputHistory:
    def __init__(self, limit: int = 1024 * 1024) -> None:
        self.limit = limit
        self.chunks: deque[bytes] = deque()
        self.size = 0
        self.dirty = False

    def append(self, data: bytes) -> None:
        self.chunks.append(data)
        self.size += len(data)
        while self.size > self.limit:
            first = self.chunks.popleft()
            remove = min(len(first), self.size - self.limit)
            self.size -= remove
            if remove < len(first):
                self.chunks.appendleft(first[remove:])
        self.dirty = True

    def value(self) -> bytes:
        return b"".join(self.chunks)


class TerminalStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.catalog = root / "sessions.json"
        self.records: dict[str, TerminalRecord] = {}
        if self.catalog.exists():
            records = TypeAdapter(list[TerminalRecord]).validate_python(
                [
                    record
                    for record in json.loads(self.catalog.read_bytes())
                    if record.get("kind") != "tui"
                ]
            )
            for record in records:
                if record.status == "running":
                    record.status = "stopped"
                    record.message = "Server restarted. Start this session to continue."
                self.records[record.id] = record

    def directory(self, session_id: str) -> Path:
        return self.root / session_id

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.catalog.with_suffix(".tmp")
        temporary.write_bytes(
            TypeAdapter(list[TerminalRecord]).dump_json(list(self.records.values()))
        )
        temporary.replace(self.catalog)

    def history(self, session_id: str) -> OutputHistory:
        history = OutputHistory()
        path = self.directory(session_id) / "output.log"
        if path.exists():
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - history.limit))
                history.append(stream.read(history.limit))
        history.dirty = False
        return history

    def save_history(self, session_id: str, history: OutputHistory) -> None:
        directory = self.directory(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "output.log"
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(history.value())
        temporary.replace(path)
        history.dirty = False
