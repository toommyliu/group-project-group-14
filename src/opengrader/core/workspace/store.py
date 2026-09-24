"""Authoritative assignment and grading state."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Callable, Iterator
from uuid import UUID, uuid4, uuid5

from pydantic import TypeAdapter

from opengrader.core.analytics.assignment_statistics import (
    AssignmentVersionStatistics,
    assignment_statistics,
)
from opengrader.core.qa_file import parse_qa_markdown
from opengrader.core.grading.edits import GradeEdit, grade_points
from opengrader.core.history import current_actor
from opengrader.core.submissions.student_file import StudentSubmissionDocument
from opengrader.core.workspace.models import (
    Answer,
    Assignment,
    AssignmentInputs,
    FeedbackJob,
    GradeSaveResult,
    Submission,
    WorkspaceEvent,
    WorkspaceInfo,
)

DATABASE = Path(".opengrader/workspace.sqlite3")
DOCUMENT = TypeAdapter(StudentSubmissionDocument)
_SCHEMA = """
CREATE TABLE workspace (id TEXT PRIMARY KEY, actor TEXT NOT NULL);
CREATE TABLE assignments (id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL);
CREATE TABLE assignment_inputs (
    id TEXT PRIMARY KEY, folder TEXT NOT NULL UNIQUE, scan TEXT NOT NULL,
    output TEXT NOT NULL, submissions TEXT, qa TEXT, roster TEXT, blank_template TEXT,
    pages_per_submission INTEGER, input_state TEXT NOT NULL
);
CREATE TABLE submission_sequence (id INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE submissions (
    assignment_id TEXT NOT NULL REFERENCES assignments(id),
    id INTEGER NOT NULL, original TEXT NOT NULL, version TEXT,
    source_key TEXT NOT NULL, UNIQUE (assignment_id, source_key),
    PRIMARY KEY (assignment_id, id)
);
CREATE TABLE answers (
    assignment_id TEXT NOT NULL, submission_id INTEGER NOT NULL,
    question_id TEXT NOT NULL, value TEXT NOT NULL,
    PRIMARY KEY (assignment_id, submission_id, question_id),
    FOREIGN KEY (assignment_id, submission_id) REFERENCES submissions(assignment_id, id)
);
CREATE TABLE receipts (
    request_id TEXT PRIMARY KEY, actor TEXT NOT NULL, command TEXT NOT NULL, result TEXT NOT NULL
);
CREATE TABLE history (
    id INTEGER PRIMARY KEY, assignment_id TEXT NOT NULL, submission_id INTEGER NOT NULL,
    question_id TEXT NOT NULL, actor TEXT NOT NULL, timestamp TEXT NOT NULL,
    before TEXT NOT NULL, after TEXT NOT NULL
);
CREATE TABLE events (
    cursor INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id TEXT NOT NULL, submission_id INTEGER NOT NULL,
    question_id TEXT NOT NULL, revision TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'grade'
);
CREATE TABLE feedback_jobs (
    assignment_id TEXT NOT NULL, submission_id INTEGER NOT NULL,
    generation INTEGER NOT NULL, status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', artifact TEXT,
    PRIMARY KEY (assignment_id, submission_id),
    FOREIGN KEY (assignment_id, submission_id) REFERENCES submissions(assignment_id, id)
);
PRAGMA user_version = 4;
"""


def record_event(
    db: sqlite3.Connection,
    assignment_id: str,
    *,
    kind: str,
    submission_id: int = 0,
    question_id: str = "",
    revision: str = "",
) -> None:
    db.execute(
        "INSERT INTO events (assignment_id, submission_id, question_id, revision, kind) VALUES (?, ?, ?, ?, ?)",
        (assignment_id, submission_id, question_id, revision, kind),
    )


class WorkspaceConflict(ValueError):
    def __init__(self, message: str, current: Answer | None = None):
        super().__init__(message)
        self.current = current


class WorkspaceNotFound(LookupError):
    pass


def discover_workspace(start: Path) -> Path | None:
    directory = start.expanduser().resolve()
    for candidate in (directory, *directory.parents):
        if (candidate / DATABASE).is_file():
            return candidate
    return None


class Workspace:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.database = self.root / DATABASE
        self.on_commit: Callable[[], None] | None = None
        if not self.database.is_file():
            raise ValueError("No workspace database in this directory.")
        with self.connect() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != 4:
                raise ValueError(
                    "This workspace needs a compatible version of Opengrader."
                )

    @classmethod
    def create(cls, root: Path, *, actor: str | None = None) -> "Workspace":
        root = root.expanduser().resolve()
        destination = root / DATABASE
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents initialization from resetting existing grades.
        with destination.open("xb"):
            pass
        destination.chmod(0o600)
        try:
            with sqlite3.connect(destination) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript(_SCHEMA)
                db.execute(
                    "INSERT INTO workspace VALUES (?, ?)",
                    (str(uuid4()), actor or current_actor()),
                )
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return cls(root)

    @contextmanager
    def connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
            if write and self.on_commit is not None:
                self.on_commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def info(self) -> WorkspaceInfo:
        with self.connect() as db:
            row = db.execute("SELECT * FROM workspace").fetchone()
            cursor = db.execute(
                "SELECT coalesce(max(cursor), 0) FROM events"
            ).fetchone()[0]
            return WorkspaceInfo(id=row["id"], actor=row["actor"], event_cursor=cursor)

    def assignments(self) -> list[Assignment]:
        """List assignments in creation order, oldest first."""
        with self.connect() as db:
            return [
                Assignment(**dict(row))
                for row in db.execute(
                    "SELECT id, title FROM assignments ORDER BY rowid"
                )
            ]

    def assignment_inputs(self) -> list[AssignmentInputs]:
        with self.connect() as db:
            return [
                AssignmentInputs(**dict(row))
                for row in db.execute("SELECT * FROM assignment_inputs")
            ]

    def save_assignment_inputs(self, inputs: AssignmentInputs) -> None:
        with self.connect(write=True) as db:
            by_folder = db.execute(
                "SELECT id FROM assignment_inputs WHERE folder=?", (inputs.folder,)
            ).fetchone()
            if by_folder is not None and by_folder[0] != inputs.id:
                raise WorkspaceConflict("This folder belongs to another assignment.")
            by_id = db.execute(
                "SELECT folder FROM assignment_inputs WHERE id=?", (inputs.id,)
            ).fetchone()
            if by_id is not None and by_id[0] != inputs.folder:
                raise WorkspaceConflict("This assignment belongs to another folder.")
            db.execute(
                """INSERT INTO assignment_inputs
                   (id, folder, scan, output, submissions, qa, roster, blank_template, pages_per_submission, input_state)
                   VALUES (:id, :folder, :scan, :output, :submissions, :qa, :roster, :blank_template, :pages_per_submission, :input_state)
                   ON CONFLICT(id) DO UPDATE SET
                     scan=excluded.scan, output=excluded.output, submissions=excluded.submissions, qa=excluded.qa,
                     roster=excluded.roster, blank_template=excluded.blank_template,
                     pages_per_submission=excluded.pages_per_submission,
                     input_state=excluded.input_state""",
                inputs.model_dump(),
            )

    def replace_assignment_inputs(self, old_id: str, inputs: AssignmentInputs) -> None:
        with self.connect(write=True) as db:
            db.execute("DELETE FROM assignment_inputs WHERE id=?", (old_id,))
            db.execute(
                """INSERT INTO assignment_inputs
                   (id, folder, scan, output, submissions, qa, roster, blank_template, pages_per_submission, input_state)
                   VALUES (:id, :folder, :scan, :output, :submissions, :qa, :roster, :blank_template, :pages_per_submission, :input_state)""",
                inputs.model_dump(),
            )

    def assignment_id_for_folder(self, folder: Path) -> str:
        relative = os.path.relpath(folder.resolve(), self.root)
        return str(uuid5(UUID(self.info().id), f"folder:{relative}"))

    def create_assignment(
        self, source: str, *, assignment_id: str | None = None
    ) -> Assignment:
        """A saved ID lets processing retries reuse the same assignment."""
        from opengrader.core.qa_file import parse_qa_markdown

        qa = parse_qa_markdown(source)
        assignment_id = UUID(assignment_id) if assignment_id else uuid4()
        summary = Assignment(
            id=str(assignment_id), title=qa.title or "Untitled assignment"
        )
        with self.connect(write=True) as db:
            existing = db.execute(
                "SELECT source FROM assignments WHERE id=?", (summary.id,)
            ).fetchone()
            if existing is not None:
                if existing[0] != source:
                    raise WorkspaceConflict(
                        "The answer key for this assignment changed. Create another assignment to use a different key."
                    )
                return summary
            db.execute(
                "INSERT INTO assignments VALUES (?, ?, ?)",
                (summary.id, summary.title, source),
            )
            record_event(db, summary.id, kind="assignment")
        return summary

    def update_assignment(
        self, assignment_id: str, source: str, expected: str
    ) -> Assignment:
        from opengrader.core.qa_file import parse_qa_markdown

        qa = parse_qa_markdown(source)
        summary = Assignment(id=assignment_id, title=qa.title or "Untitled assignment")
        with self.connect(write=True) as db:
            row = db.execute(
                "SELECT source FROM assignments WHERE id=?", (assignment_id,)
            ).fetchone()
            if row is None:
                raise WorkspaceNotFound("Assignment not found.")
            if row[0] != expected:
                raise WorkspaceConflict(
                    "This assignment changed. Reopen it before saving."
                )
            if db.execute(
                "SELECT 1 FROM submissions WHERE assignment_id=? LIMIT 1",
                (assignment_id,),
            ).fetchone():
                raise WorkspaceConflict(
                    "Assignments with submissions cannot be edited."
                )
            db.execute(
                "UPDATE assignments SET source=?, title=? WHERE id=?",
                (source, summary.title, assignment_id),
            )
            record_event(db, assignment_id, kind="assignment")
        return summary

    def delete_submissions(self, assignment_id: str, source_keys: list[str]) -> None:
        with self.connect(write=True) as db:
            for key in source_keys:
                row = db.execute(
                    "SELECT id FROM submissions WHERE assignment_id=? AND source_key=?",
                    (assignment_id, key),
                ).fetchone()
                if row is None:
                    continue
                for table in ("feedback_jobs", "answers"):
                    db.execute(
                        f"DELETE FROM {table} WHERE assignment_id=? AND submission_id=?",
                        (assignment_id, row[0]),
                    )
                db.execute(
                    "DELETE FROM submissions WHERE assignment_id=? AND id=?",
                    (assignment_id, row[0]),
                )
                record_event(db, assignment_id, kind="submission", submission_id=row[0])

    def delete_assignment(self, assignment_id: str) -> None:
        with self.connect(write=True) as db:
            for table in ("feedback_jobs", "answers", "submissions", "history"):
                db.execute(
                    f"DELETE FROM {table} WHERE assignment_id=?", (assignment_id,)
                )
            db.execute("DELETE FROM assignments WHERE id=?", (assignment_id,))
            db.execute("DELETE FROM assignment_inputs WHERE id=?", (assignment_id,))
            record_event(db, assignment_id, kind="assignment")

    def assignment_source(self, assignment_id: str) -> str:
        with self.connect() as db:
            row = db.execute(
                "SELECT source FROM assignments WHERE id=?", (assignment_id,)
            ).fetchone()
            if row is None:
                raise WorkspaceNotFound("Assignment not found.")
            return row[0]

    def submissions(self, assignment_id: str) -> list[Submission]:
        with self.connect() as db:
            return self._submissions(db, assignment_id)

    def statistics(self, assignment_id: str) -> list[AssignmentVersionStatistics]:
        with self.connect() as db:
            row = db.execute(
                "SELECT source FROM assignments WHERE id=?", (assignment_id,)
            ).fetchone()
            if row is None:
                raise WorkspaceNotFound("Assignment not found.")
            source = row[0]
            submissions = self._submissions(db, assignment_id)
        return assignment_statistics(parse_qa_markdown(source), submissions)

    @staticmethod
    def _submissions(db: sqlite3.Connection, assignment_id: str) -> list[Submission]:
        rows = db.execute(
            """SELECT id, version, json_extract(original, '$.student_name') AS student_name,
               json_extract(original, '$.student_id') AS student_id
               FROM submissions WHERE assignment_id=? ORDER BY id""",
            (assignment_id,),
        ).fetchall()
        scores: dict[int, dict[str, float | None]] = {}
        for row in db.execute(
            """SELECT submission_id, question_id, json_extract(value, '$.points_awarded') AS points
               FROM answers WHERE assignment_id=?""",
            (assignment_id,),
        ):
            scores.setdefault(row["submission_id"], {})[row["question_id"]] = row[
                "points"
            ]
        return [
            Submission(**dict(row), question_scores=scores.get(row["id"], {}))
            for row in rows
        ]

    @staticmethod
    def _answer(
        db: sqlite3.Connection, assignment_id: str, submission_id: int, question_id: str
    ) -> Answer:
        row = db.execute(
            "SELECT value FROM answers WHERE assignment_id=? AND submission_id=? AND question_id=?",
            (assignment_id, submission_id, question_id),
        ).fetchone()
        if row is None:
            raise WorkspaceNotFound("Answer not found.")
        return Answer.model_validate_json(row[0])

    def answer(
        self, assignment_id: str, submission_id: int, question_id: str
    ) -> Answer:
        with self.connect() as db:
            return self._answer(db, assignment_id, submission_id, question_id)

    def answers(self, assignment_id: str, submission_id: int) -> list[Answer]:
        with self.connect() as db:
            return [
                Answer.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT value FROM answers WHERE assignment_id=? AND submission_id=? ORDER BY rowid",
                    (assignment_id, submission_id),
                )
            ]

    def save_grade(
        self, assignment_id: str, submission_id: int, question_id: str, edit: GradeEdit
    ) -> GradeSaveResult:
        """Commit a grade, history, receipt, event, and feedback work together."""
        request_id = str(edit.request_id)
        command = json.dumps(
            [assignment_id, submission_id, question_id, edit.model_dump(mode="json")],
            sort_keys=True,
        )
        with self.connect(write=True) as db:
            actor = db.execute("SELECT actor FROM workspace").fetchone()[0]
            receipt = db.execute(
                "SELECT * FROM receipts WHERE request_id=?", (request_id,)
            ).fetchone()
            if receipt is not None:
                if receipt["command"] != command or receipt["actor"] != actor:
                    raise WorkspaceConflict(
                        "This save request ID was already used for a different edit."
                    )
                return GradeSaveResult.model_validate_json(receipt["result"])
            before = self._answer(db, assignment_id, submission_id, question_id)
            if (
                before.revision != edit.expected_revision
                or before.context_revision != edit.expected_context_revision
            ):
                raise WorkspaceConflict(
                    "This answer changed. Your draft is preserved; review the saved answer before retrying.",
                    before,
                )
            after = before.model_copy(
                update={
                    "revision": str(int(before.revision) + 1),
                    "points_awarded": grade_points(before.question, edit),
                    "score_mode": edit.score_mode,
                    "rubric_labels": tuple(edit.rubric_labels),
                    "analysis": edit.analysis,
                }
            )
            db.execute(
                "UPDATE answers SET value=? WHERE assignment_id=? AND submission_id=? AND question_id=?",
                (after.model_dump_json(), assignment_id, submission_id, question_id),
            )
            db.execute(
                "INSERT INTO history (assignment_id, submission_id, question_id, actor, timestamp, before, after) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    assignment_id,
                    submission_id,
                    question_id,
                    actor,
                    datetime.now(timezone.utc).isoformat(),
                    before.model_dump_json(),
                    after.model_dump_json(),
                ),
            )
            db.execute(
                "INSERT INTO events (assignment_id, submission_id, question_id, revision) VALUES (?, ?, ?, ?)",
                (assignment_id, submission_id, question_id, after.revision),
            )
            db.execute(
                """INSERT INTO feedback_jobs (assignment_id, submission_id, generation, status) VALUES (?, ?, 1, 'pending')
                ON CONFLICT(assignment_id, submission_id) DO UPDATE SET generation=generation+1, status='pending', error=''""",
                (assignment_id, submission_id),
            )
            result = GradeSaveResult(answer=after)
            db.execute(
                "INSERT INTO receipts VALUES (?, ?, ?, ?)",
                (request_id, actor, command, result.model_dump_json()),
            )
            return result

    def events(self, after: int = 0) -> list[WorkspaceEvent]:
        with self.connect() as db:
            return [
                WorkspaceEvent(**dict(row))
                for row in db.execute(
                    "SELECT * FROM events WHERE cursor>? ORDER BY cursor LIMIT 500",
                    (after,),
                )
            ]

    def history(self, assignment_id: str) -> list[dict[str, object]]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM history WHERE assignment_id=? ORDER BY id",
                    (assignment_id,),
                )
            ]

    def document(
        self, assignment_id: str, submission_id: int
    ) -> StudentSubmissionDocument:
        with self.connect() as db:
            row = db.execute(
                "SELECT original FROM submissions WHERE assignment_id=? AND id=?",
                (assignment_id, submission_id),
            ).fetchone()
            if row is None:
                raise WorkspaceNotFound("Submission not found.")
            original = DOCUMENT.validate_json(row[0])
            answers = {
                answer.question.id: answer
                for answer in (
                    Answer.model_validate_json(row[0])
                    for row in db.execute(
                        "SELECT value FROM answers WHERE assignment_id=? AND submission_id=?",
                        (assignment_id, submission_id),
                    )
                )
            }
            return replace(
                original,
                questions=[
                    replace(
                        question,
                        possible_points=answers[
                            question.question_id
                        ].question.possible_points,
                        points_awarded=answers[question.question_id].points_awarded,
                        rubric_labels=answers[question.question_id].rubric_labels,
                        analysis=answers[question.question_id].analysis,
                    )
                    for question in original.questions
                    if question.question_id in answers
                ],
            )

    def asset(self, relative: str) -> Path:
        path = (self.root / ".opengrader" / relative).resolve()
        if (
            not path.is_relative_to((self.root / ".opengrader" / "assets").resolve())
            or not path.is_file()
        ):
            raise WorkspaceNotFound("Asset not found.")
        return path

    def jobs(self) -> list[FeedbackJob]:
        with self.connect() as db:
            return [
                FeedbackJob(**dict(row))
                for row in db.execute("SELECT * FROM feedback_jobs")
            ]
