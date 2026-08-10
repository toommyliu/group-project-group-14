"""Session history of grading activity shown in the QA picker's log pane."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal

GradingLogKind = Literal["grade", "comment", "rubric", "transcription"]

# Bounded so a marathon grading session cannot grow memory without limit.
MAX_LOG_ENTRIES = 200


@dataclass(frozen=True)
class GradeRevert:
    """The persisted state to restore when a log entry is undone.

    Captured *before* a grade/comment write, this snapshots exactly what the
    submission file held so :meth:`GradeController.undo_last_grade_change` can
    put it back. ``None``/empty values mean the question was ungraded or had no
    comment. Only per-student grade and comment edits carry a revert; rubric
    edits (which mutate the QA file and propagate) are intentionally not
    undoable this way, so their entries leave ``revert`` as ``None``.
    """

    kind: GradingLogKind
    student_item_id: str
    question_id: str
    possible_points: float | None = None
    points_awarded: float | None = None
    rubric_labels: tuple[str, ...] | None = None
    analysis: str | None = None
    # Transcription reverts snapshot the response text (and the confidence and
    # reader that went with it) so undo restores the pre-correction reading
    # exactly — including *who* read it, or the undone file would keep claiming
    # a human typed the machine's words.
    response_text: str | None = None
    response_confidence: float | None = None
    response_source: str | None = None


@dataclass(frozen=True)
class GradingLogEntry:
    kind: GradingLogKind
    question_id: str
    summary: str
    # Student attribution; empty for question-scoped events (rubric edits).
    # Name and display id are both kept so the log pane can render either,
    # depending on the anonymity mode active at display time.
    student_item_id: str = ""
    student_name: str = ""
    student_display_id: str = ""
    # Pre-change state, present only on undoable (grade/comment) entries.
    revert: GradeRevert | None = None
    timestamp: datetime = field(default_factory=datetime.now)


class GradingLog:
    """Append-only, bounded activity log for the current TUI session.

    Consecutive events for the same (kind, question, student) coalesce into
    one entry, so per-keystroke comment saves and rapid rubric reselections
    read as a single change instead of flooding the log.
    """

    def __init__(self, *, max_entries: int = MAX_LOG_ENTRIES) -> None:
        self._max_entries = max(1, max_entries)
        self._entries: list[GradingLogEntry] = []
        self._version = 0

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def version(self) -> int:
        """Monotonic change counter, letting views skip unchanged re-renders."""
        return self._version

    def record(self, entry: GradingLogEntry) -> None:
        self._version += 1
        if self._entries and self._coalesces(self._entries[-1], entry):
            # Keep the *original* pre-streak revert so undoing a coalesced run
            # of edits restores the state before the run began, not the state
            # between two of its keystrokes.
            self._entries[-1] = replace(entry, revert=self._entries[-1].revert)
            return
        self._entries.append(entry)
        if len(self._entries) > self._max_entries:
            del self._entries[: len(self._entries) - self._max_entries]

    def peek_last_undoable(self) -> GradingLogEntry | None:
        """Return the most recent entry that carries a revert, or ``None``."""
        for entry in reversed(self._entries):
            if entry.revert is not None:
                return entry
        return None

    def drop_entry(self, entry: GradingLogEntry) -> bool:
        """Remove ``entry`` (matched by identity) after it has been undone."""
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
