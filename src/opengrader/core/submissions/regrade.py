from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

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
    return f"rg-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class RegradeRequest:
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
        if existing is None or record.updated_at >= existing.updated_at:
            latest[record.request_id] = record

    return sorted(latest.values(), key=lambda r: (r.created_at, r.request_id))


def open_regrade_requests(
    submissions_path: str | Path,
) -> list[RegradeRequest]:
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
    return _close_request(
        submissions_path, request_id, status="denied", note=note, actor=actor
    )
