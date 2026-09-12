from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable, Literal, Mapping, Sequence

from opengrader.core.qa_file.scoring import EPSILON

from .journal import GradeJournal, GradeJournalEntry

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

    from opengrader.core.submissions.student_file import (
        StudentSubmissionDocument,
        StudentSubmissionGradeUpdate,
    )

Aspect = Literal["grade", "comment", "transcription"]
RESTORABLE_ASPECTS: tuple[Aspect, ...] = ("grade", "comment", "transcription")

_ASPECT_LABELS: dict[Aspect, str] = {
    "grade": "grade",
    "comment": "comment",
    "transcription": "transcription",
}

_RELATIVE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([smhdw])$", re.IGNORECASE)
_RELATIVE_UNITS = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}

_ABSOLUTE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


class RestoreError(ValueError):
    pass


@dataclass(frozen=True)
class AspectValue:
    """Transcription confidence and reader are restored with the text but excluded from
    value matching.
    """

    points: float | None = None
    rubric: tuple[str, ...] = ()
    text: str = ""
    confidence: float | None = None
    source: str | None = None

    def matches(self, other: "AspectValue", aspect: Aspect) -> bool:
        if aspect == "grade":
            return _points_equal(self.points, other.points) and self.rubric == other.rubric
        return self.text == other.text


@dataclass(frozen=True)
class CellState:
    values: Mapping[Aspect, AspectValue] = field(default_factory=dict)

    @property
    def known(self) -> frozenset[Aspect]:
        return frozenset(self.values)

    def value(self, aspect: Aspect) -> AspectValue | None:
        return self.values.get(aspect)


@dataclass(frozen=True)
class JournalSnapshot:
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
    changes: tuple[RestoreChange, ...] = ()
    unresolved: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.changes)

    @property
    def student_count(self) -> int:
        return len({change.student_key for change in self.changes})


def _points_equal(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return abs(left - right) <= EPSILON


def format_aspect_value(aspect: Aspect, value: AspectValue) -> str:
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
        # Interpret naive timestamps in the machine's local timezone.
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def _entry_time(entry: GradeJournalEntry) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(entry.timestamp)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def student_key(student_id: str, student_name: str) -> str:
    normalized_id = (student_id or "").strip()
    if normalized_id:
        return normalized_id.casefold()
    normalized_name = " ".join((student_name or "").split()).casefold()
    if normalized_name:
        return f"name:{normalized_name}"
    return ""


def _build_aliases(entries: Sequence[GradeJournalEntry]) -> dict[str, str]:
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
            if nxt in seen:
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
    source = str(raw_source) if isinstance(raw_source, str) and raw_source.strip() else None
    return AspectValue(
        text="" if raw_text is None else str(raw_text),
        confidence=confidence,
        source=source,
    )


@dataclass
class _Timeline:
    entries: list[tuple[datetime, GradeJournalEntry, bool]] = field(default_factory=list)

    def resolve(self, at: datetime | None, aspect: Aspect) -> AspectValue | None:
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
    ordered: list[tuple[datetime, GradeJournalEntry]] = []
    undated: list[GradeJournalEntry] = []
    all_entries = list(entries)
    for entry in all_entries:
        stamp = _entry_time(entry)
        if stamp is None:
            undated.append(entry)
            continue
        ordered.append((stamp, entry))
    ordered.sort(key=lambda item: item[0])

    excluded = {actor.strip().casefold() for actor in exclude_actors if actor.strip()}
    aliases = _build_aliases(all_entries)

    timelines: dict[tuple[str, str, Aspect], _Timeline] = {}
    labels: dict[str, str] = {}
    unattributed = 0

    for stamp, entry in ordered:
        aspect = _aspect_for_kind(entry.kind)
        if aspect is None:
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
            assert before is not None
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


def document_student_key(document: "StudentSubmissionDocument") -> str:
    return student_key(document.student_id, document.student_name)


def current_cells_from_documents(
    documents: Iterable["StudentSubmissionDocument"],
) -> dict[tuple[str, str], CellState]:
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
    """points=None cannot clear a grade without rubric_labels, even if the labels are empty."""
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
        if transcription.source is not None:
            update_kwargs["response_source"] = transcription.source
        if transcription.confidence is not None:
            update_kwargs["response_confidence"] = transcription.confidence
        elif transcription.source is not None:
            update_kwargs["clear_response_confidence"] = True
    return StudentSubmissionGradeUpdate(**update_kwargs)


@dataclass(frozen=True)
class RestoreOutcome:
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
                pass

    return RestoreOutcome(written=tuple(written), failed=tuple(failed))


def _restore_summary(change: RestoreChange, reason: str) -> str:
    base = (
        f"restored {_ASPECT_LABELS[change.aspect]} → "
        f"{format_aspect_value(change.aspect, change.after)}"
    )
    return f"{base} ({reason})" if reason else base


def journal_payload(aspect: Aspect, value: AspectValue) -> dict[str, Any]:
    if aspect == "grade":
        return {"points": value.points, "rubric": list(value.rubric)}
    if aspect == "comment":
        return {"analysis": value.text}
    payload: dict[str, Any] = {"response_text": value.text}
    if value.confidence is not None:
        payload["response_confidence"] = value.confidence
    if value.source is not None:
        payload["response_source"] = value.source
    return payload
