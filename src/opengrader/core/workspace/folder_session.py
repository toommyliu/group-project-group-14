"""A class folder opened in the browser from classic mode.

Classic grades `.submission.md` files directly, so the files are the truth.
When classic opens such a folder in the browser, the local service still
needs its SQLite workspace, but the folder stays the truth: every grade
saved while the marker below exists is written straight into that
student's file (write-through), and `sync_folder` pulls in what classic
changed in the files meanwhile. "Create a workspace" removes the marker;
from then on grades live in SQLite and reach the files only by write-back.

Both directions reuse the write-back merge (`writeback.plan_submission`):
the file is "ours", the workspace "theirs", and the base is the write-back
state. A cell changed on both sides goes to the workspace, because in a
folder session a workspace change is always a browser save, the newest
explicit action; the file value it replaces is kept in grade history.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock
from uuid import uuid4

from opengrader.core.grading.edits import GradeEdit, grade_points
from opengrader.core.history.journal import GradeJournal, resolve_history_dir
from opengrader.core.history.restore import Aspect, AspectValue
from opengrader.core.workspace.context import submissions_path
from opengrader.core.workspace.intake import publish_new_files
from opengrader.core.workspace.models import (
    Answer,
    FolderSessionInfo,
    FolderSyncResult,
    FolderWrite,
)
from opengrader.core.workspace.store import (
    DATABASE,
    Workspace,
    WorkspaceConflict,
    WorkspaceNotFound,
    discover_workspace,
)
from opengrader.core.workspace.writeback import (
    FileChanged,
    SubmissionPlan,
    WriteBackError,
    _cells,
    _submission_rows,
    apply_submission_plan,
    file_fingerprint,
    load_write_back_state,
    locate_submissions_dir,
    plan_submission,
    update_write_back_state,
    workspace_cells,
)

__all__ = [
    "BROWSER_ACTOR",
    "FOLDER_SESSION",
    "FolderSession",
    "active_folder_session",
    "end_folder_session",
    "folder_lock",
    "folder_session_info",
    "load_folder_session",
    "shared_workspace_for",
    "start_folder_session",
    "sync_folder",
    "write_through",
]

logger = logging.getLogger(__name__)

FOLDER_SESSION = Path(".opengrader") / "folder-session.json"
# Grade history actor for file values replaced from the browser, so
# `grade-restore apply --without-actor browser` undoes them.
BROWSER_ACTOR = "browser"
SAVE_SUMMARY = "saved from the browser"
SYNC_SUMMARY = "saved from the browser (written on sync)"
_ATTEMPTS = 3

_locks: dict[Path, RLock] = {}
_locks_guard = Lock()
_journals: dict[Path, GradeJournal] = {}


@dataclass(frozen=True)
class FolderSession:
    assignment_id: str
    created_at: str


def load_folder_session(root: str | Path) -> FolderSession | None:
    """The marker, or None; a damaged marker is treated as no session."""
    try:
        raw = json.loads((Path(root) / FOLDER_SESSION).read_text(encoding="utf-8"))
        assignment_id = raw["assignment_id"]
        created_at = raw.get("created_at", "")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if raw.get("version") != 1 or not isinstance(assignment_id, str) or not assignment_id:
        return None
    return FolderSession(assignment_id=assignment_id, created_at=str(created_at))


def start_folder_session(root: str | Path, assignment_id: str) -> FolderSession:
    existing = load_folder_session(root)
    if existing is not None and existing.assignment_id == assignment_id:
        return existing
    session = FolderSession(
        assignment_id=assignment_id,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    target = Path(root) / FOLDER_SESSION
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                {
                    "version": 1,
                    "assignment_id": session.assignment_id,
                    "created_at": session.created_at,
                },
                indent=1,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return session


def end_folder_session(root: str | Path) -> None:
    (Path(root) / FOLDER_SESSION).unlink(missing_ok=True)


def active_folder_session(workspace: Workspace) -> FolderSession | None:
    """The marker, when its assignment is in the workspace (an import that
    failed part way leaves a marker for the retry, but nothing to grade)."""
    session = load_folder_session(workspace.root)
    if session is None:
        return None
    if all(item.id != session.assignment_id for item in workspace.assignments()):
        return None
    return session


def folder_session_info(workspace: Workspace) -> FolderSessionInfo | None:
    session = active_folder_session(workspace)
    if session is None:
        return None
    title = next(
        item.title
        for item in workspace.assignments()
        if item.id == session.assignment_id
    )
    folder = locate_submissions_dir(workspace, session.assignment_id)
    if folder is None:
        folder = submissions_path(workspace, session.assignment_id)
    return FolderSessionInfo(
        assignment_id=session.assignment_id,
        assignment_title=title,
        folder=str(folder),
    )


def folder_lock(root: str | Path) -> RLock:
    """Serializes saves, write-through and sync for one workspace.

    The service runs handlers on a thread pool; without this a sync could
    pull a cell while a save of the same cell writes its file.
    """
    key = Path(root).expanduser().resolve()
    with _locks_guard:
        return _locks.setdefault(key, RLock())


def _journal(folder: Path) -> GradeJournal:
    # One journal file per service process, not one per save.
    history = resolve_history_dir(folder)
    with _locks_guard:
        journal = _journals.get(history)
        if journal is None:
            journal = _journals[history] = GradeJournal(history, actor=BROWSER_ACTOR)
        return journal


def _plan(
    workspace: Workspace,
    assignment_id: str,
    submission_id: int,
    folder: Path,
    questions: set[str] | None = None,
) -> SubmissionPlan:
    """Plan from a stable read, retrying while classic is mid-save."""
    for attempt in range(_ATTEMPTS):
        try:
            return plan_submission(
                workspace,
                assignment_id,
                submission_id,
                folder=folder,
                base=load_write_back_state(workspace.root, assignment_id).cells,
                questions=questions,
            )
        except FileChanged:
            if attempt == _ATTEMPTS - 1:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")


def write_through(
    workspace: Workspace, assignment_id: str, submission_id: int, question_id: str
) -> FolderWrite:
    """Write one saved cell into its `.submission.md`; call under `folder_lock`.

    Never raises: the grade is already committed, and a file that could not
    be written is reported (and retried by the next sync) instead.
    """
    path: Path | None = None
    try:
        folder = locate_submissions_dir(workspace, assignment_id)
        if folder is None:
            return FolderWrite(
                status="failed",
                message="The class folder could not be found; it may have moved. "
                "The grade is saved in the browser session only.",
            )
        known = load_write_back_state(workspace.root, assignment_id).files
        for attempt in range(_ATTEMPTS):
            plan = _plan(workspace, assignment_id, submission_id, folder, {question_id})
            path = plan.path
            if plan.unknown_questions:
                return FolderWrite(
                    status="failed",
                    path=str(path),
                    message=f"{path.name} has no {question_id}, so this grade "
                    "is saved in the browser session only.",
                )
            try:
                written, fingerprint = apply_submission_plan(
                    plan, journal=_journal(folder), summary=SAVE_SUMMARY
                )
                break
            except FileChanged:
                if attempt == _ATTEMPTS - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))
        files: dict[str, tuple[int, int] | None] = {}
        # Only a file that agreed with the base before still does now.
        if fingerprint is not None and known.get(plan.source_key) == plan.fingerprint:
            files[plan.source_key] = fingerprint
        update_write_back_state(
            workspace.root,
            assignment_id,
            cells={key: plan.theirs[key] for key in plan.theirs if key in plan.ours},
            files=files,
        )
        return FolderWrite(
            status="written" if written else "unchanged", path=str(path)
        )
    except FileChanged:
        message = (
            "The file kept changing (is classic mode saving it?). "
            "The grade is saved; the next sync writes it."
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        message = f"The grade is saved, but its file could not be written: {exc}"
    except Exception as exc:  # noqa: BLE001 - the committed save must still succeed
        logger.exception("Folder write-through failed")
        message = f"The grade is saved, but its file could not be written: {exc}"
    return FolderWrite(
        status="failed", path=str(path) if path else None, message=message
    )


def sync_folder(workspace: Workspace, assignment_id: str) -> FolderSyncResult:
    """Bring the workspace and the class folder back into agreement.

    New files are imported; a file cell classic changed is saved into the
    workspace through `save_grade` (so revisions and events reach open
    clients); a workspace cell the file lacks (a failed write-through) is
    written. Files unchanged since they last agreed are not read. Call under
    `folder_lock`.
    """
    folder = locate_submissions_dir(workspace, assignment_id)
    if folder is None:
        raise WriteBackError(
            "The class folder could not be found; it may have moved."
        )
    imported, problems = publish_new_files(workspace, assignment_id, folder)
    result = FolderSyncResult(imported=imported, failed=len(problems), problems=problems)
    state = load_write_back_state(workspace.root, assignment_id)
    current = workspace_cells(workspace, assignment_id)
    for submission_id, source_key, original in _submission_rows(workspace, assignment_id):
        path = folder / source_key.removeprefix("pipeline:")
        try:
            fingerprint = file_fingerprint(path)
        except OSError:
            continue  # a removed file has nothing to sync; write-back reports it
        base = _cells(source_key, original)
        base.update({k: v for k, v in state.cells.items() if k[0] == source_key})
        pending = any(
            key not in base
            or any(
                not (cell.value(a) or AspectValue()).matches(
                    base[key].value(a) or AspectValue(), a
                )
                for a in ("grade", "comment")
            )
            for key, cell in current.get(source_key, {}).items()
        )
        if state.files.get(source_key) == fingerprint and not pending:
            continue
        _sync_submission(workspace, assignment_id, submission_id, folder, result)
    return result


def _sync_submission(
    workspace: Workspace,
    assignment_id: str,
    submission_id: int,
    folder: Path,
    result: FolderSyncResult,
) -> None:
    label = f"submission {submission_id}"
    try:
        plan = _plan(workspace, assignment_id, submission_id, folder)
        label = plan.path.name
        pulls = plan.pulls()
        failed_pulls = 0
        for question_id, values in pulls.items():
            try:
                _pull(workspace, assignment_id, submission_id, question_id, values)
                result.updated += 1
            except (ValueError, WorkspaceConflict, WorkspaceNotFound) as exc:
                failed_pulls += 1
                result.failed += 1
                result.problems.append(f"{label} {question_id}: {exc}")
        for attempt in range(_ATTEMPTS):
            if pulls or attempt:
                plan = _plan(workspace, assignment_id, submission_id, folder)
            try:
                written, fingerprint = apply_submission_plan(
                    plan, journal=_journal(folder), summary=SYNC_SUMMARY
                )
                break
            except FileChanged:
                if attempt == _ATTEMPTS - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))
        result.written += len({change.question_id for change in written})
        unknown = plan.unknown_questions
        if unknown:
            result.failed += len(unknown)
            result.problems.append(
                f"{label} has no {', '.join(unknown)}; "
                "those grades are saved in the browser session only."
            )
        reconciled = not failed_pulls and not unknown
        update_write_back_state(
            workspace.root,
            assignment_id,
            cells={key: plan.theirs[key] for key in plan.theirs if key in plan.ours},
            # Remember the file only if every cell in it agrees; otherwise the
            # next sync reads it again and reports what is still wrong.
            files={plan.source_key: (fingerprint or plan.fingerprint) if reconciled else None},
        )
    except FileChanged:
        result.failed += 1
        result.problems.append(f"{label} kept changing while it was read; try again.")
    except (OSError, ValueError, sqlite3.Error) as exc:
        result.failed += 1
        result.problems.append(f"{label}: {exc}")


def _pull(
    workspace: Workspace,
    assignment_id: str,
    submission_id: int,
    question_id: str,
    values: dict[Aspect, AspectValue],
) -> None:
    answer = workspace.answer(assignment_id, submission_id, question_id)
    workspace.save_grade(
        assignment_id, submission_id, question_id, _pull_edit(answer, values)
    )


def _pull_edit(answer: Answer, values: dict[Aspect, AspectValue]) -> GradeEdit:
    grade = values.get("grade")
    comment = values.get("comment")
    if grade is None:
        mode = answer.score_mode
        labels = list(answer.rubric_labels)
        points = answer.points_awarded if mode == "manual" else None
        target = AspectValue(points=answer.points_awarded, rubric=answer.rubric_labels)
    else:
        target = grade
        labels = list(grade.rubric)
        mode = "rubric" if labels or grade.points is None else "manual"
        points = grade.points if mode == "manual" else None
    edit = GradeEdit(
        request_id=uuid4(),
        expected_context_revision=answer.context_revision,
        expected_revision=answer.revision,
        rubric_labels=labels,
        analysis=comment.text if comment is not None else answer.analysis,
        score_mode=mode,
        points_awarded=points,
    )
    saved = AspectValue(points=grade_points(answer.question, edit), rubric=tuple(labels))
    # The save path derives points from rubric items; a file whose points
    # disagree with its items cannot be copied faithfully, so say so.
    if not saved.matches(target, "grade"):
        raise ValueError(
            "its points do not match its rubric items; regrade it in either app."
        )
    return edit


def shared_workspace_for(submissions: str | Path) -> Path | None:
    """The workspace holding this submissions folder, unless it is a folder session.

    Classic warns then: grades entered in the browser for this folder live
    in the workspace until written back, so the files may be behind.
    """
    try:
        folder = Path(submissions).expanduser().resolve()
        root = discover_workspace(folder)
        if root is None or not (root / DATABASE).is_file():
            return None
        workspace = Workspace(root)
        session = load_folder_session(root)
        for item in workspace.assignment_inputs():
            if item.submissions is None:
                continue
            if submissions_path(workspace, item.id) != folder:
                continue
            if session is not None and session.assignment_id == item.id:
                return None
            return root
    except (OSError, ValueError, sqlite3.Error):
        return None
    return None
