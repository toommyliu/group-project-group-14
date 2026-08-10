"""Multi-grader work claims — divide the pile so two TAs never grade one paper.

The anonymity lock (`core/policy.py`) makes a multi-grader team *fair*: every TA
sees student ids instead of names. What it does not do is make the work
*divided*. Two TAs opening the same folder both start at question 1, student 1,
and grade straight down the same list. The only thing that eventually notices is
the optimistic mtime guard (`core/concurrency.py`), which refuses the second
save with a "changed on disk" error — after both people have already done the
work, and phrased as a *conflict* rather than as what it is: nobody said who was
grading what.

This module is the missing agreement. A **claim** records that one grader has
taken a slice of the cohort — "TA1 takes q1-q3", "TA2 takes the first forty
students" — so each person's queue is disjoint by construction and the mtime
guard goes back to being the last-resort safety net it was designed as.

Design decisions (mirroring `submissions/regrade.py` and `policy.py`):

* **Location** — ``<submissions>/.opengrader/claims.jsonl``. Co-located with the
  assignment so claims travel when the folder is copied or synced, in the
  ``.opengrader`` dot-dir so they read as tool metadata and stay out of
  submission discovery.
* **Append-only, event-sourced JSONL** — one self-contained record per line; a
  release/complete appends a *fresh* line for the same ``claim_id`` and
  :func:`read_claims` folds so the latest wins. Appending is what makes this
  safe for the exact situation it exists for: several graders on one shared or
  synced folder, all writing at once. A mutable blob would need the very
  read-modify-write lock we cannot have.
* **Absent/malformed = no claims** — an unclaimed folder behaves exactly as
  before, and a truncated line is skipped rather than fatal. Losing this file
  costs coordination, never grades.

**A claim is a coordination record, not a permission system.** It is the same
honest scope as the anonymity lock: a semi-trusted professor+TA team, on a
shared disk, where the failure being prevented is *duplicated effort*, not
malice. Nothing here can stop a determined grader from editing any file — the
value is that their queue no longer points them at someone else's slice, and
that a teacher can see the division and its gaps up front.

The whole module is pure: it is handed the cohort's question and student ids
rather than reading submissions itself, so the CLI, the TUI (which already has
them loaded) and any later web layer all divide work identically.
"""

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
    """A slice overlaps work another grader has already claimed.

    Carries the conflicting claims so the caller can name who holds the
    overlapping work rather than just refusing.
    """

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
    """A short, stable, collision-free id (e.g. ``cl-1a2b3c4d``)."""
    return f"cl-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class WorkSlice:
    """Which work a claim covers, as a question axis and a student axis.

    **An empty axis means "all of it"**, so the slice is the cross product of
    the two: ``WorkSlice(question_ids=("q1", "q2"))`` is those two questions for
    every student, and ``WorkSlice()`` is the entire assignment.

    That default is convenient and dangerous in the same breath — an empty
    *share* produced by dividing work between too many graders would silently
    mean "everything" — so :func:`divide_work` never emits one and
    :func:`claim_work` refuses to store a slice that claims the whole cohort
    unless it is asked for explicitly.
    """

    question_ids: tuple[str, ...] = ()
    student_ids: tuple[str, ...] = ()

    @property
    def is_everything(self) -> bool:
        return not self.question_ids and not self.student_ids

    def covers(self, question_id: str, student_id: str) -> bool:
        """Whether one unit of work (a student's answer) falls in this slice."""
        if self.question_ids and question_id not in self.question_ids:
            return False
        if self.student_ids and student_id not in self.student_ids:
            return False
        return True

    def overlaps(self, other: "WorkSlice") -> bool:
        """Whether two slices share any work at all.

        Both axes must intersect: "q1-q3 for everyone" and "q4-q6 for everyone"
        are disjoint, and so are "all questions for students A-M" and "all
        questions for students N-Z". An empty axis intersects everything.
        """
        return _axis_overlaps(self.question_ids, other.question_ids) and _axis_overlaps(
            self.student_ids, other.student_ids
        )

    def describe(self) -> str:
        """A short human summary ("q1-q3 · 40 students").

        The two axes are summarized differently on purpose. A grader needs to
        know *which questions* they hold — real assignments number them
        ``q1, q2, q5a, q5b`` and a bare "6 questions" tells them nothing — but
        student ids are opaque digits, so "40 students" is the more useful
        summary than forty of them listed out.
        """
        parts = [
            _describe_questions(self.question_ids),
            _describe_students(self.student_ids),
        ]
        return " · ".join(part for part in parts if part) or "everything"


@dataclass(frozen=True)
class WorkClaim:
    """One grader's claim on a slice of the cohort."""

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
    """Two active claims that share work — the thing claims exist to prevent."""

    first: WorkClaim
    second: WorkClaim
    shared_cells: int


@dataclass(frozen=True)
class CoverageReport:
    """How much of the cohort is spoken for, by whom, and where it double-books.

    "Cells" are units of work — one student's answer to one question — which is
    the granularity a claim actually divides. Counting them lets a mixed set of
    slices (some by question, some by student) be summarized honestly, where
    counting questions or students alone could not.
    """

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


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #


def resolve_claims_path(submissions_path: str | Path) -> Path:
    """Return the ``.opengrader/claims.jsonl`` path for a file or dir."""
    base = Path(submissions_path)
    if base.is_file():
        base = base.parent
    return base / _CLAIMS_DIRNAME / _CLAIMS_FILENAME


def read_claims(submissions_path: str | Path) -> list[WorkClaim]:
    """Every claim's current state, oldest creation first.

    Folds the append-only log so the latest line per ``claim_id`` wins, and
    skips malformed lines so one truncated write never makes the log unreadable
    (identical semantics to ``submissions/regrade.read_regrade_requests``).
    """
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
        # A strictly newer updated_at wins; equal/absent falls back to file order.
        if existing is None or record.updated_at >= existing.updated_at:
            latest[record.claim_id] = record

    return sorted(latest.values(), key=lambda c: (c.created_at, c.claim_id))


def active_claims(submissions_path: str | Path) -> list[WorkClaim]:
    """Just the claims still held (what coordination cares about)."""
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
    """Record that ``actor`` has taken ``work``. Returns the stored claim.

    Raises :class:`ClaimConflictError` when the slice overlaps another grader's
    active claim, unless ``allow_overlap``. Overlapping *your own* claims is
    always fine — re-claiming or widening your slice is not a double-booking.

    Claiming the entire assignment (an empty slice on both axes) needs
    ``allow_everything``, because that is far more often a caller that computed
    an empty share than a grader who genuinely means "all of it".
    """
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
    """Give a slice back to the pool (someone else may take it). ``None`` if unknown."""
    return _close_claim(submissions_path, claim_id, status="released", actor=actor)


def complete_claim(
    submissions_path: str | Path, claim_id: str, *, actor: str | None = None
) -> WorkClaim | None:
    """Mark a slice finished — released, and recorded as *done*. ``None`` if unknown.

    Kept distinct from a release on purpose: "I finished my forty" and "I am
    handing mine back" are different answers to "is this assignment graded yet".
    """
    return _close_claim(submissions_path, claim_id, status="completed", actor=actor)


# --------------------------------------------------------------------------- #
# Queries (pure — the TUI and CLI both route through these)
# --------------------------------------------------------------------------- #


def find_conflicts(
    work: WorkSlice, claims: Iterable[WorkClaim], *, actor: str
) -> list[WorkClaim]:
    """Active claims by *other* graders whose work overlaps ``work``."""
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
    """The active claim covering one unit of work, or ``None`` if unclaimed.

    First match wins; with overlapping claims (which :func:`claim_work` refuses
    to create by default) the answer is simply the earliest, and
    :func:`coverage_report` is where a human is told the overlap exists.
    """
    for claim in claims:
        if claim.is_active and claim.covers(question_id, student_id):
            return claim
    return None

def is_owned_by_other(
    claims: Iterable[WorkClaim], question_id: str, student_id: str, *, actor: str
) -> bool:
    """Whether this work belongs to a grader who is not ``actor``.

    The predicate a front-end filters a queue with. **Unclaimed work is not
    owned by anyone**, so it returns False — an assignment nobody has divided
    behaves exactly as it did before claims existed, and a partial division
    leaves the remainder open to whoever gets there.
    """
    owner = claim_for(claims, question_id, student_id)
    return owner is not None and owner.actor != actor


def divide_work(
    graders: Sequence[str],
    *,
    question_ids: Sequence[str] = (),
    student_ids: Sequence[str] = (),
    axis: DivideAxis = "question",
) -> list[tuple[str, WorkSlice]]:
    """Split the cohort into one contiguous slice per grader.

    ``axis`` picks what gets cut: ``"question"`` gives each grader a run of
    questions across every student (the usual choice — a grader who marks q1-q3
    for everyone applies one rubric consistently), ``"student"`` gives each a run
    of students across every question (better when questions are interdependent
    or the marking is holistic).

    Slices are contiguous and in order, so "TA2 has q4-q6" is something a person
    can hold in their head. Remainders go to the earliest graders, and a grader
    who would receive **nothing is dropped rather than given an empty slice** —
    an empty slice means *everything* (see :class:`WorkSlice`), which would hand
    one person the whole assignment.
    """
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
    """Summarize who has what, what nobody has, and what is double-booked."""
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


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _axis_overlaps(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    """Whether two axis selections intersect; an empty axis means "all"."""
    if not left or not right:
        return True
    return bool(set(left) & set(right))


# How many question ids to spell out before a claim's summary switches to a
# count. Real assignments run to a dozen or so questions, and a grader has to be
# able to read which ones are theirs off one line.
_MAX_LISTED_QUESTIONS = 8


def _describe_questions(ids: tuple[str, ...]) -> str:
    """"q1-q3" for a run, "q1, q2, q5a" for a short set, a count for a long one."""
    if not ids:
        return ""
    if len(ids) == 1:
        return ids[0]
    if _is_contiguous_run(ids):
        return f"{ids[0]}-{ids[-1]}"
    if len(ids) <= _MAX_LISTED_QUESTIONS:
        # Subquestions (q5a/q5b) break the numeric run, and they are exactly the
        # case where naming the ids matters most.
        return ", ".join(ids)
    return f"{ids[0]}…{ids[-1]} ({len(ids)} questions)"


def _describe_students(ids: tuple[str, ...]) -> str:
    """A count, because student ids are opaque — except for a single student."""
    if not ids:
        return ""
    if len(ids) == 1:
        return f"student {ids[0]}"
    return f"{len(ids)} students"


def _is_contiguous_run(ids: tuple[str, ...]) -> bool:
    """Whether ids look like ``q4, q5, q6`` — a run worth writing as a range."""
    numbers = []
    for value in ids:
        digits = "".join(char for char in value if char.isdigit())
        if not digits or not value.endswith(digits):
            return False
        numbers.append(int(digits))
    return all(b - a == 1 for a, b in zip(numbers, numbers[1:]))


def _even_chunks(items: Sequence[str], parts: int) -> list[list[str]]:
    """Split into ``parts`` contiguous chunks, remainder to the earliest ones."""
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
