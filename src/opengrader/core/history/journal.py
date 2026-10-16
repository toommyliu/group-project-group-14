from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

JournalKind = Literal["grade", "comment", "rubric", "identity", "transcription"]

_HISTORY_DIRNAME = ".opengrader"
_HISTORY_SUBDIR = "history"
_JOURNAL_GLOB = "grade-journal-*.jsonl"
_UNSAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class GradeJournalEntry:
    timestamp: str
    actor: str
    kind: JournalKind
    student_id: str
    student_name: str
    question_id: str
    summary: str
    old: dict[str, Any] = field(default_factory=dict)
    new: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> "GradeJournalEntry":
        data = json.loads(line)
        return cls(
            timestamp=str(data.get("timestamp", "")),
            actor=str(data.get("actor", "")),
            kind=data.get("kind", "grade"),
            student_id=str(data.get("student_id", "")),
            student_name=str(data.get("student_name", "")),
            question_id=str(data.get("question_id", "")),
            summary=str(data.get("summary", "")),
            old=data.get("old") or {},
            new=data.get("new") or {},
        )


def current_actor() -> str:
    override = os.environ.get("OPENGRADER_GRADER", "").strip()
    if override:
        return override
    for env_key in ("USER", "USERNAME", "LOGNAME"):
        value = os.environ.get(env_key, "").strip()
        if value:
            return value
    try:
        import getpass

        return getpass.getuser()
    except Exception:
        return "unknown"


def resolve_history_dir(submissions_path: str | Path) -> Path:
    base = Path(submissions_path)
    if base.is_file():
        base = base.parent
    return base / _HISTORY_DIRNAME / _HISTORY_SUBDIR


def _sanitize(component: str) -> str:
    cleaned = _UNSAFE_CHARS_RE.sub("-", component.strip())
    return cleaned.strip("-") or "x"


def _default_session_id(actor: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{_sanitize(actor)}-{os.getpid()}"


class GradeJournal:
    def __init__(
        self,
        history_dir: str | Path,
        *,
        actor: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self._dir = Path(history_dir)
        self._actor = actor or current_actor()
        self._session_id = session_id or _default_session_id(self._actor)
        self._path = self._dir / f"grade-journal-{self._session_id}.jsonl"

    @property
    def directory(self) -> Path:
        return self._dir

    @property
    def actor(self) -> str:
        return self._actor

    @property
    def path(self) -> Path:
        return self._path

    def append(
        self,
        *,
        kind: JournalKind,
        student_id: str,
        student_name: str,
        question_id: str,
        summary: str,
        old: dict[str, Any] | None = None,
        new: dict[str, Any] | None = None,
    ) -> GradeJournalEntry:
        entry = GradeJournalEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            actor=self._actor,
            kind=kind,
            student_id=student_id,
            student_name=student_name,
            question_id=question_id,
            summary=summary,
            old=old or {},
            new=new or {},
        )
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(entry.to_json() + "\n")
        return entry


def journal_fingerprint(history_dir: str | Path) -> tuple[tuple[str, int, int], ...]:
    directory = Path(history_dir)
    if not directory.is_dir():
        return ()
    entries: list[tuple[str, int, int]] = []
    for journal_file in sorted(directory.glob(_JOURNAL_GLOB)):
        try:
            stat = journal_file.stat()
        except OSError:
            continue
        entries.append((journal_file.name, stat.st_size, stat.st_mtime_ns))
    return tuple(entries)


def read_journal(history_dir: str | Path) -> list[GradeJournalEntry]:
    directory = Path(history_dir)
    if not directory.is_dir():
        return []
    entries: list[GradeJournalEntry] = []
    for journal_file in sorted(directory.glob(_JOURNAL_GLOB)):
        try:
            text = journal_file.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(GradeJournalEntry.from_json(line))
            except (json.JSONDecodeError, ValueError):
                continue
    entries.sort(key=lambda entry: entry.timestamp)
    return entries
