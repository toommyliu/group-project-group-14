"""Durable, append-only grade-history journal for accountability.

Grading truth lives in the ``.submission.md`` files, and the optimistic mtime
guard (`core/concurrency.py`) already stops two graders from clobbering each
other. What that leaves missing — and what a shared-grading workflow needs — is
*accountability*: a durable record of who changed which grade, from what to
what, and when. This module is that record.

Design decisions (deliberate, for a file-only offline-first tool):

* **Location** — ``<submissions>/.opengrader/history/``. Co-located with the
  assignment data it describes (history travels when the folder is copied/moved), but
  namespaced in a dot-dir so it stays out of submission discovery and reads as
  tool metadata, not the user's data. Mirrors the existing ``.tui_cache`` dot
  directory precedent.
* **Append-only JSONL** — one self-contained event per line. Append avoids the
  read-modify-write races a single mutable JSON blob would invite.
* **Per-session files** — each grading session writes its *own* file
  (``grade-journal-<session>.jsonl``), so two graders on a shared/synced folder
  never contend on one file. Reading merges every file in the directory.
* **Actor attribution** — the OS user by default, overridable with
  ``OPENGRADER_GRADER`` so a named grader survives shared logins.
"""

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
# Keep session ids filesystem-safe across the actor/host components.
_UNSAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class GradeJournalEntry:
    """One recorded grading action.

    ``old`` and ``new`` are small dicts of the fields the action changed (e.g.
    ``{"points": 8.0, "rubric": ["Partial"]}``); keeping them generic lets the
    same entry describe grade, comment, and rubric events without a schema per
    kind.
    """

    timestamp: str  # ISO 8601, UTC, e.g. "2026-07-20T18:30:00+00:00"
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
    """Best-effort identity for the person grading.

    ``OPENGRADER_GRADER`` wins so a named grader survives shared machines;
    otherwise fall back to the OS login name, then a neutral default.
    """
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
    """Return the ``.opengrader/history`` dir for a submissions file or dir."""
    base = Path(submissions_path)
    if base.is_file():
        base = base.parent
    return base / _HISTORY_DIRNAME / _HISTORY_SUBDIR


def _sanitize(component: str) -> str:
    cleaned = _UNSAFE_CHARS_RE.sub("-", component.strip())
    return cleaned.strip("-") or "x"


def _default_session_id(actor: str) -> str:
    """A per-run, per-actor id: timestamp + actor + pid, filesystem-safe."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{_sanitize(actor)}-{os.getpid()}"


class GradeJournal:
    """Append-only writer for one grading session's history file."""

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
    """A cheap identity for "the journal as it is right now", for cache keys.

    :func:`read_journal` is not cheap — it parses every line of every session
    file — so a caller that memoizes something derived from the journal needs a
    way to ask "has it moved?" without re-reading it. That is this: the sorted
    ``(filename, size, mtime_ns)`` of each session file, which changes if and
    only if the journal did.

    **Size is the load-bearing part, not the mtime.** These files are strictly
    append-only (:meth:`GradeJournal.append` opens them ``"a"`` and never
    rewrites), so *any* new entry — by this process or another one — grows the
    file. That makes the fingerprint immune to the coarse filesystem clocks
    that can report an unchanged mtime for a write inside the same tick; the
    mtime is carried alongside only as a second opinion.

    Including the **file names** is what covers the case a size cannot: a
    *different* actor or session writing for the first time creates a whole new
    ``grade-journal-<session>.jsonl`` rather than growing an existing one. That
    is exactly what a second grader opening the same shared folder, a
    ``grade-restore`` run, or a ``grade-merge`` apply does.

    A missing directory (nothing has ever been graded here) fingerprints as the
    empty tuple, and an unreadable entry is skipped rather than raising — a
    cache key must never be the thing that takes a grading session down.
    """
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
    """Merge every session file in ``history_dir``, oldest event first.

    Malformed lines are skipped rather than raising, so one truncated write
    (e.g. an interrupted save) never makes the whole history unreadable.
    """
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
