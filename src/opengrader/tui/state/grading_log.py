from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal

GradingLogKind = Literal["grade", "comment", "rubric", "transcription"]

MAX_LOG_ENTRIES = 200


@dataclass(frozen=True)
class GradeRevert:
    kind: GradingLogKind
    student_item_id: str
    question_id: str
    possible_points: float | None = None
    points_awarded: float | None = None
    rubric_labels: tuple[str, ...] | None = None
    analysis: str | None = None
    response_text: str | None = None
    response_confidence: float | None = None
    response_source: str | None = None
    response_provider: str | None = None


@dataclass(frozen=True)
class GradingLogEntry:
    kind: GradingLogKind
    question_id: str
    summary: str
    student_item_id: str = ""
    student_name: str = ""
    student_display_id: str = ""
    revert: GradeRevert | None = None
    timestamp: datetime = field(default_factory=datetime.now)


class GradingLog:
    def __init__(self, *, max_entries: int = MAX_LOG_ENTRIES) -> None:
        self._max_entries = max(1, max_entries)
        self._entries: list[GradingLogEntry] = []
        self._version = 0

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def version(self) -> int:
        return self._version

    def record(self, entry: GradingLogEntry) -> None:
        self._version += 1
        if self._entries and self._coalesces(self._entries[-1], entry):
            self._entries[-1] = replace(entry, revert=self._entries[-1].revert)
            return
        self._entries.append(entry)
        if len(self._entries) > self._max_entries:
            del self._entries[: len(self._entries) - self._max_entries]

    def peek_last_undoable(self) -> GradingLogEntry | None:
        for entry in reversed(self._entries):
            if entry.revert is not None:
                return entry
        return None

    def drop_entry(self, entry: GradingLogEntry) -> bool:
        for index, candidate in enumerate(self._entries):
            if candidate is entry:
                del self._entries[index]
                self._version += 1
                return True
        return False

    def entries_latest_first(self) -> list[GradingLogEntry]:
        return list(reversed(self._entries))

    def entries_for_student(self, student_item_id: str) -> list[GradingLogEntry]:
        return [
            entry
            for entry in self.entries_latest_first()
            if entry.student_item_id and entry.student_item_id == student_item_id
        ]

    def entries_for_question(self, question_id: str) -> list[GradingLogEntry]:
        return [
            entry
            for entry in self.entries_latest_first()
            if entry.question_id == question_id
        ]

    @staticmethod
    def _coalesces(previous: GradingLogEntry, new: GradingLogEntry) -> bool:
        return (
            previous.kind == new.kind
            and previous.question_id == new.question_id
            and previous.student_item_id == new.student_item_id
        )
