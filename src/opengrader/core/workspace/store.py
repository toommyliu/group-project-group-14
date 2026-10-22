"""Authoritative assignment and grading state."""

from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from typing import Callable, Iterator
from urllib.parse import quote
from uuid import UUID, uuid4, uuid5

from pydantic import TypeAdapter

from opengrader.core.analytics.assignment_statistics import (
    AssignmentVersionStatistics,
    assignment_progress,
    assignment_statistics,
)
from opengrader.core.qa_file import parse_qa_markdown
from opengrader.core.grading.edits import GradeEdit, grade_points
from opengrader.core.outline import BoxChanges, question_structure
from opengrader.core.region_map import RegionMap, region_map_from_json, region_map_to_json
from opengrader.core.history import current_actor
from opengrader.core.submissions.student_file import (
    IdentityCandidate,
    StudentSubmissionDocument,
)
from opengrader.core.workspace.models import (
    Answer,
    Assignment,
    AssignmentInputs,
    AssignmentSummary,
    CourseDetails,
    FeedbackJob,
    GradeSaveResult,
    StaffRole,
    Submission,
    WorkspaceEvent,
    WorkspaceInfo,
)

DATABASE = Path(".opengrader/workspace.sqlite3")
DOCUMENT = TypeAdapter(StudentSubmissionDocument)
_OUTLINES = """CREATE TABLE IF NOT EXISTS outlines (
    assignment_id TEXT NOT NULL REFERENCES assignments(id), version TEXT NOT NULL,
    template TEXT NOT NULL, regions TEXT NOT NULL, PRIMARY KEY (assignment_id, version)
)"""
_STUDENTS = """CREATE TABLE IF NOT EXISTS students (
    id TEXT PRIMARY KEY, sid TEXT NOT NULL UNIQUE, first_name TEXT NOT NULL,
    last_name TEXT NOT NULL, email TEXT UNIQUE, revision INTEGER NOT NULL
)"""
_RECUTS = """CREATE TABLE IF NOT EXISTS recuts (
    assignment_id TEXT NOT NULL, submission_id INTEGER NOT NULL,
    questions TEXT NOT NULL, identity INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'failed', 'done')),
    error TEXT CHECK ((status = 'failed') = (error IS NOT NULL)),
    PRIMARY KEY (assignment_id, submission_id),
    FOREIGN KEY (assignment_id, submission_id) REFERENCES submissions(assignment_id, id)
)"""
_STAFF = """CREATE TABLE staff (
    email TEXT PRIMARY KEY, role TEXT NOT NULL CHECK (role IN ('instructor', 'grader'))
)"""
# Sharing a workspace with other accounts on the same computer (graders who
# reach it over SSH): which accounts are instructors, the group allowed to
# join, and the changes a grader asked for to a grade someone else gave.
_ACCOUNTS = """CREATE TABLE IF NOT EXISTS accounts (
    login TEXT PRIMARY KEY, role TEXT NOT NULL CHECK (role IN ('instructor', 'grader')),
    may_change_others INTEGER NOT NULL DEFAULT 0
)"""
_SHARING = """CREATE TABLE IF NOT EXISTS sharing (
    id INTEGER PRIMARY KEY CHECK (id = 1), group_name TEXT NOT NULL
)"""
_CHANGE_REQUESTS = """CREATE TABLE IF NOT EXISTS change_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id TEXT NOT NULL,
    submission_id INTEGER NOT NULL, question_id TEXT NOT NULL,
    requested_by TEXT NOT NULL, graded_by TEXT NOT NULL, edit TEXT NOT NULL,
    created TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'dismissed')),
    decided_by TEXT, decided TEXT
)"""
_MIGRATIONS: dict[int, tuple[str, ...]] = {
    4: ("CREATE TABLE graders (email TEXT PRIMARY KEY)",),
    # Version 6 meant outlines on one development branch and students on
    # another, so both steps create whichever table a workspace lacks.
    5: (_OUTLINES, _STUDENTS),
    6: (_OUTLINES, _STUDENTS),
    7: (_RECUTS,),
    8: (
        _STAFF,
        "INSERT INTO staff SELECT email, 'grader' FROM graders",
        "DROP TABLE graders",
    ),
    9: (
        "ALTER TABLE workspace ADD COLUMN number TEXT",
        "ALTER TABLE workspace ADD COLUMN name TEXT",
        "ALTER TABLE workspace ADD COLUMN term TEXT",
        "ALTER TABLE workspace ADD COLUMN year INTEGER",
    ),
    10: (_ACCOUNTS, _SHARING, _CHANGE_REQUESTS),
}
_VERSION = max(_MIGRATIONS) + 1
_SCHEMA = f"""
CREATE TABLE workspace (
    id TEXT PRIMARY KEY, actor TEXT NOT NULL,
    number TEXT, name TEXT, term TEXT, year INTEGER
);
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
{_STAFF};
{_OUTLINES};
{_STUDENTS};
{_RECUTS};
{_ACCOUNTS};
{_SHARING};
{_CHANGE_REQUESTS};
PRAGMA user_version = {_VERSION};
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


def queue_feedback(db: sqlite3.Connection, assignment_id: str, submission_id: int) -> None:
    db.execute(
        """INSERT INTO feedback_jobs (assignment_id, submission_id, generation, status) VALUES (?, ?, 1, 'pending')
        ON CONFLICT(assignment_id, submission_id) DO UPDATE SET generation=generation+1, status='pending', error=''""",
        (assignment_id, submission_id),
    )


def refresh_feedback(db: sqlite3.Connection, assignment_id: str, submission_id: int) -> None:
    db.execute(
        "UPDATE feedback_jobs SET generation=generation+1, status='pending', error='' WHERE assignment_id=? AND submission_id=?",
        (assignment_id, submission_id),
    )


@dataclass(frozen=True)
class StoredOutline:
    template: str
    regions: RegionMap


@dataclass(frozen=True)
class VersionSubmissions:
    count: int
    rematchable: int
    graded: dict[str, int]


def picked_by_hand(student_name: str | None, name_confidence: float | None) -> bool:
    return bool(student_name) and name_confidence is None


class WorkspaceConflict(ValueError):
    def __init__(self, message: str, current: Answer | None = None):
        super().__init__(message)
        self.current = current


class WorkspaceNotFound(LookupError):
    pass


def asset_url(relative: str | None) -> str | None:
    return f"/api/assets/{quote(Path(relative).name)}" if relative else None


class WorkspaceNeedsUpgrade(ValueError):
    pass


def discover_workspace(start: Path) -> Path | None:
    directory = start.expanduser().resolve()
    for candidate in (directory, *directory.parents):
        if (candidate / DATABASE).is_file():
            return candidate
    return None


class Workspace:
    def __init__(self, root: Path, *, upgrade: bool = True):
        self.root = root.expanduser().resolve()
        self.database = self.root / DATABASE
        self.on_commit: Callable[[], None] | None = None
        if not self.database.is_file():
            raise ValueError("No workspace database in this directory.")
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == _VERSION:
            return
        if version not in _MIGRATIONS:
            raise ValueError(
                "This workspace needs a compatible version of Opengrader."
            )
        if not upgrade:
            raise WorkspaceNeedsUpgrade(
                "This workspace needs an upgrade before this version of Opengrader can read it."
            )
        with self.connect(write=True) as db:
            # Another process may have upgraded it since the read above.
            version = db.execute("PRAGMA user_version").fetchone()[0]
            while version in _MIGRATIONS:
                for statement in _MIGRATIONS[version]:
                    db.execute(statement)
                version += 1
                db.execute(f"PRAGMA user_version = {version}")

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
            with closing(sqlite3.connect(destination)) as db, db:
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript(_SCHEMA)
                db.execute(
                    "INSERT INTO workspace (id, actor) VALUES (?, ?)",
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
        course = (
            CourseDetails(number=row["number"], name=row["name"], term=row["term"], year=row["year"])
            if row["number"]
            else CourseDetails(number=self.root.name[:120])
        )
        return WorkspaceInfo(id=row["id"], actor=row["actor"], event_cursor=cursor, course=course)

    def set_course(self, course: CourseDetails, *, replace: bool = True) -> None:
        with self.connect(write=True) as db:
            db.execute(
                "UPDATE workspace SET number=?, name=?, term=?, year=?"
                + ("" if replace else " WHERE number IS NULL"),
                (course.number, course.name, course.term, course.year),
            )

    def assignments(self) -> list[Assignment]:
        """List assignments in creation order, oldest first."""
        with self.connect() as db:
            return [
                Assignment(**dict(row))
                for row in db.execute(
                    "SELECT id, title FROM assignments ORDER BY rowid"
                )
            ]

    def assignment_summaries(self) -> list[AssignmentSummary]:
        """Summarize each assignment's grading progress, oldest first."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT id, title, source FROM assignments ORDER BY rowid"
            ).fetchall()
            submissions = {row["id"]: self._submissions(db, row["id"]) for row in rows}
        return [
            AssignmentSummary(
                id=row["id"],
                title=row["title"],
                **asdict(
                    assignment_progress(
                        parse_qa_markdown(row["source"]), submissions[row["id"]]
                    )
                ),
            )
            for row in rows
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
        self,
        source: str,
        *,
        assignment_id: str | None = None,
        template: StoredOutline | None = None,
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
            if template is not None:
                self._write_outline(db, summary.id, "", template)
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
            if self._outlines(db, assignment_id) and question_structure(
                parse_qa_markdown(row[0])
            ) != question_structure(qa):
                raise WorkspaceConflict(
                    "This assignment has an outline, so add, remove, or reorder "
                    "questions on the Outline page."
                )
            db.execute(
                "UPDATE assignments SET source=?, title=? WHERE id=?",
                (source, summary.title, assignment_id),
            )
            record_event(db, assignment_id, kind="assignment")
        return summary

    @staticmethod
    def _outlines(db: sqlite3.Connection, assignment_id: str) -> dict[str, StoredOutline]:
        return {
            row["version"]: StoredOutline(
                template=row["template"],
                regions=region_map_from_json(json.loads(row["regions"])),
            )
            for row in db.execute(
                "SELECT version, template, regions FROM outlines WHERE assignment_id=?",
                (assignment_id,),
            )
        }

    @staticmethod
    def _write_outline(
        db: sqlite3.Connection, assignment_id: str, version: str, outline: StoredOutline
    ) -> None:
        db.execute(
            """INSERT INTO outlines VALUES (?, ?, ?, ?) ON CONFLICT(assignment_id, version)
               DO UPDATE SET template=excluded.template, regions=excluded.regions""",
            (
                assignment_id,
                version,
                outline.template,
                json.dumps(region_map_to_json(outline.regions)),
            ),
        )

    def outlines(self, assignment_id: str) -> dict[str, StoredOutline]:
        with self.connect() as db:
            return self._outlines(db, assignment_id)

    def save_outline(
        self,
        assignment_id: str,
        version: str,
        outline: StoredOutline,
        *,
        expected_source: str,
        source: str | None = None,
    ) -> None:
        """Save a template and its regions, with the answer key they were drawn against."""
        title = parse_qa_markdown(source).title if source is not None else None
        with self.connect(write=True) as db:
            row = db.execute(
                "SELECT source FROM assignments WHERE id=?", (assignment_id,)
            ).fetchone()
            if row is None:
                raise WorkspaceNotFound("Assignment not found.")
            if row[0] != expected_source:
                raise WorkspaceConflict("This assignment changed. Reopen it before saving.")
            if db.execute(
                "SELECT 1 FROM submissions WHERE assignment_id=? LIMIT 1",
                (assignment_id,),
            ).fetchone():
                raise WorkspaceConflict("Assignments with submissions cannot change their outline.")
            if source is not None and source != row[0]:
                db.execute(
                    "UPDATE assignments SET source=?, title=? WHERE id=?",
                    (source, title or "Untitled assignment", assignment_id),
                )
            self._write_outline(db, assignment_id, version, outline)
            record_event(db, assignment_id, kind="assignment")

    def move_boxes(
        self,
        assignment_id: str,
        version: str,
        outline: StoredOutline,
        changes: BoxChanges,
        *,
        expected_source: str,
    ) -> None:
        with self.connect(write=True) as db:
            row = db.execute(
                "SELECT source FROM assignments WHERE id=?", (assignment_id,)
            ).fetchone()
            if row is None:
                raise WorkspaceNotFound("Assignment not found.")
            if row[0] != expected_source:
                raise WorkspaceConflict("This assignment changed. Reopen it before saving.")
            self._write_outline(db, assignment_id, version, outline)
            if changes.questions or changes.identity:
                db.execute(
                    "DELETE FROM recuts WHERE assignment_id=? AND status='done'", (assignment_id,)
                )
                submissions = db.execute(
                    "SELECT id FROM submissions WHERE assignment_id=? AND coalesce(version, '')=?",
                    (assignment_id, version),
                ).fetchall()
                for (submission_id,) in submissions:
                    queued = db.execute(
                        "SELECT questions, identity FROM recuts WHERE assignment_id=? AND submission_id=?",
                        (assignment_id, submission_id),
                    ).fetchone()
                    questions = {*changes.questions, *json.loads(queued[0] if queued else "[]")}
                    db.execute(
                        """INSERT INTO recuts VALUES (?, ?, ?, ?, 'pending', NULL)
                           ON CONFLICT(assignment_id, submission_id) DO UPDATE SET
                           questions=excluded.questions, identity=excluded.identity,
                           status='pending', error=NULL""",
                        (
                            assignment_id,
                            submission_id,
                            json.dumps(sorted(questions)),
                            int(changes.identity or bool(queued and queued[1])),
                        ),
                    )
            record_event(db, assignment_id, kind="assignment")

    def change_versions(
        self,
        assignment_id: str,
        source: str,
        *,
        expected_source: str,
        renamed: dict[str, str],
        removed: str | None = None,
    ) -> None:
        """Save an answer key whose versions changed, keeping each outline with its version."""
        with self.connect(write=True) as db:
            row = db.execute(
                "SELECT source FROM assignments WHERE id=?", (assignment_id,)
            ).fetchone()
            if row is None:
                raise WorkspaceNotFound("Assignment not found.")
            if row[0] != expected_source:
                raise WorkspaceConflict("This assignment changed. Reopen it before saving.")
            if db.execute(
                "SELECT 1 FROM submissions WHERE assignment_id=? LIMIT 1",
                (assignment_id,),
            ).fetchone():
                raise WorkspaceConflict("Assignments with submissions cannot change their versions.")
            db.execute("UPDATE assignments SET source=? WHERE id=?", (source, assignment_id))
            if removed is not None:
                db.execute(
                    "DELETE FROM outlines WHERE assignment_id=? AND version=?",
                    (assignment_id, removed),
                )
            for before, after in renamed.items():
                db.execute(
                    "UPDATE outlines SET version=? WHERE assignment_id=? AND version=?",
                    (after, assignment_id, before),
                )
            record_event(db, assignment_id, kind="assignment")

    def set_student(
        self, assignment_id: str, submission_id: int, student_name: str, student_id: str
    ) -> Submission:
        with self.connect(write=True) as db:
            updated = db.execute(
                """UPDATE submissions SET original=json_set(original, '$.student_name', ?,
                   '$.student_id', ?, '$.name_confidence', NULL, '$.id_candidates', json('[]'))
                   WHERE assignment_id=? AND id=?""",
                (student_name, student_id, assignment_id, submission_id),
            ).rowcount
            if not updated:
                raise WorkspaceNotFound("Submission not found.")
            refresh_feedback(db, assignment_id, submission_id)
            record_event(db, assignment_id, kind="submission", submission_id=submission_id)
            return next(
                submission
                for submission in self._submissions(db, assignment_id)
                if submission.id == submission_id
            )

    def suggest_student(
        self,
        assignment_id: str,
        submission_id: int,
        student_name: str,
        student_id: str,
        match_percent: float | None,
        candidates: list[IdentityCandidate],
    ) -> bool:
        with self.connect(write=True) as db:
            updated = db.execute(
                """UPDATE submissions SET original=json_set(original, '$.student_name', ?,
                   '$.student_id', ?, '$.name_confidence', ?, '$.id_candidates', json(?))
                   WHERE assignment_id=? AND id=?
                   AND coalesce(json_extract(original, '$.student_name'), '') = ''""",
                (
                    student_name,
                    student_id,
                    match_percent,
                    json.dumps([asdict(candidate) for candidate in candidates]),
                    assignment_id,
                    submission_id,
                ),
            ).rowcount
            if updated:
                refresh_feedback(db, assignment_id, submission_id)
                record_event(db, assignment_id, kind="submission", submission_id=submission_id)
            return bool(updated)

    def delete_submissions(self, assignment_id: str, source_keys: list[str]) -> None:
        with self.connect(write=True) as db:
            for key in source_keys:
                row = db.execute(
                    "SELECT id FROM submissions WHERE assignment_id=? AND source_key=?",
                    (assignment_id, key),
                ).fetchone()
                if row is None:
                    continue
                for table in ("recuts", "feedback_jobs", "answers", "change_requests"):
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
            for table in ("recuts", "feedback_jobs", "answers", "change_requests", "submissions", "history", "outlines"):
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

    def submission_for_source(self, assignment_id: str, source_key: str) -> int | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT id FROM submissions WHERE assignment_id=? AND source_key=?",
                (assignment_id, source_key),
            ).fetchone()
        return row[0] if row else None

    def submission_counts(self, assignment_id: str) -> tuple[int, int]:
        with self.connect() as db:
            total, imported = db.execute(
                """SELECT count(*), coalesce(sum(source_key NOT LIKE 'scan-%'), 0)
                   FROM submissions WHERE assignment_id=?""",
                (assignment_id,),
            ).fetchone()
        return total, imported

    def version_submissions(self, assignment_id: str, version: str) -> VersionSubmissions | None:
        with self.connect() as db:
            students = db.execute(
                """SELECT json_extract(original, '$.student_name'),
                   json_extract(original, '$.name_confidence')
                   FROM submissions WHERE assignment_id=? AND coalesce(version, '')=?""",
                (assignment_id, version),
            ).fetchall()
            graded = db.execute(
                """SELECT answers.question_id, count(*) FROM answers JOIN submissions
                   ON submissions.assignment_id=answers.assignment_id AND submissions.id=answers.submission_id
                   WHERE answers.assignment_id=? AND coalesce(submissions.version, '')=?
                   AND json_extract(answers.value, '$.points_awarded') IS NOT NULL
                   GROUP BY answers.question_id""",
                (assignment_id, version),
            ).fetchall()
            roster = db.execute("SELECT EXISTS (SELECT 1 FROM students)").fetchone()[0]
        if not students:
            return None
        by_hand = sum(picked_by_hand(*student) for student in students)
        return VersionSubmissions(
            count=len(students),
            rematchable=len(students) - by_hand if roster else 0,
            graded=dict(graded),
        )

    def submission_sources(self, assignment_id: str, version: str) -> list[str]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT json_extract(original, '$.source_pdf') FROM submissions
                   WHERE assignment_id=? AND COALESCE(version, '')=?""",
                (assignment_id, version),
            ).fetchall()
        return sorted(row[0] for row in rows if row[0])

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
               json_extract(original, '$.student_id') AS student_id,
               json_extract(original, '$.name_image_path') AS name_image,
               json_extract(original, '$.id_image_path') AS id_image,
               json_extract(original, '$.name_confidence') AS match_percent,
               json_extract(original, '$.id_candidates') AS candidates
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
            Submission(
                id=row["id"],
                version=row["version"],
                student_name=row["student_name"] or "",
                student_id=row["student_id"] or "",
                name_image_url=asset_url(row["name_image"]),
                id_image_url=asset_url(row["id_image"]),
                match_percent=row["match_percent"] if row["student_name"] else None,
                candidates=[
                    IdentityCandidate(**candidate)
                    for candidate in json.loads(row["candidates"] or "[]")
                ],
                question_scores=scores.get(row["id"], {}),
            )
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

    def staff(self) -> dict[str, StaffRole]:
        with self.connect() as db:
            return dict(db.execute("SELECT email, role FROM staff ORDER BY email"))

    def add_staff(self, email: str, role: StaffRole) -> bool:
        with self.connect(write=True) as db:
            return db.execute("INSERT OR IGNORE INTO staff VALUES (?, ?)", (email, role)).rowcount == 1

    def change_staff_role(self, email: str, role: StaffRole) -> bool:
        with self.connect(write=True) as db:
            return db.execute("UPDATE staff SET role=? WHERE email=?", (role, email)).rowcount == 1

    def remove_staff(self, email: str) -> bool:
        with self.connect(write=True) as db:
            return db.execute("DELETE FROM staff WHERE email=?", (email,)).rowcount == 1

    def save_grade(
        self,
        assignment_id: str,
        submission_id: int,
        question_id: str,
        edit: GradeEdit,
        *,
        actor: str | None = None,
    ) -> GradeSaveResult:
        """Commit a grade, history, receipt, event, and feedback work together."""
        request_id = str(edit.request_id)
        command = json.dumps(
            [assignment_id, submission_id, question_id, edit.model_dump(mode="json")],
            sort_keys=True,
        )
        with self.connect(write=True) as db:
            actor = actor or db.execute("SELECT actor FROM workspace").fetchone()[0]
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
            queue_feedback(db, assignment_id, submission_id)
            result = GradeSaveResult(answer=after)
            db.execute(
                "INSERT INTO receipts VALUES (?, ?, ?, ?)",
                (request_id, actor, command, result.model_dump_json()),
            )
            return result

    def save_transcription(
        self,
        assignment_id: str,
        submission_id: int,
        question_id: str,
        text: str,
        *,
        expected_revision: str,
        actor: str,
    ) -> Answer:
        from opengrader.core.workspace.intake import context_revision

        with self.connect(write=True) as db:
            before = self._answer(db, assignment_id, submission_id, question_id)
            if before.revision != expected_revision:
                raise WorkspaceConflict("This answer changed while it was being read.", before)
            after = before.model_copy(
                update={
                    "revision": str(int(before.revision) + 1),
                    "response_text": text,
                    "context_revision": context_revision(before.question, text),
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
            return after

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
                        response_text=answers[question.question_id].response_text,
                        points_awarded=answers[question.question_id].points_awarded,
                        rubric_labels=answers[question.question_id].rubric_labels,
                        analysis=answers[question.question_id].analysis,
                    )
                    for question in original.questions
                    if question.question_id in answers
                ],
            )

    def add_asset(self, source: Path) -> str:
        """Copy a file into content-addressed storage and return its relative path."""
        assets = self.root / ".opengrader" / "assets"
        assets.mkdir(exist_ok=True)
        with TemporaryDirectory(dir=assets) as staging:
            temporary = Path(staging) / "asset"
            digest = hashlib.sha256()
            with source.open("rb") as reader, temporary.open("xb") as output:
                while chunk := reader.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            name = digest.hexdigest() + source.suffix.lower()
            destination = assets / name
            if not destination.exists():
                temporary.replace(destination)
        return f"assets/{name}"

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
