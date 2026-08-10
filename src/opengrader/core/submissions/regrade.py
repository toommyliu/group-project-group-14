"""Regrade-request tracking — turn a grade dispute into a first-class item.

Once graded assignments are returned (annotated PDFs), a student may contest a
grade: "I think Q3 was marked wrong because…". Today that lives in the
instructor's inbox — untracked, invisible to whoever is grading, and with no
route back to the disputed question. This module makes a dispute a *tracked*
record that flows into the existing needs-review triage
(`core/submissions/review.py`) and routes a grader straight to the question.

Design decisions (mirroring `core/history/journal.py` and `core/policy.py`):

* **Location** — ``<submissions>/.opengrader/regrades.jsonl``. Co-located with
  the assignment data so requests travel when the folder is copied/moved,
  namespaced in the ``.opengrader`` dot-dir so they read as tool metadata and
  stay out of submission discovery.
* **Append-only, event-sourced JSONL** — one self-contained record per line.
  A *status change* (resolve/deny) is a fresh line for the same ``request_id``;
  :func:`read_regrade_requests` folds the log so the latest line per id wins.
  Append (never read-modify-write) avoids the races a mutable blob would invite,
  so a future web intake process and a grader's TUI can both write safely.
* **Front-end agnostic** — all dispute logic lives here, in pure ``core``, exactly
  like ``policy.py``/``curve.py``. The TUI and CLI are thin callers; a future web
  layer imports the same functions. Every record carries a ``source`` (``"instructor"``
  today, room for ``"student_portal"`` later) and a stable ``request_id`` so a web
  form is just another writer of the same log.
* **Absent/malformed = empty** — a missing or corrupt file resolves to no
  requests rather than raising, so it never blocks grading; a truncated line is
  skipped, not fatal.

A regrade request is a *prompt to a human*, never an automated grade change —
the same philosophy as the consistency/similarity audits.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from opengrader.core.history.journal import current_actor

RegradeStatus = Literal["open", "resolved", "denied"]
RegradeSource = Literal["instructor", "student_portal"]

_REGRADE_DIRNAME = ".opengrader"
_REGRADE_FILENAME = "regrades.jsonl"

_VALID_STATUSES: frozenset[str] = frozenset({"open", "resolved", "denied"})
_VALID_SOURCES: frozenset[str] = frozenset({"instructor", "student_portal"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_request_id() -> str:
    """A short, stable, collision-free id (e.g. ``rg-1a2b3c4d``)."""
    return f"rg-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class RegradeRequest:
    """One tracked grade dispute.

    ``question_id`` is empty for a whole-assignment dispute (the reviewer is
    routed to the student's first question). ``status`` is the current state;
    ``resolved_*`` fields are populated only once it leaves ``"open"``.
    """

    request_id: str
    student_id: str
    reason: str
    question_id: str = ""
    status: RegradeStatus = "open"
    source: RegradeSource = "instructor"
    created_at: str = ""
    created_by: str = ""
    updated_at: str = ""
    resolved_at: str = ""
    resolved_by: str = ""
    resolution_note: str = ""

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> "RegradeRequest":
        data = json.loads(line)
        status = data.get("status", "open")
        if status not in _VALID_STATUSES:
            status = "open"
        source = data.get("source", "instructor")
        if source not in _VALID_SOURCES:
            source = "instructor"
        return cls(
            request_id=str(data.get("request_id", "")),
            student_id=str(data.get("student_id", "")),
            reason=str(data.get("reason", "")),
            question_id=str(data.get("question_id", "")),
            status=status,  # type: ignore[arg-type]
            source=source,  # type: ignore[arg-type]
            created_at=str(data.get("created_at", "")),
            created_by=str(data.get("created_by", "")),
            updated_at=str(data.get("updated_at", "")),
            resolved_at=str(data.get("resolved_at", "")),
            resolved_by=str(data.get("resolved_by", "")),
            resolution_note=str(data.get("resolution_note", "")),
        )


def resolve_regrades_path(submissions_path: str | Path) -> Path:
    """Return the ``.opengrader/regrades.jsonl`` path for a file or dir."""
    base = Path(submissions_path)
    if base.is_file():
        base = base.parent
    return base / _REGRADE_DIRNAME / _REGRADE_FILENAME


def _append_record(submissions_path: str | Path, record: RegradeRequest) -> Path:
    path = resolve_regrades_path(submissions_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.to_json() + "\n")
    return path


def read_regrade_requests(submissions_path: str | Path) -> list[RegradeRequest]:
    """Return the current state of every request, oldest creation first.

    The file is an append-only event log: a resolve/deny writes a fresh line for
    the same ``request_id``. This folds it so the latest line per id wins (by
    ``updated_at``, tie-broken by file order), and malformed lines are skipped so
    one truncated write never makes the whole log unreadable.
    """
    path = resolve_regrades_path(submissions_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    latest: dict[str, RegradeRequest] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = RegradeRequest.from_json(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not record.request_id:
            continue
        existing = latest.get(record.request_id)
        # Later file position wins on an equal/absent timestamp; a strictly
        # newer updated_at always wins.
        if existing is None or record.updated_at >= existing.updated_at:
            latest[record.request_id] = record

    return sorted(latest.values(), key=lambda r: (r.created_at, r.request_id))


def open_regrade_requests(
    submissions_path: str | Path,
) -> list[RegradeRequest]:
    """Just the still-open requests (what triage cares about)."""
    return [r for r in read_regrade_requests(submissions_path) if r.is_open]


def add_regrade_request(
    submissions_path: str | Path,
    *,
    student_id: str,
    reason: str,
    question_id: str = "",
    source: RegradeSource = "instructor",
    actor: str | None = None,
) -> RegradeRequest:
    """File a new dispute and return the stored record.

    ``student_id`` identifies whose grade is contested; ``question_id`` is the
    disputed question (empty for a whole-assignment dispute). ``reason`` is the
    student's stated argument. ``source`` distinguishes an instructor-entered
    request from a future student-portal one.
    """
    student_id = student_id.strip()
    if not student_id:
        raise ValueError("a regrade request needs a student_id")
    now = _now_iso()
    record = RegradeRequest(
        request_id=_new_request_id(),
        student_id=student_id,
        reason=reason.strip(),
        question_id=question_id.strip(),
        status="open",
        source=source,
        created_at=now,
        created_by=actor or current_actor(),
        updated_at=now,
    )
    _append_record(submissions_path, record)
    return record


def _close_request(
    submissions_path: str | Path,
    request_id: str,
    *,
    status: RegradeStatus,
    note: str,
    actor: str | None,
) -> RegradeRequest | None:
    request_id = request_id.strip()
    current = {r.request_id: r for r in read_regrade_requests(submissions_path)}
    existing = current.get(request_id)
    if existing is None:
        return None
    now = _now_iso()
    updated = replace(
        existing,
        status=status,
        updated_at=now,
        resolved_at=now,
        resolved_by=actor or current_actor(),
        resolution_note=note.strip(),
    )
    _append_record(submissions_path, updated)
    return updated


def resolve_regrade_request(
    submissions_path: str | Path,
    request_id: str,
    *,
    note: str = "",
    actor: str | None = None,
) -> RegradeRequest | None:
    """Mark a request resolved (the grade was reviewed/adjusted). ``None`` if unknown."""
    return _close_request(
        submissions_path, request_id, status="resolved", note=note, actor=actor
    )


def deny_regrade_request(
    submissions_path: str | Path,
    request_id: str,
    *,
    note: str = "",
    actor: str | None = None,
) -> RegradeRequest | None:
    """Mark a request denied (grade stands). ``None`` if the id is unknown."""
    return _close_request(
        submissions_path, request_id, status="denied", note=note, actor=actor
    )
