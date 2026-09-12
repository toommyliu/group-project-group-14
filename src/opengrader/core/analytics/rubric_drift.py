from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from opengrader.core.history.journal import GradeJournalEntry
from opengrader.core.history.restore import student_key


@dataclass(frozen=True)
class RubricChange:
    question_id: str
    timestamp: str
    actor: str
    old_options: tuple[str, ...]
    new_options: tuple[str, ...]

    @property
    def added(self) -> tuple[str, ...]:
        return tuple(label for label in self.new_options if label not in self.old_options)

    @property
    def removed(self) -> tuple[str, ...]:
        return tuple(label for label in self.old_options if label not in self.new_options)

    def describe(self) -> str:
        parts: list[str] = []
        if self.added:
            parts.append("added " + ", ".join(repr(label) for label in self.added))
        if self.removed:
            parts.append("removed " + ", ".join(repr(label) for label in self.removed))
        if not parts:
            # The journal cannot distinguish point or ordering changes from rubric labels alone.
            return "rubric options changed"
        return " and ".join(parts)


@dataclass(frozen=True)
class StaleGrade:
    student_key: str
    student_id: str
    student_name: str
    question_id: str
    graded_at: str
    graded_by: str
    change: RubricChange

    @property
    def label(self) -> str:
        return self.student_name or self.student_id or self.student_key


def _options(payload: dict) -> tuple[str, ...]:
    raw = payload.get("options")
    if isinstance(raw, (list, tuple)):
        return tuple(str(label) for label in raw)
    return ()


def find_rubric_changes(entries: Sequence[GradeJournalEntry]) -> dict[str, RubricChange]:
    changes: dict[str, RubricChange] = {}
    for entry in entries:
        if entry.kind != "rubric" or not entry.question_id:
            continue
        old_options = _options(entry.old)
        new_options = _options(entry.new)
        if old_options == new_options:
            continue
        change = RubricChange(
            question_id=entry.question_id,
            timestamp=entry.timestamp,
            actor=entry.actor,
            old_options=old_options,
            new_options=new_options,
        )
        existing = changes.get(entry.question_id)
        if existing is None or change.timestamp >= existing.timestamp:
            changes[entry.question_id] = change
    return changes


def detect_rubric_drift(entries: Sequence[GradeJournalEntry]) -> list[StaleGrade]:
    changes = find_rubric_changes(entries)
    if not changes:
        return []

    latest_grade: dict[tuple[str, str], GradeJournalEntry] = {}
    for entry in entries:
        if entry.kind != "grade" or not entry.question_id:
            continue
        if entry.question_id not in changes:
            continue
        key = student_key(entry.student_id, entry.student_name)
        if not key:
            continue
        previous = latest_grade.get((key, entry.question_id))
        if previous is None or entry.timestamp >= previous.timestamp:
            latest_grade[(key, entry.question_id)] = entry

    stale: list[StaleGrade] = []
    for (key, question_id), entry in latest_grade.items():
        change = changes[question_id]
        if entry.timestamp >= change.timestamp:
            continue
        stale.append(
            StaleGrade(
                student_key=key,
                student_id=entry.student_id,
                student_name=entry.student_name,
                question_id=question_id,
                graded_at=entry.timestamp,
                graded_by=entry.actor,
                change=change,
            )
        )

    stale.sort(key=lambda item: (item.question_id, item.label.lower(), item.student_key))
    return stale
