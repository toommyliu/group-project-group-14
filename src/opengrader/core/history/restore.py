"""Time travel over the grade-history journal — reconstruct and restore grades.

The journal (`journal.py`) already records ``old`` and ``new`` for every grading
action, but nothing ever read ``old`` back except the TUI's single-step in-memory
undo, which dies with the session. This module turns the journal into what its
contents always implied: a full history the grades can be rewound to.

Three questions it answers, all from the same replay:

* *What were the grades at 10:00 this morning?* — ``replay_journal(at=…)``
* *What would they be if that grader (or that batch run) had never happened?* —
  ``replay_journal(exclude_actors=…)``
* *What has to change on disk to get there?* — ``plan_restore`` /
  ``apply_restore_plan``

Design decisions:

* **Replay is pure, application is not.** Everything down to ``plan_restore``
  is a function over journal entries and caller-supplied current state, so the
  whole reconstruction unit-tests with no files and no journal on disk. Only
  ``apply_restore_plan`` touches the filesystem (the same split
  `submissions/scan_check.py` uses).
* **The journal says where to go; the *file* says where we are.** A plan is
  always diffed against the submissions as they are right now, never against
  the journal's idea of "current". The journal can be incomplete (a
  hand-edited file, a tool that predates journalling), and silently writing a
  value that is already there — or missing one that drifted — would make
  restore untrustworthy exactly when it matters.
* **The first entry's ``old`` is the pre-journal baseline.** That is what makes
  "restore to a time before this question was ever touched" work: with no entry
  at or before the target time we take the *earliest* entry's ``old``, which is
  by construction the state the journal started from.
* **Only per-answer aspects are restorable** — ``grade`` (points + rubric),
  ``comment`` (analysis) and ``transcription`` (response text). ``rubric``
  events edit the shared QA file and propagate across students, and ``identity``
  events go through a different write path (`set_student_identity_file`) where
  un-assigning an id would strand a submission; both are deliberately excluded,
  the same call the in-session undo makes. Identity events are still *read*,
  because they are what tells us that today's id 012345 is the same person as
  yesterday's blank one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable, Literal, Mapping, Sequence

from opengrader.core.qa_file.scoring import EPSILON

from .journal import GradeJournal, GradeJournalEntry

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    from opengrader.core.submissions.student_file import (
        StudentSubmissionDocument,
        StudentSubmissionGradeUpdate,
    )

# The journal kinds a restore can put back. Each maps to a set of fields that
# always move together, because the action that wrote them wrote them together:
# a grade is meaningless with its points restored but its rubric selection left
# behind. Anything not listed here is read for context but never written.
Aspect = Literal["grade", "comment", "transcription"]
RESTORABLE_ASPECTS: tuple[Aspect, ...] = ("grade", "comment", "transcription")

_ASPECT_LABELS: dict[Aspect, str] = {
    "grade": "grade",
    "comment": "comment",
    "transcription": "transcription",
}

# "2h", "45m", "1d", "90s" — a relative age, which is how people actually
# describe the thing they want undone ("put it back to an hour ago").
_RELATIVE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([smhdw])$", re.IGNORECASE)
_RELATIVE_UNITS = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}

# Accepted absolute forms, tried in order. Date-only means the start of that
# day, which is the intuitive reading of "--at 2026-08-01".
_ABSOLUTE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


class RestoreError(ValueError):
    """A restore could not be planned or applied (bad time, unknown student)."""


@dataclass(frozen=True)
class AspectValue:
    """The value of one aspect of one answer.

    Only the fields belonging to the aspect are meaningful: ``points``/``rubric``
    for a grade, ``text`` for a comment (the Analysis field) or a transcription
    (the Response text field). Keeping one shape for all three is what lets the
    planner, the renderer and the applier stay aspect-agnostic.

    ``confidence``/``source`` ride along with a **transcription** only. They are
    not part of what a restore *decides* — see :meth:`matches` — but they are
    part of what it must *write*: putting a machine's old words back while
    leaving ``Read by: human`` and ``Confidence: 100`` in place would make the
    file claim a person typed something no person ever typed, which is exactly
    the sort of quiet dishonesty the review queue then trusts.
    """

    points: float | None = None
    rubric: tuple[str, ...] = ()
    text: str = ""
    confidence: float | None = None
    source: str | None = None

    def matches(self, other: "AspectValue", aspect: Aspect) -> bool:
        """Compare only the fields the aspect actually owns.

        A transcription is its **words**. Provenance deliberately does not enter
        the comparison: a reading whose text is unchanged is not a change worth
        rewriting a file for, and treating a differing ``Read by`` as one would
        propose no-op restores for every answer whose source predates the field.
        """
        if aspect == "grade":
            return _points_equal(self.points, other.points) and self.rubric == other.rubric
        return self.text == other.text


@dataclass(frozen=True)
class CellState:
    """The reconstructed state of one (student, question) pair.

    ``known`` is load-bearing: an aspect absent from it means the journal has
    *no information*, which is a different thing from a known-empty value. A
    question nobody ever graded and a question explicitly set back to ungraded
    both end up with ``points=None``, and only ``known`` tells them apart — so
    only aspects in ``known`` are ever proposed as changes.
    """

    values: Mapping[Aspect, AspectValue] = field(default_factory=dict)

    @property
    def known(self) -> frozenset[Aspect]:
        return frozenset(self.values)

    def value(self, aspect: Aspect) -> AspectValue | None:
        return self.values.get(aspect)


@dataclass(frozen=True)
class JournalSnapshot:
    """Every reconstructed cell at one point in time.

    ``aliases`` maps a historical student key to the key that same person is
    filed under today, and ``labels`` remembers the best display name seen for
    each key, so a report can name a student the journal only ever saw as an id.
    """

    at: datetime | None
    cells: Mapping[tuple[str, str], CellState]
    aliases: Mapping[str, str]
    labels: Mapping[str, str]
    unattributed_entries: int = 0

    def cell(self, student_key: str, question_id: str) -> CellState | None:
        return self.cells.get((student_key, question_id))

    def student_keys(self) -> tuple[str, ...]:
        return tuple(sorted({key for key, _ in self.cells}))

    def label_for(self, student_key: str) -> str:
        return self.labels.get(student_key, student_key)


@dataclass(frozen=True)
class RestoreChange:
    """One field-group that has to be written to reach the target state."""

    student_key: str
    student_label: str
    question_id: str
    aspect: Aspect
    before: AspectValue
    after: AspectValue

    def describe(self) -> str:
        return (
            f"{self.student_label} · {self.question_id} · {_ASPECT_LABELS[self.aspect]}: "
            f"{format_aspect_value(self.aspect, self.before)} → "
            f"{format_aspect_value(self.aspect, self.after)}"
        )


@dataclass(frozen=True)
class RestorePlan:
    """The complete set of changes, plus what could not be decided.

    ``unresolved`` names students the journal knows about but the caller could
    not match to a submission on disk (a deleted or renamed file). They are
    surfaced rather than dropped: a restore that silently skipped a student
    would be worse than one that says so.
    """

    changes: tuple[RestoreChange, ...] = ()
    unresolved: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.changes)

    @property
    def student_count(self) -> int:
        return len({change.student_key for change in self.changes})


def _points_equal(left: float | None, right: float | None) -> bool:
    """Compare awarded points, tolerating float noise but not None-vs-zero."""
    if left is None or right is None:
        return left is None and right is None
    return abs(left - right) <= EPSILON


def format_aspect_value(aspect: Aspect, value: AspectValue) -> str:
    """Render one aspect value for a human-readable diff line."""
    if aspect == "grade":
        points = "ungraded" if value.points is None else f"{value.points:g}"
        if value.rubric:
            return f"{points} [{', '.join(value.rubric)}]"
        return points
    text = value.text.strip()
    if not text:
        return "(empty)"
    collapsed = " ".join(text.split())
    return f'"{collapsed[:57]}…"' if len(collapsed) > 58 else f'"{collapsed}"'


def parse_restore_time(raw: str, *, now: datetime | None = None) -> datetime:
    """Parse a user-supplied point in time into an aware UTC datetime.

    Accepts a relative age (``2h``, ``30m``, ``1d``) and the common absolute
    spellings. A naive absolute time is read as the user's **local** clock and
    converted to UTC — the journal stores UTC, but nobody types UTC when they
    mean "before lunch".
    """
    text = raw.strip()
    if not text:
        raise RestoreError("empty timestamp")

    reference = now or datetime.now(timezone.utc)

    relative = _RELATIVE_RE.match(text)
    if relative is not None:
        amount = float(relative.group(1))
        unit = _RELATIVE_UNITS[relative.group(2).lower()]
        return reference - timedelta(**{unit: amount})

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in _ABSOLUTE_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        raise RestoreError(
            f"could not read {raw!r} as a time — try 2026-08-01T14:30, "
            "2026-08-01, or a relative age like 2h"
        )
    if parsed.tzinfo is None:
        # Naive input is wall-clock time on this machine.
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def _entry_time(entry: GradeJournalEntry) -> datetime | None:
    """Parse an entry's ISO timestamp, treating an unreadable one as unknown."""
    try:
        parsed = datetime.fromisoformat(entry.timestamp)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def student_key(student_id: str, student_name: str) -> str:
    """The stable key a journal entry is filed under.

    A real student id wins. Before an id is assigned the name is the only
    handle we have, so it is used with a ``name:`` prefix that can never
    collide with an id. An entry with neither is genuinely unattributable and
    gets an empty key, which the replay counts and refuses to act on rather
    than guessing which anonymous submission it meant.
    """
    normalized_id = (student_id or "").strip()
    if normalized_id:
        return normalized_id.casefold()
    normalized_name = " ".join((student_name or "").split()).casefold()
    if normalized_name:
        return f"name:{normalized_name}"
    return ""


def _build_aliases(entries: Sequence[GradeJournalEntry]) -> dict[str, str]:
    """Map every historical student key onto the key in use today.

    Identity events are the only record that the submission graded under a
    blank id yesterday is the one filed under 012345 today. Chains are
    followed, so an id corrected twice still lands on the current one.

    Identity entries are read regardless of the target time or excluded
    actors: *who someone is* is not part of the state being rewound, and we
    always need today's key to find today's file.
    """
    direct: dict[str, str] = {}
    for entry in entries:
        if entry.kind != "identity":
            continue
        old_key = student_key(
            str(entry.old.get("student_id", "")),
            str(entry.old.get("student_name", "")),
        )
        new_key = student_key(
            str(entry.new.get("student_id", "")),
            str(entry.new.get("student_name", "")),
        )
        if not old_key or not new_key or old_key == new_key:
            continue
        direct[old_key] = new_key

    resolved: dict[str, str] = {}
    for start in direct:
        seen = {start}
        current = start
        while current in direct:
            nxt = direct[current]
            if nxt in seen:  # defensive: a cycle from a corrupt journal
                break
            seen.add(nxt)
            current = nxt
        resolved[start] = current
    return resolved


def _aspect_for_kind(kind: str) -> Aspect | None:
    if kind in RESTORABLE_ASPECTS:
        return kind  # type: ignore[return-value]
    return None


def _aspect_value(aspect: Aspect, payload: Mapping[str, Any]) -> AspectValue:
    """Read one side (``old``/``new``) of an entry into an AspectValue.

    The journal is on disk and hand-editable, so every field is coerced
    defensively — a malformed value degrades to the empty default rather than
    raising and making the whole history unreadable.
    """
    if aspect == "grade":
        raw_points = payload.get("points")
        points: float | None
        try:
            points = None if raw_points is None else float(raw_points)
        except (TypeError, ValueError):
            points = None
        raw_rubric = payload.get("rubric")
        rubric: tuple[str, ...]
        if isinstance(raw_rubric, (list, tuple)):
            rubric = tuple(str(label) for label in raw_rubric)
        else:
            rubric = ()
        return AspectValue(points=points, rubric=rubric)

    if aspect == "comment":
        raw_comment = payload.get("analysis")
        return AspectValue(text="" if raw_comment is None else str(raw_comment))

    raw_text = payload.get("response_text")
    raw_confidence = payload.get("response_confidence")
    confidence: float | None
    try:
        confidence = None if raw_confidence is None else float(raw_confidence)
    except (TypeError, ValueError):
        confidence = None
    raw_source = payload.get("response_source")
    # Absent on every entry written before provenance was recorded, which is why
    # it degrades to None rather than to a name: "we don't know who read this"
    # is the honest answer and the writer below leaves the file's own claim
    # alone when it gets it.
    source = str(raw_source) if isinstance(raw_source, str) and raw_source.strip() else None
    return AspectValue(
        text="" if raw_text is None else str(raw_text),
        confidence=confidence,
        source=source,
    )


@dataclass
class _Timeline:
    """Every entry for one (student, question, aspect), in time order."""

    entries: list[tuple[datetime, GradeJournalEntry, bool]] = field(default_factory=list)

    def resolve(self, at: datetime | None, aspect: Aspect) -> AspectValue | None:
        """The value this aspect held at ``at``, ignoring excluded entries.

        Falls back to the earliest entry's ``old`` — the state the journal
        started from — when nothing eligible precedes the target time. That
        fallback is what lets a restore reach back past the first recorded
        change, and it is also what makes ``exclude_actors`` able to undo a
        grader who was the only one to ever touch a question.
        """
        eligible = [
            (stamp, entry)
            for stamp, entry, included in self.entries
            if included and (at is None or stamp <= at)
        ]
        if eligible:
            return _aspect_value(aspect, eligible[-1][1].new)
        if self.entries:
            return _aspect_value(aspect, self.entries[0][1].old)
        return None


def replay_journal(
    entries: Iterable[GradeJournalEntry],
    *,
    at: datetime | None = None,
    exclude_actors: Iterable[str] = (),
) -> JournalSnapshot:
    """Reconstruct every answer's state at a point in time.

    ``at=None`` replays the whole journal (the state its last entry left
    behind). ``exclude_actors`` drops those graders' entries from the replay,
    answering "what would this be if they had never touched it" — the shape a
    bad batch run needs. The two compose.
    """
    ordered: list[tuple[datetime, GradeJournalEntry]] = []
    undated: list[GradeJournalEntry] = []
    all_entries = list(entries)
    for entry in all_entries:
        stamp = _entry_time(entry)
        if stamp is None:
            undated.append(entry)
            continue
        ordered.append((stamp, entry))
    # A stable sort on the timestamp keeps same-instant entries in journal
    # order, so the later line still wins — two saves inside one clock tick
    # are common when a comment is coalesced with its grade.
    ordered.sort(key=lambda item: item[0])

    excluded = {actor.strip().casefold() for actor in exclude_actors if actor.strip()}
    aliases = _build_aliases(all_entries)

    timelines: dict[tuple[str, str, Aspect], _Timeline] = {}
    labels: dict[str, str] = {}
    unattributed = 0

    for stamp, entry in ordered:
        aspect = _aspect_for_kind(entry.kind)
        if aspect is None:
            # Not restorable (rubric/identity), but identity entries still
            # carry the best-known name for a key, so record that.
            if entry.kind == "identity":
                key = student_key(
                    str(entry.new.get("student_id", "")),
                    str(entry.new.get("student_name", "")),
                )
                name = str(entry.new.get("student_name", "")).strip()
                if key and name:
                    labels[key] = name
            continue

        raw_key = student_key(entry.student_id, entry.student_name)
        if not raw_key:
            unattributed += 1
            continue
        key = aliases.get(raw_key, raw_key)
        if entry.student_name.strip():
            labels[key] = entry.student_name.strip()
        labels.setdefault(key, entry.student_id.strip() or key)

        included = entry.actor.strip().casefold() not in excluded
        timeline = timelines.setdefault((key, entry.question_id, aspect), _Timeline())
        timeline.entries.append((stamp, entry, included))

    cells: dict[tuple[str, str], dict[Aspect, AspectValue]] = {}
    for (key, question_id, aspect), timeline in timelines.items():
        value = timeline.resolve(at, aspect)
        if value is None:
            continue
        cells.setdefault((key, question_id), {})[aspect] = value

    return JournalSnapshot(
        at=at,
        cells={key: CellState(values=values) for key, values in cells.items()},
        aliases=aliases,
        labels=labels,
        unattributed_entries=unattributed + len(undated),
    )


def plan_restore(
    snapshot: JournalSnapshot,
    current: Mapping[tuple[str, str], CellState],
    *,
    students: Iterable[str] = (),
    questions: Iterable[str] = (),
    aspects: Iterable[Aspect] = RESTORABLE_ASPECTS,
    known_students: Iterable[str] | None = None,
) -> RestorePlan:
    """Diff a reconstructed snapshot against the state on disk today.

    ``current`` is supplied by the caller from the real ``.submission.md``
    files — the journal is never trusted to say what is there now. Only
    aspects the journal actually knows about are considered, so a question it
    has no record of is left exactly as it is.

    ``known_students`` (when given) is the set of student keys that could be
    matched to a submission; anyone the snapshot mentions who is not in it is
    reported in ``RestorePlan.unresolved`` instead of being quietly skipped.
    """
    wanted_students = {key.strip().casefold() for key in students if key.strip()}
    wanted_questions = {qid.strip().casefold() for qid in questions if qid.strip()}
    wanted_aspects = tuple(aspects)
    resolvable = None if known_students is None else set(known_students)

    changes: list[RestoreChange] = []
    unresolved: set[str] = set()

    for (key, question_id), target in sorted(snapshot.cells.items()):
        if wanted_students and key not in wanted_students:
            continue
        if wanted_questions and question_id.strip().casefold() not in wanted_questions:
            continue
        if resolvable is not None and key not in resolvable:
            unresolved.add(key)
            continue

        present = current.get((key, question_id))
        for aspect in wanted_aspects:
            after = target.value(aspect)
            if after is None:
                continue
            before = (
                present.value(aspect)
                if present is not None and present.value(aspect) is not None
                else AspectValue()
            )
            assert before is not None  # narrowed above
            if before.matches(after, aspect):
                continue
            changes.append(
                RestoreChange(
                    student_key=key,
                    student_label=snapshot.label_for(key),
                    question_id=question_id,
                    aspect=aspect,
                    before=before,
                    after=after,
                )
            )

    changes.sort(
        key=lambda change: (
            change.student_label.lower(),
            change.student_key,
            change.question_id,
            RESTORABLE_ASPECTS.index(change.aspect),
        )
    )
    return RestorePlan(changes=tuple(changes), unresolved=tuple(sorted(unresolved)))


# --------------------------------------------------------------------------
# Everything below touches the filesystem. Keep new logic above this line so
# it stays unit-testable without a submissions folder.
# --------------------------------------------------------------------------


def document_student_key(document: "StudentSubmissionDocument") -> str:
    """The journal key a submission file is filed under, by the same rules."""
    return student_key(document.student_id, document.student_name)


def current_cells_from_documents(
    documents: Iterable["StudentSubmissionDocument"],
) -> dict[tuple[str, str], CellState]:
    """Read today's on-disk state into the shape ``plan_restore`` diffs against.

    Every aspect is marked known here — unlike the journal, a submission file
    always has a definite answer for all three (an empty comment *is* the
    comment). Pure: it takes parsed documents, not paths.
    """
    cells: dict[tuple[str, str], CellState] = {}
    for document in documents:
        key = document_student_key(document)
        if not key:
            continue
        for question in document.questions:
            cells[(key, question.question_id)] = CellState(
                values={
                    "grade": AspectValue(
                        points=question.points_awarded,
                        rubric=tuple(question.rubric_labels),
                    ),
                    "comment": AspectValue(text=question.analysis or ""),
                    "transcription": AspectValue(
                        text=question.response_text or "",
                        confidence=question.response_confidence,
                        source=question.response_source,
                    ),
                }
            )
    return cells


def merge_update_for_question(
    question_id: str,
    aspects: Mapping[Aspect, AspectValue],
) -> "StudentSubmissionGradeUpdate":
    """Fold every restored aspect of one question into a single file update.

    One write per question rather than per aspect, so a grade+comment restore
    cannot leave the file half-updated if the second write fails.

    Public because a restore is not the only thing that writes a reconstructed
    answer back: `core/handoff/merge.py` applies a merged value through the same
    call, and two copies of these rules would eventually disagree about what a
    grade *is*.

    Note the ``grade`` case: ``StudentSubmissionGradeUpdate`` reads ``None`` as
    "leave unchanged", so restoring a question *back to ungraded* would be
    impossible on ``points_awarded`` alone. Passing ``rubric_labels`` (even
    empty) is what makes the writer emit the Points line, which is how a blank
    — genuinely ungraded — value gets written. Always send both together.
    """
    # Imported here, not at module scope: student_file pulls in OpenCV/PIL via
    # the extractor, and the replay half of this module must stay importable
    # (and fast) without them.
    from opengrader.core.submissions.student_file import StudentSubmissionGradeUpdate

    update_kwargs: dict[str, Any] = {"question_id": question_id}
    grade = aspects.get("grade")
    if grade is not None:
        update_kwargs["points_awarded"] = grade.points
        update_kwargs["rubric_labels"] = grade.rubric
    comment = aspects.get("comment")
    if comment is not None:
        update_kwargs["analysis"] = comment.text
    transcription = aspects.get("transcription")
    if transcription is not None:
        update_kwargs["response_text"] = transcription.text
        # Put the reading's own confidence and reader back with its words, so a
        # restored machine transcription cannot keep sitting under the "human,
        # 100" the correction that is being undone wrote. Both are conditional
        # on the journal having recorded them: entries written before
        # provenance existed carry neither, and there the old rule still
        # applies — leave the file's claim alone rather than invent one.
        if transcription.source is not None:
            update_kwargs["response_source"] = transcription.source
        if transcription.confidence is not None:
            update_kwargs["response_confidence"] = transcription.confidence
        elif transcription.source is not None:
            # A recorded source with no number is a model reading: unverified is
            # the truth, and `response_confidence=None` cannot say it (that
            # means "leave unchanged"), so use the field that can.
            update_kwargs["clear_response_confidence"] = True
    return StudentSubmissionGradeUpdate(**update_kwargs)


@dataclass(frozen=True)
class RestoreOutcome:
    """What ``apply_restore_plan`` actually did."""

    written: tuple[RestoreChange, ...] = ()
    failed: tuple[tuple[RestoreChange, str], ...] = ()

    @property
    def files_touched(self) -> int:
        return len({change.student_key for change in self.written})


def apply_restore_plan(
    plan: RestorePlan,
    paths_by_key: Mapping[str, "Path"],
    *,
    journal: GradeJournal | None = None,
    reason: str = "",
) -> RestoreOutcome:
    """Write a plan's changes to the submission files.

    Each (student, question) is written once with every restored aspect
    merged. A failure on one question is recorded and the rest of the plan
    still runs: a restore that aborts halfway leaves a cohort in a state
    nobody asked for, which is worse than a partial one that says exactly
    which answers it could not write.

    When a ``journal`` is given the restore records itself, with ``old`` set to
    what was on disk and ``new`` to what was written. That keeps the audit
    trail honest (a restore is a grade change like any other) and — because it
    is an ordinary journal entry — makes the restore itself restorable.
    """
    from opengrader.core.submissions.student_file import update_student_submission_file

    grouped: dict[tuple[str, str], dict[Aspect, RestoreChange]] = {}
    for change in plan.changes:
        grouped.setdefault((change.student_key, change.question_id), {})[
            change.aspect
        ] = change

    written: list[RestoreChange] = []
    failed: list[tuple[RestoreChange, str]] = []

    for (key, question_id), by_aspect in sorted(grouped.items()):
        path = paths_by_key.get(key)
        if path is None:
            for change in by_aspect.values():
                failed.append((change, "no submission file for this student"))
            continue
        update = merge_update_for_question(
            question_id,
            {aspect: change.after for aspect, change in by_aspect.items()},
        )
        try:
            update_student_submission_file(path, update)
        except (OSError, ValueError) as exc:
            for change in by_aspect.values():
                failed.append((change, str(exc)))
            continue

        for aspect, change in by_aspect.items():
            written.append(change)
            if journal is None:
                continue
            try:
                journal.append(
                    kind=aspect,
                    student_id=key if not key.startswith("name:") else "",
                    student_name=change.student_label,
                    question_id=question_id,
                    summary=_restore_summary(change, reason),
                    old=journal_payload(aspect, change.before),
                    new=journal_payload(aspect, change.after),
                )
            except OSError:
                # Accountability is best-effort, exactly as it is on a normal
                # save: a read-only history dir must never fail the write that
                # already happened.
                pass

    return RestoreOutcome(written=tuple(written), failed=tuple(failed))


def _restore_summary(change: RestoreChange, reason: str) -> str:
    base = (
        f"restored {_ASPECT_LABELS[change.aspect]} → "
        f"{format_aspect_value(change.aspect, change.after)}"
    )
    return f"{base} ({reason})" if reason else base


def journal_payload(aspect: Aspect, value: AspectValue) -> dict[str, Any]:
    """Render an AspectValue back into the journal's ``old``/``new`` shape.

    Public for the same reason as `merge_update_for_question`: every writer of a
    reconstructed value has to record it in the journal the same way, or a later
    replay reads the two writers' entries differently.
    """
    if aspect == "grade":
        return {"points": value.points, "rubric": list(value.rubric)}
    if aspect == "comment":
        return {"analysis": value.text}
    payload: dict[str, Any] = {"response_text": value.text}
    # Emitted only when known, so an entry about a reading with no recorded
    # provenance keeps exactly the shape it has always had — the same rule the
    # optional `.submission.md` fields follow, and what keeps old and new
    # journals comparable.
    if value.confidence is not None:
        payload["response_confidence"] = value.confidence
    if value.source is not None:
        payload["response_source"] = value.source
    return payload
