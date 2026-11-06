"""A class folder's roster file, shown and edited on the browser's People page.

In a folder session the roster is a file in the class folder — the one the
pipeline matched names against — and the browser's roster is a mirror of it.
The same rule as for a rubric or a student's name holds: **the file is
written first**, and the workspace follows only once that has worked, so a
sync can simply take whatever the file says.

* `sync_roster` reads the file and makes the browser's roster equal to it.
* `add_student`, `update_student` and `remove_student` change one line of
  the file, then the roster.
* A student whose name or ID changes takes their matched submissions along:
  each submission's file is rewritten, then the workspace.

Where the roster is comes from what the pipeline card saved for the folder
(`core/pipeline_settings`), so a roster the user pointed at by hand is the
one used here too; with nothing saved it is the single roster-shaped file in
the folder, if there is exactly one. Several rosters (one per section) are
shown but not edited: a new student would have no one file to go into.

Every function here is called under `folder_session.folder_lock`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from opengrader.core.workspace import roster
from opengrader.core.workspace.context import inputs_for_assignment, resolve_path
from opengrader.core.workspace.folder_session import write_student_file
from opengrader.core.workspace.models import (
    FolderRoster,
    Student,
    StudentChange,
    StudentDetails,
    StudentEdit,
)
from opengrader.core.workspace.roster_file import (
    RosterFile,
    RosterFormat,
    parse_roster,
    roster_format,
    with_student_added,
    with_student_changed,
    with_student_removed,
)
from opengrader.core.workspace.store import (
    Workspace,
    WorkspaceConflict,
    WorkspaceNotFound,
)
from opengrader.core.workspace.writeback import (
    FileChanged,
    WriteBackError,
    _read_stable,
    _replace_atomically,
)

__all__ = [
    "ROSTER_STATE",
    "RosterSource",
    "RosterSync",
    "roster_source",
    "roster_info",
    "sync_roster",
    "add_student",
    "update_student",
    "remove_student",
]

logger = logging.getLogger(__name__)

# What the last sync found, so `/api/workspace` can say whether there is a
# roster (and the People page a reason to exist) without reading the folder.
ROSTER_STATE = Path(".opengrader") / "folder-roster.json"
_ATTEMPTS = 3


@dataclass(frozen=True)
class RosterSource:
    paths: tuple[Path, ...]

    @property
    def editable(self) -> bool:
        return len(self.paths) == 1


@dataclass(frozen=True)
class RosterSync:
    changed: bool = False
    problems: list[str] = field(default_factory=list)
    # The file could not be read at all, so the roster is as it was before.
    unreadable: bool = False


def _class_folder(workspace: Workspace, assignment_id: str) -> Path:
    saved = inputs_for_assignment(workspace, assignment_id)
    return resolve_path(workspace.root, saved.folder) if saved else workspace.root


def roster_source(workspace: Workspace, assignment_id: str) -> RosterSource | None:
    """The roster file(s) this class folder uses, or None when it has none."""
    from opengrader.core.submissions.roster_check import class_roster_paths

    # What the pipeline card saved, else whatever the folder holds. A roster
    # left unticked in the pipeline card is still offered here — that tick
    # decides whether extraction matches names, not whether there is a
    # roster.
    paths = class_roster_paths(_class_folder(workspace, assignment_id))
    if paths:
        return RosterSource(paths)
    # Nothing in the folder looks like a roster right now. If the file used
    # last time is still there, it is still the roster: a file caught
    # mid-save, or saved in an encoding this cannot read, is a roster with a
    # problem to report — not a class that suddenly has no students.
    remembered = tuple(path for path in _remembered(workspace) if path.is_file())
    return RosterSource(remembered) if remembered else None


def _state_path(workspace: Workspace) -> Path:
    return workspace.root / ROSTER_STATE


def _state(workspace: Workspace) -> dict:
    try:
        data = json.loads(_state_path(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def roster_info(workspace: Workspace) -> FolderRoster | None:
    try:
        return FolderRoster.model_validate(_state(workspace)["roster"])
    except (KeyError, ValueError):
        return None


def _remembered(workspace: Workspace) -> tuple[Path, ...]:
    paths = _state(workspace).get("paths")
    if not isinstance(paths, list):
        return ()
    return tuple(Path(path) for path in paths if isinstance(path, str))


def _save_state(
    workspace: Workspace, info: FolderRoster | None, paths: tuple[Path, ...] = ()
) -> bool:
    """Record what was found; whether that differs from what was recorded."""
    target = _state_path(workspace)
    if info is None:
        existed = target.exists()
        target.unlink(missing_ok=True)
        return existed
    body = {
        "roster": info.model_dump(mode="json"),
        "paths": [str(path) for path in paths],
    }
    if body == _state(workspace):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return True


def _read(path: Path) -> tuple[str, tuple[int, int]]:
    for attempt in range(_ATTEMPTS):
        try:
            return _read_stable(path)
        except FileChanged:
            if attempt == _ATTEMPTS - 1:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")


def _load(path: Path) -> tuple[str, tuple[int, int], RosterFormat, RosterFile]:
    text, fingerprint = _read(path)
    kind = roster_format(path)
    return text, fingerprint, kind, parse_roster(text, kind)


def _rename_matched(
    workspace: Workspace,
    assignment_id: str,
    old_sid: str,
    after: StudentDetails,
) -> tuple[int, list[str]]:
    """Carry a student's new name or ID into their matched submissions' files.

    One submission at a time, file first: a file that cannot be written
    leaves that submission as it was in the browser as well, so the two
    never disagree. It then shows as not on the roster, to be re-matched.
    """
    renamed = 0
    problems: list[str] = []
    for assignment, submission_id in roster.submissions_matched_to(workspace, old_sid):
        if assignment != assignment_id:
            continue
        try:
            settle = write_student_file(
                workspace, assignment_id, submission_id, after.name, after.sid
            )
            roster.rename_submission(workspace, assignment_id, submission_id, after)
            settle()
            renamed += 1
        except (OSError, ValueError, LookupError, sqlite3.Error) as exc:
            problems.append(f"Submission {submission_id}: {exc}")
    return renamed, problems


def sync_roster(workspace: Workspace, assignment_id: str) -> RosterSync:
    """Make the browser's roster equal to the class folder's roster file.

    The file wins outright. A roster that cannot be read (mid-save, not
    UTF-8, a `.csv` without its heading row) changes nothing and is
    reported, so a bad save does not empty the People page.
    """
    source = roster_source(workspace, assignment_id)
    if source is None:
        if roster_info(workspace) is None:
            return RosterSync()
        # There was a roster and now there is none.
        roster.mirror_students(workspace, [])
        _save_state(workspace, None)
        return RosterSync(changed=True)

    files: list[RosterFile] = []
    problems: list[str] = []
    for path in source.paths:
        try:
            files.append(_load(path)[3])
        except FileChanged:
            problems.append(f"{path.name} kept changing while it was read; try again.")
        except (OSError, ValueError) as exc:
            problems.append(f"The roster ({path.name}) could not be read: {exc}")
    if problems:
        return RosterSync(problems=problems, unreadable=True)

    students = [student for item in files for student in item.students]
    mirror = roster.mirror_students(workspace, students)
    for before, after in mirror.renamed:
        problems += _rename_matched(workspace, assignment_id, before.sid, after)[1]
    saved = _save_state(
        workspace,
        FolderRoster(
            files=[path.name for path in source.paths],
            format=files[0].format,
            editable=source.editable,
            has_email=all(item.has_email for item in files),
            students=len({student.sid for student in students}),
            skipped=sum(item.skipped for item in files),
        ),
        source.paths,
    )
    return RosterSync(changed=mirror.changed or saved, problems=problems)


# ---------------------------------------------------------------------------
# Edits from the People page
# ---------------------------------------------------------------------------


def _editable(workspace: Workspace, assignment_id: str) -> Path:
    """Bring the roster up to the file, and return the one file to edit."""
    source = roster_source(workspace, assignment_id)
    if source is None:
        raise WorkspaceConflict(
            "This class folder has no roster file to save students into."
        )
    if not source.editable:
        raise WorkspaceConflict(
            "This class folder has one roster per section, so its students "
            "are edited in those files."
        )
    pulled = sync_roster(workspace, assignment_id)
    if pulled.unreadable:
        raise WriteBackError(pulled.problems[0])
    return source.paths[0]


def _write(path: Path, text: str, updated: str, fingerprint: tuple[int, int]) -> None:
    if updated == text:
        return
    try:
        _replace_atomically(path, updated, fingerprint)
    except FileChanged as exc:
        raise WorkspaceConflict(
            f"{path.name} changed while it was being saved, so nothing was "
            "written. Try again."
        ) from exc
    except OSError as exc:
        raise WriteBackError(
            f"{path.name} could not be written, so the roster was not "
            f"changed: {exc}"
        ) from exc


def _open(path: Path) -> tuple[str, tuple[int, int], RosterFormat]:
    try:
        text, fingerprint = _read(path)
    except FileChanged as exc:
        raise WorkspaceConflict(
            f"{path.name} is being saved by another program. Try again in a moment."
        ) from exc
    except OSError as exc:
        raise WriteBackError(f"{path.name} could not be read: {exc}") from exc
    return text, fingerprint, roster_format(path)


def _not_in_file(path: Path, sid: str) -> WorkspaceConflict:
    return WorkspaceConflict(
        f"{path.name} no longer has a student with ID {sid}. Reload the page "
        "to see the roster as it is now."
    )


def add_student(
    workspace: Workspace, assignment_id: str, details: StudentDetails
) -> Student:
    path = _editable(workspace, assignment_id)
    roster.check_add(workspace, details)
    text, fingerprint, kind = _open(path)
    _write(path, text, with_student_added(text, kind, details), fingerprint)
    roster.add_student(workspace, details)
    # Read back, so the roster is what the file now says (a one-column name
    # may divide into first and last differently than it was typed).
    sync_roster(workspace, assignment_id)
    return roster.student_by_sid(workspace, details.sid)


def update_student(
    workspace: Workspace, assignment_id: str, student_id: str, edit: StudentEdit
) -> StudentChange:
    path = _editable(workspace, assignment_id)
    current, details = roster.check_update(
        workspace, student_id, edit, edit.expected_revision
    )
    text, fingerprint, kind = _open(path)
    try:
        updated = with_student_changed(text, kind, current.sid, details)
    except LookupError as exc:
        raise _not_in_file(path, current.sid) from exc
    _write(path, text, updated, fingerprint)

    renamed, problems = 0, []
    if (details.sid, details.name) != (current.sid, current.name):
        renamed, problems = _rename_matched(
            workspace, assignment_id, current.sid, details
        )
    roster.update_student(
        workspace,
        student_id,
        details,
        edit.expected_revision,
        rename_submissions=False,
    )
    sync_roster(workspace, assignment_id)
    try:
        saved = roster.student_by_sid(workspace, details.sid)
    except WorkspaceNotFound:
        saved = roster.get_student(workspace, student_id)
    return StudentChange(**saved.model_dump(), renamed=renamed, problems=problems)


def remove_student(workspace: Workspace, assignment_id: str, student_id: str) -> None:
    path = _editable(workspace, assignment_id)
    student = roster.get_student(workspace, student_id)
    text, fingerprint, kind = _open(path)
    try:
        updated = with_student_removed(text, kind, student.sid)
    except LookupError as exc:
        raise _not_in_file(path, student.sid) from exc
    _write(path, text, updated, fingerprint)
    roster.remove_student(workspace, student_id)
    sync_roster(workspace, assignment_id)
