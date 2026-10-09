import csv
from dataclasses import dataclass
import hashlib
import io
import json
import sqlite3
from uuid import uuid4

from pydantic import ValidationError

from opengrader.core.workspace.models import (
    RosterImport,
    RosterProblem,
    RosterUpdate,
    Student,
    StudentChange,
    StudentDetails,
)
from opengrader.core.workspace.store import (
    Workspace,
    WorkspaceConflict,
    WorkspaceNotFound,
    record_event,
    refresh_feedback,
)

MAX_ROSTER_BYTES = 2_000_000
_COLUMNS = {
    "first name": "first_name",
    "last name": "last_name",
    "sid": "sid",
    "email": "email",
    "role": "role",
}
_REQUIRED = {"first_name": "First Name", "last_name": "Last Name", "sid": "SID"}
_ORDER = (
    "ORDER BY CASE last_name WHEN '' THEN first_name ELSE last_name END COLLATE NOCASE, "
    "first_name COLLATE NOCASE, sid"
)


class RosterConflict(WorkspaceConflict):
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class RosterRow:
    row: int
    student: StudentDetails


def read_roster_csv(data: bytes) -> tuple[list[RosterRow], list[RosterProblem]]:
    if len(data) > MAX_ROSTER_BYTES:
        return [], [
            RosterProblem(
                message="This file is over 2 MB, which is too large for a roster. Check that you chose the roster CSV."
            )
        ]
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], [
            RosterProblem(
                message="This file isn't UTF-8 text. In Excel, save it as CSV UTF-8 and try again."
            )
        ]
    records: list[list[str]] = []
    try:
        for record in csv.reader(io.StringIO(text, newline=""), strict=True):
            records.append(record)
    except csv.Error:
        return [], [
            RosterProblem(
                row=len(records) + 1,
                message="This row has a quotation mark that is out of place or never closed. "
                'Quote whole values only, and write a quotation mark inside a value as "".',
            )
        ]
    if not records:
        return [], [RosterProblem(message="This file is empty.")]

    problems: list[RosterProblem] = []
    columns: dict[str, int] = {}
    for index, heading in enumerate(records[0]):
        field = _COLUMNS.get(" ".join(heading.split()).casefold())
        if field is None:
            continue
        if field in columns:
            problems.append(
                RosterProblem(message=f"There is more than one {heading.strip()} column.")
            )
        columns.setdefault(field, index)
    missing = [label for field, label in _REQUIRED.items() if field not in columns]
    if missing:
        problems.append(
            RosterProblem(
                message="The first row needs First Name, Last Name, and SID column headings. "
                f"Missing: {', '.join(missing)}.",
            )
        )
    if problems:
        return [], problems

    rows: list[RosterRow] = []
    width = len(records[0])
    for number, record in enumerate(records[1:], start=2):
        if not any(cell.strip() for cell in record):
            continue
        if any(cell.strip() for cell in record[width:]):
            problems.append(
                RosterProblem(
                    row=number,
                    message="This row has more values than there are column headings. "
                    "Put quotes around values that contain commas.",
                )
            )
            continue
        cells = {
            field: record[index] if index < len(record) else ""
            for field, index in columns.items()
        }
        role = cells.pop("role", "").strip()
        if role and role.casefold() != "student":
            problems.append(
                RosterProblem(
                    row=number,
                    message=f'The role is "{role}". Only students can be imported, '
                    "so remove this row or correct its role.",
                )
            )
            continue
        try:
            rows.append(RosterRow(number, StudentDetails.model_validate(cells)))
        except ValidationError as exc:
            problems.extend(
                RosterProblem(row=number, message=error["msg"]) for error in exc.errors()
            )
    if not rows and not problems:
        problems.append(RosterProblem(message="This file has column headings but no students."))
    return rows, problems


def _details(student: Student) -> StudentDetails:
    return StudentDetails.model_validate(
        student.model_dump(include=set(StudentDetails.model_fields))
    )


@dataclass(frozen=True)
class _Plan:
    additions: list[StudentDetails]
    updates: list[tuple[Student, StudentDetails]]
    unchanged: int
    problems: list[RosterProblem]

    def result(self) -> RosterImport:
        result = RosterImport(
            added=self.additions,
            updated=[
                RosterUpdate(before=_details(student), after=after)
                for student, after in self.updates
            ],
            unchanged=self.unchanged,
            problems=self.problems,
            fingerprint="",
        )
        basis = [
            result.model_dump(mode="json"),
            [(student.id, student.revision) for student, _ in self.updates],
        ]
        digest = hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()
        return result.model_copy(update={"fingerprint": digest})


def _plan(
    current: list[Student], rows: list[RosterRow], problems: list[RosterProblem]
) -> _Plan:
    by_sid = {student.sid: student for student in current}
    final: dict[str, StudentDetails] = {student.sid: student for student in current}
    claims: dict[str, int] = {}
    additions: list[StudentDetails] = []
    updates: list[tuple[Student, StudentDetails]] = []
    unchanged = 0
    problems = list(problems)
    for row in rows:
        sid = row.student.sid
        if sid in claims:
            problems.append(
                RosterProblem(row=row.row, message=f"SID {sid} is also on row {claims[sid]}.")
            )
            continue
        claims[sid] = row.row
        existing = by_sid.get(sid)
        after = row.student
        if existing is None:
            additions.append(after)
        else:
            if after.email is None:
                after = after.model_copy(update={"email": existing.email})
            if _details(existing) == after:
                unchanged += 1
            else:
                updates.append((existing, after))
        final[sid] = after

    owners: dict[str, list[str]] = {}
    for sid, details in final.items():
        if details.email is not None:
            owners.setdefault(details.email, []).append(sid)
    for email, sids in owners.items():
        first, *others = sorted(sids, key=lambda sid: claims.get(sid, 0))
        for sid in others:
            message = (
                f"Email {email} is also on row {claims[first]}."
                if first in claims
                else f"{email} already belongs to {by_sid[first]}."
            )
            problems.append(RosterProblem(row=claims[sid], message=message))
    problems.sort(key=lambda problem: problem.row or 0)
    return _Plan(additions, updates, unchanged, problems)


def matched_submissions(db: sqlite3.Connection, sid: str) -> list[tuple[str, int]]:
    """(assignment, submission) for every submission matched to this student ID."""
    if not sid:
        return []
    return [
        (row[0], row[1])
        for row in db.execute(
            "SELECT assignment_id, id FROM submissions "
            "WHERE json_extract(original, '$.student_id')=? ORDER BY assignment_id, id",
            (sid,),
        )
    ]


def rename_matched_submissions(
    db: sqlite3.Connection, old_sid: str, after: StudentDetails
) -> int:
    """Carry a roster student's new name or ID into the submissions matched to them.

    A submission keeps its own copy of the name and ID it was given (that is
    what is printed on its feedback). Matching is by ID, so those copies are
    exactly the ones holding the student's old ID. Whether the match was
    confirmed by a grader is left as it was: this corrects who the student
    *is*, not which paper is theirs.
    """
    matched = matched_submissions(db, old_sid)
    if not matched:
        return 0
    changed = 0
    for assignment_id, submission_id in matched:
        changed += db.execute(
            """UPDATE submissions SET original=json_set(original,
               '$.student_name', ?, '$.student_id', ?)
               WHERE assignment_id=? AND id=?
               AND (coalesce(json_extract(original, '$.student_name'), '') != ?
                    OR json_extract(original, '$.student_id') != ?)""",
            (after.name, after.sid, assignment_id, submission_id, after.name, after.sid),
        ).rowcount
        refresh_feedback(db, assignment_id, submission_id)
        record_event(db, assignment_id, kind="submission", submission_id=submission_id)
    return changed


def submissions_matched_to(workspace: Workspace, sid: str) -> list[tuple[str, int]]:
    with workspace.connect() as db:
        return matched_submissions(db, sid)


def rename_submission(
    workspace: Workspace, assignment_id: str, submission_id: int, after: StudentDetails
) -> None:
    """One submission's share of `rename_matched_submissions`, on its own.

    For a folder session, which renames a submission in the workspace only
    once its file has been written.
    """
    with workspace.connect(write=True) as db:
        db.execute(
            """UPDATE submissions SET original=json_set(original,
               '$.student_name', ?, '$.student_id', ?) WHERE assignment_id=? AND id=?""",
            (after.name, after.sid, assignment_id, submission_id),
        )
        refresh_feedback(db, assignment_id, submission_id)
        record_event(db, assignment_id, kind="submission", submission_id=submission_id)


def _apply(db: sqlite3.Connection, plan: _Plan) -> None:
    # Clear changing emails first so two students can trade addresses in one import.
    db.executemany(
        "UPDATE students SET email=NULL WHERE id=?",
        [(student.id,) for student, after in plan.updates if after.email != student.email],
    )
    db.executemany(
        "UPDATE students SET first_name=?, last_name=?, email=?, revision=revision+1 WHERE id=?",
        [
            (after.first_name, after.last_name, after.email, student.id)
            for student, after in plan.updates
        ],
    )
    db.executemany(
        "INSERT INTO students (id, sid, first_name, last_name, email, revision) VALUES (?, ?, ?, ?, ?, 1)",
        [
            (str(uuid4()), details.sid, details.first_name, details.last_name, details.email)
            for details in plan.additions
        ],
    )
    for student, after in plan.updates:
        if after.name != student.name:
            rename_matched_submissions(db, student.sid, after)
    record_event(db, "", kind="roster")


def import_roster(
    workspace: Workspace,
    data: bytes,
    *,
    dry_run: bool = False,
    expected_fingerprint: str | None = None,
) -> RosterImport:
    rows, problems = read_roster_csv(data)
    with workspace.connect(write=not dry_run) as db:
        plan = _plan(_students(db), rows, problems)
        result = plan.result()
        if expected_fingerprint is not None and expected_fingerprint != result.fingerprint:
            raise WorkspaceConflict("The roster changed after you reviewed this file.")
        if not dry_run and not plan.problems and (plan.additions or plan.updates):
            _apply(db, plan)
    return result


def _students(db: sqlite3.Connection) -> list[Student]:
    return [
        Student(**dict(row)) for row in db.execute(f"SELECT * FROM students {_ORDER}")
    ]


def students(workspace: Workspace) -> list[Student]:
    with workspace.connect() as db:
        return _students(db)


def student_by_sid(workspace: Workspace, sid: str) -> Student:
    with workspace.connect() as db:
        row = db.execute("SELECT * FROM students WHERE sid=?", (sid.strip(),)).fetchone()
    if row is None:
        raise WorkspaceNotFound(f"No student on the roster has SID {sid.strip()}.")
    return Student(**dict(row))


def _require_unique(
    db: sqlite3.Connection, details: StudentDetails, student_id: str | None = None
) -> None:
    for field, value in (("sid", details.sid), ("email", details.email)):
        if value is None:
            continue
        row = db.execute(
            f"SELECT * FROM students WHERE {field}=? AND id IS NOT ?", (value, student_id)
        ).fetchone()
        if row is not None:
            other = Student(**dict(row))
            raise RosterConflict(
                field,
                f"SID {value} already belongs to {other.name}."
                if field == "sid"
                else f"{value} already belongs to {other}.",
            )


def add_student(workspace: Workspace, details: StudentDetails) -> Student:
    student = Student(
        **details.model_dump(include=set(StudentDetails.model_fields)),
        id=str(uuid4()),
        revision=1,
    )
    with workspace.connect(write=True) as db:
        _require_unique(db, student)
        db.execute(
            "INSERT INTO students (id, sid, first_name, last_name, email, revision) "
            "VALUES (:id, :sid, :first_name, :last_name, :email, :revision)",
            student.model_dump(),
        )
        record_event(db, "", kind="roster")
    return student


def _checked_update(
    db: sqlite3.Connection, student_id: str, details: StudentDetails, expected_revision: int
) -> tuple[Student, StudentDetails]:
    row = db.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    if row is None:
        raise WorkspaceNotFound("This student is no longer on the roster.")
    current = Student(**dict(row))
    if current.revision != expected_revision:
        raise WorkspaceConflict(
            "Someone else changed this student while you were editing."
        )
    details = StudentDetails.model_validate(
        details.model_dump(include=set(StudentDetails.model_fields))
    )
    if _details(current) != details:
        _require_unique(db, details, student_id)
    return current, details


def check_add(workspace: Workspace, details: StudentDetails) -> None:
    """Raise what `add_student` would, changing nothing."""
    with workspace.connect() as db:
        _require_unique(db, details)


def check_update(
    workspace: Workspace, student_id: str, details: StudentDetails, expected_revision: int
) -> tuple[Student, StudentDetails]:
    """Raise what `update_student` would, changing nothing.

    Returns the student as it is now and the edit as it would be stored, for
    a caller that has to write a file before the roster may change.
    """
    with workspace.connect() as db:
        return _checked_update(db, student_id, details, expected_revision)


def get_student(workspace: Workspace, student_id: str) -> Student:
    with workspace.connect() as db:
        row = db.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
    if row is None:
        raise WorkspaceNotFound("This student is no longer on the roster.")
    return Student(**dict(row))


def update_student(
    workspace: Workspace,
    student_id: str,
    details: StudentDetails,
    expected_revision: int,
    *,
    rename_submissions: bool = True,
) -> StudentChange:
    """Change a roster student; matched submissions follow a new name or ID.

    `rename_submissions=False` is for a folder session, which has already
    carried the change into each submission's file and the workspace with it.
    """
    with workspace.connect(write=True) as db:
        current, details = _checked_update(db, student_id, details, expected_revision)
        if _details(current) == details:
            return StudentChange(**current.model_dump())
        student = Student(**details.model_dump(), id=student_id, revision=current.revision + 1)
        db.execute(
            "UPDATE students SET sid=:sid, first_name=:first_name, last_name=:last_name, "
            "email=:email, revision=:revision WHERE id=:id",
            student.model_dump(),
        )
        renamed = 0
        if rename_submissions and (
            details.sid != current.sid or details.name != current.name
        ):
            renamed = rename_matched_submissions(db, current.sid, details)
        record_event(db, "", kind="roster")
    return StudentChange(**student.model_dump(), renamed=renamed)


@dataclass(frozen=True)
class RosterMirror:
    """What making the roster equal to a file changed.

    `renamed` pairs a student as they were with what the file now says, for
    those whose name changed under the same ID.
    """

    changed: bool = False
    renamed: tuple[tuple[Student, StudentDetails], ...] = ()


def mirror_students(workspace: Workspace, desired: list[StudentDetails]) -> RosterMirror:
    """Make the roster exactly `desired`, the students in a class folder's file.

    Students are matched by ID: one the file still has keeps its identity
    (and is updated if its name or email changed), one it gained is added,
    one it lost is removed. Submissions are not touched here — a folder
    session renames those through their files (`folder_roster`).
    """
    wanted: dict[str, StudentDetails] = {}
    emails: set[str] = set()
    for details in desired:
        if details.sid in wanted:
            continue
        if details.email is not None:
            # Two rows with one address: the roster allows an address once.
            if details.email in emails:
                details = details.model_copy(update={"email": None})
            else:
                emails.add(details.email)
        wanted[details.sid] = details
    with workspace.connect(write=True) as db:
        current = {student.sid: student for student in _students(db)}
        removed = [student for sid, student in current.items() if sid not in wanted]
        added = [details for sid, details in wanted.items() if sid not in current]
        updated = [
            (current[sid], details)
            for sid, details in wanted.items()
            if sid in current and _details(current[sid]) != details
        ]
        if not (removed or added or updated):
            return RosterMirror()
        db.executemany("DELETE FROM students WHERE id=?", [(s.id,) for s in removed])
        db.executemany(
            "UPDATE students SET email=NULL WHERE id=?", [(s.id,) for s, _ in updated]
        )
        db.executemany(
            "UPDATE students SET first_name=?, last_name=?, email=?, revision=revision+1 WHERE id=?",
            [(d.first_name, d.last_name, d.email, s.id) for s, d in updated],
        )
        db.executemany(
            "INSERT INTO students (id, sid, first_name, last_name, email, revision) VALUES (?, ?, ?, ?, ?, 1)",
            [(str(uuid4()), d.sid, d.first_name, d.last_name, d.email) for d in added],
        )
        record_event(db, "", kind="roster")
    return RosterMirror(
        changed=True,
        renamed=tuple((s, d) for s, d in updated if s.name != d.name),
    )


def remove_student(workspace: Workspace, student_id: str) -> None:
    with workspace.connect(write=True) as db:
        if db.execute("DELETE FROM students WHERE id=?", (student_id,)).rowcount == 0:
            raise WorkspaceNotFound("This student is no longer on the roster.")
        record_event(db, "", kind="roster")
