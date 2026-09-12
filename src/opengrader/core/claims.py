from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal, Sequence

from opengrader.core.history.journal import current_actor

ClaimStatus = Literal["active", "released", "completed"]
DivideAxis = Literal["question", "student"]

_CLAIMS_DIRNAME = ".opengrader"
_CLAIMS_FILENAME = "claims.jsonl"

_VALID_STATUSES: frozenset[str] = frozenset({"active", "released", "completed"})


class ClaimConflictError(RuntimeError):
    def __init__(self, conflicts: Sequence["WorkClaim"]) -> None:
        self.conflicts = tuple(conflicts)
        who = ", ".join(sorted({claim.actor for claim in self.conflicts}))
        super().__init__(
            f"that work is already claimed by {who}. Claim a different slice, "
            "ask them to release theirs, or re-run with force to overlap "
            "deliberately."
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_claim_id() -> str:
    return f"cl-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class WorkSlice:
    question_ids: tuple[str, ...] = ()
    student_ids: tuple[str, ...] = ()

    @property
    def is_everything(self) -> bool:
        return not self.question_ids and not self.student_ids

    def covers(self, question_id: str, student_id: str) -> bool:
        if self.question_ids and question_id not in self.question_ids:
            return False
        if self.student_ids and student_id not in self.student_ids:
            return False
        return True

    def overlaps(self, other: "WorkSlice") -> bool:
        return _axis_overlaps(self.question_ids, other.question_ids) and _axis_overlaps(
            self.student_ids, other.student_ids
        )

    def describe(self) -> str:
        parts = [
            _describe_questions(self.question_ids),
            _describe_students(self.student_ids),
        ]
        return " · ".join(part for part in parts if part) or "everything"


@dataclass(frozen=True)
class WorkClaim:
    claim_id: str
    actor: str
    work: WorkSlice = field(default_factory=WorkSlice)
    status: ClaimStatus = "active"
    note: str = ""
    created_at: str = ""
    updated_at: str = ""
    closed_at: str = ""
    closed_by: str = ""

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    def covers(self, question_id: str, student_id: str) -> bool:
        return self.work.covers(question_id, student_id)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, line: str) -> "WorkClaim":
        data = json.loads(line)
        status = data.get("status", "active")
        if status not in _VALID_STATUSES:
            status = "active"
        work = data.get("work") or {}
        if not isinstance(work, dict):
            work = {}
        return cls(
            claim_id=str(data.get("claim_id", "")),
            actor=str(data.get("actor", "")),
            work=WorkSlice(
                question_ids=_string_tuple(work.get("question_ids")),
                student_ids=_string_tuple(work.get("student_ids")),
            ),
            status=status,  # type: ignore[arg-type]
            note=str(data.get("note", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            closed_at=str(data.get("closed_at", "")),
            closed_by=str(data.get("closed_by", "")),
        )


@dataclass(frozen=True)
class ClaimOverlap:
    first: WorkClaim
    second: WorkClaim
    shared_cells: int


@dataclass(frozen=True)
class CoverageReport:
    total_cells: int
    claimed_cells: int
    cells_by_actor: dict[str, int]
    overlaps: tuple[ClaimOverlap, ...] = ()

    @property
    def unclaimed_cells(self) -> int:
        return max(0, self.total_cells - self.claimed_cells)

    @property
    def is_fully_claimed(self) -> bool:
        return self.total_cells > 0 and self.unclaimed_cells == 0

    @property
    def has_overlaps(self) -> bool:
        return bool(self.overlaps)


def resolve_claims_path(submissions_path: str | Path) -> Path:
    base = Path(submissions_path)
    if base.is_file():
        base = base.parent
    return base / _CLAIMS_DIRNAME / _CLAIMS_FILENAME


def read_claims(submissions_path: str | Path) -> list[WorkClaim]:
    path = resolve_claims_path(submissions_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    latest: dict[str, WorkClaim] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = WorkClaim.from_json(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not record.claim_id:
            continue
        existing = latest.get(record.claim_id)
        if existing is None or record.updated_at >= existing.updated_at:
            latest[record.claim_id] = record

    return sorted(latest.values(), key=lambda c: (c.created_at, c.claim_id))


def active_claims(submissions_path: str | Path) -> list[WorkClaim]:
    return [claim for claim in read_claims(submissions_path) if claim.is_active]


def _append_record(submissions_path: str | Path, record: WorkClaim) -> Path:
    path = resolve_claims_path(submissions_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.to_json() + "\n")
    return path


def claim_work(
    submissions_path: str | Path,
    work: WorkSlice,
    *,
    actor: str | None = None,
    note: str = "",
    allow_overlap: bool = False,
    allow_everything: bool = False,
) -> WorkClaim:
    who = (actor or current_actor()).strip()
    if not who:
        raise ValueError("a claim needs an actor")
    if work.is_everything and not allow_everything:
        raise ValueError(
            "that claim covers the entire assignment; name questions or "
            "students, or pass allow_everything to mean it"
        )

    if not allow_overlap:
        conflicts = find_conflicts(work, active_claims(submissions_path), actor=who)
        if conflicts:
            raise ClaimConflictError(conflicts)

    now = _now_iso()
    record = WorkClaim(
        claim_id=_new_claim_id(),
        actor=who,
        work=work,
        status="active",
        note=note.strip(),
        created_at=now,
        updated_at=now,
    )
    _append_record(submissions_path, record)
    return record


def _close_claim(
    submissions_path: str | Path,
    claim_id: str,
    *,
    status: ClaimStatus,
    actor: str | None,
) -> WorkClaim | None:
    claim_id = claim_id.strip()
    current = {claim.claim_id: claim for claim in read_claims(submissions_path)}
    existing = current.get(claim_id)
    if existing is None:
        return None
    now = _now_iso()
    updated = replace(
        existing,
        status=status,
        updated_at=now,
        closed_at=now,
        closed_by=(actor or current_actor()),
    )
    _append_record(submissions_path, updated)
    return updated


def release_claim(
    submissions_path: str | Path, claim_id: str, *, actor: str | None = None
) -> WorkClaim | None:
    return _close_claim(submissions_path, claim_id, status="released", actor=actor)


def complete_claim(
    submissions_path: str | Path, claim_id: str, *, actor: str | None = None
) -> WorkClaim | None:
    return _close_claim(submissions_path, claim_id, status="completed", actor=actor)


def find_conflicts(
    work: WorkSlice, claims: Iterable[WorkClaim], *, actor: str
) -> list[WorkClaim]:
    return [
        claim
        for claim in claims
        if claim.is_active
        and claim.actor != actor
        and claim.work.overlaps(work)
    ]


def claim_for(
    claims: Iterable[WorkClaim], question_id: str, student_id: str
) -> WorkClaim | None:
    for claim in claims:
        if claim.is_active and claim.covers(question_id, student_id):
            return claim
    return None

def is_owned_by_other(
    claims: Iterable[WorkClaim], question_id: str, student_id: str, *, actor: str
) -> bool:
    owner = claim_for(claims, question_id, student_id)
    return owner is not None and owner.actor != actor


def divide_work(
    graders: Sequence[str],
    *,
    question_ids: Sequence[str] = (),
    student_ids: Sequence[str] = (),
    axis: DivideAxis = "question",
) -> list[tuple[str, WorkSlice]]:
    """Empty WorkSlice fields mean the whole assignment, so empty slices must be omitted."""
    names = [name.strip() for name in graders if name.strip()]
    if not names:
        raise ValueError("dividing work needs at least one grader")

    items = list(question_ids if axis == "question" else student_ids)
    if not items:
        raise ValueError(f"no {axis}s to divide")

    shares: list[tuple[str, WorkSlice]] = []
    for name, chunk in zip(names, _even_chunks(items, len(names))):
        if not chunk:
            continue
        if axis == "question":
            shares.append((name, WorkSlice(question_ids=tuple(chunk))))
        else:
            shares.append((name, WorkSlice(student_ids=tuple(chunk))))
    return shares


def coverage_report(
    claims: Iterable[WorkClaim],
    *,
    question_ids: Sequence[str],
    student_ids: Sequence[str],
) -> CoverageReport:
    held = [claim for claim in claims if claim.is_active]
    total = len(question_ids) * len(student_ids)

    claimed = 0
    by_actor: dict[str, int] = {}
    for question_id in question_ids:
        for student_id in student_ids:
            owner = claim_for(held, question_id, student_id)
            if owner is None:
                continue
            claimed += 1
            by_actor[owner.actor] = by_actor.get(owner.actor, 0) + 1

    overlaps: list[ClaimOverlap] = []
    for index, first in enumerate(held):
        for second in held[index + 1 :]:
            if first.actor == second.actor or not first.work.overlaps(second.work):
                continue
            shared = sum(
                1
                for question_id in question_ids
                for student_id in student_ids
                if first.covers(question_id, student_id)
                and second.covers(question_id, student_id)
            )
            if shared:
                overlaps.append(ClaimOverlap(first, second, shared))

    return CoverageReport(
        total_cells=total,
        claimed_cells=claimed,
        cells_by_actor=by_actor,
        overlaps=tuple(overlaps),
    )


def _axis_overlaps(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    if not left or not right:
        return True
    return bool(set(left) & set(right))


_MAX_LISTED_QUESTIONS = 8


def _describe_questions(ids: tuple[str, ...]) -> str:
    if not ids:
        return ""
    if len(ids) == 1:
        return ids[0]
    if _is_contiguous_run(ids):
        return f"{ids[0]}-{ids[-1]}"
    if len(ids) <= _MAX_LISTED_QUESTIONS:
        return ", ".join(ids)
    return f"{ids[0]}…{ids[-1]} ({len(ids)} questions)"


def _describe_students(ids: tuple[str, ...]) -> str:
    if not ids:
        return ""
    if len(ids) == 1:
        return f"student {ids[0]}"
    return f"{len(ids)} students"


def _is_contiguous_run(ids: tuple[str, ...]) -> bool:
    numbers = []
    for value in ids:
        digits = "".join(char for char in value if char.isdigit())
        if not digits or not value.endswith(digits):
            return False
        numbers.append(int(digits))
    return all(b - a == 1 for a, b in zip(numbers, numbers[1:]))


def _even_chunks(items: Sequence[str], parts: int) -> list[list[str]]:
    size, remainder = divmod(len(items), parts)
    chunks: list[list[str]] = []
    start = 0
    for index in range(parts):
        length = size + (1 if index < remainder else 0)
        chunks.append(list(items[start : start + length]))
        start += length
    return chunks


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())
