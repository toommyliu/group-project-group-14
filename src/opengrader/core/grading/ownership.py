"""Whose grade is this: the auto-grader's, or a person's?

Auto-grading runs again whenever the answer key, the readings or the AI
settings change, and an objective answer (multiple choice, an exact value)
is scored again every time — that is how a fix to the key reaches every
student. What it must never do is score over a grade a person gave: partial
credit on a multiple-choice answer, a comment, a fix of its own mistake.

So the auto-grader remembers what it wrote, per submission file and
question, in `.opengrader/auto-grades.json` beside the grade history. A
grade it may change is one that is still exactly what it last wrote, or
that nobody gave yet. Anything else is a person's, and is kept.

A folder graded before this record existed has no record yet. There the
grade history decides: a grade whose latest history entry is the
auto-grader's, and still says what the file says, is the auto-grader's. A
grade with no history at all (journalling off, or written by hand) is
treated as a person's: keeping one by mistake costs a re-run with
`--overwrite-grades`; scoring over one by mistake loses a person's work.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from opengrader.core.history import read_journal, resolve_history_dir
from opengrader.core.history.restore import student_key
from opengrader.core.submissions import StudentSubmissionDocument, StudentSubmissionQuestion

RECORD_FILENAME = "auto-grades.json"
_AUTO_ACTOR_PREFIX = "automated-grading"


class Owner(str, Enum):
    NOBODY = "nobody"
    AUTO = "auto"
    PERSON = "person"


@dataclass(frozen=True)
class _Written:
    points: float | None
    rubric: tuple[str, ...]
    analysis: str


def same_points(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    return abs(left - right) < 1e-9


class AutoGrades:
    """What the auto-grader last wrote under one submissions folder."""

    def __init__(self, submissions_path: str | Path) -> None:
        self._history_dir = resolve_history_dir(submissions_path)
        self._root = self._history_dir.parent.parent
        self._path = self._history_dir.parent / RECORD_FILENAME
        self._files: dict[str, dict[str, _Written]] = {}
        self._history: dict[tuple[str, str, str], Any] | None = None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        for name, questions in (raw.get("files") or {}).items():
            if not isinstance(questions, dict):
                continue
            self._files[name] = {
                question_id: _Written(
                    points=None if value.get("points") is None else float(value["points"]),
                    rubric=tuple(str(label) for label in value.get("rubric") or ()),
                    analysis=str(value.get("analysis") or ""),
                )
                for question_id, value in questions.items()
                if isinstance(value, dict)
            }

    def _key(self, student_path: Path) -> str:
        try:
            return student_path.resolve().relative_to(self._root.resolve()).as_posix()
        except ValueError:
            return student_path.resolve().as_posix()

    def _latest_history(self, document: StudentSubmissionDocument, question_id: str, kind: str):
        if self._history is None:
            self._history = {}
            for entry in read_journal(self._history_dir):
                # Entries are in time order, so the last one kept is the latest.
                key = student_key(entry.student_id, entry.student_name)
                if key:
                    self._history[(key, entry.question_id, entry.kind)] = entry
        key = student_key(document.student_id, document.student_name)
        return self._history.get((key, question_id, kind)) if key else None

    def grade_owner(
        self, student_path: Path, document: StudentSubmissionDocument, question: StudentSubmissionQuestion
    ) -> Owner:
        if question.points_awarded is None and not question.rubric_labels:
            return Owner.NOBODY
        written = self._files.get(self._key(student_path), {}).get(question.question_id)
        if written is not None:
            same = same_points(written.points, question.points_awarded) and written.rubric == tuple(
                question.rubric_labels
            )
            return Owner.AUTO if same else Owner.PERSON
        entry = self._latest_history(document, question.question_id, "grade")
        if entry is not None and entry.actor.startswith(_AUTO_ACTOR_PREFIX):
            points = entry.new.get("points")
            rubric = tuple(str(label) for label in entry.new.get("rubric") or ())
            if same_points(None if points is None else float(points), question.points_awarded) and rubric == tuple(
                question.rubric_labels
            ):
                return Owner.AUTO
        return Owner.PERSON

    def comment_owner(
        self, student_path: Path, document: StudentSubmissionDocument, question: StudentSubmissionQuestion
    ) -> Owner:
        current = question.analysis or ""
        if not current.strip():
            return Owner.NOBODY
        written = self._files.get(self._key(student_path), {}).get(question.question_id)
        if written is not None:
            return Owner.AUTO if written.analysis == current else Owner.PERSON
        entry = self._latest_history(document, question.question_id, "comment")
        if (
            entry is not None
            and entry.actor.startswith(_AUTO_ACTOR_PREFIX)
            and str(entry.new.get("analysis") or "") == current
        ):
            return Owner.AUTO
        return Owner.PERSON

    def remember(
        self,
        student_path: Path,
        question_id: str,
        *,
        points: float | None,
        rubric: tuple[str, ...],
        analysis: str,
    ) -> None:
        self._files.setdefault(self._key(student_path), {})[question_id] = _Written(points, rubric, analysis)

    def move(self, moves: Mapping[Path, Path | None]) -> None:
        """Grades moved between submission files (the scan was split again):
        what the auto-grader wrote on `old` now sits on `new`, or nowhere
        when `new` is None. All moves happen at once, so two files may swap."""
        keyed = {self._key(old): (self._key(new) if new is not None else None) for old, new in moves.items()}
        moved = {new: self._files[old] for old, new in keyed.items() if new is not None and old in self._files}
        for old in keyed:
            self._files.pop(old, None)
        self._files.update(moved)

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "files": {
                name: {
                    question_id: {
                        "points": written.points,
                        "rubric": list(written.rubric),
                        "analysis": written.analysis,
                    }
                    for question_id, written in sorted(questions.items())
                }
                for name, questions in sorted(self._files.items())
                if questions
            },
        }
        temporary = self._path.with_name(f"{self._path.name}.tmp")
        temporary.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
        os.replace(temporary, self._path)
