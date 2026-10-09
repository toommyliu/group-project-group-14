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

Two things other than a grade can be changed from the browser, and for both
the file is written *first* and the workspace only once that has worked:
a question's rubric (`edit_rubric`, into the answer-key file) and a
submission's student name and ID (`set_student`, into its `.submission.md`).
That order is what lets a sync treat the file as the truth for them without
keeping a merge base: the two can only differ because the terminal app (or a
person) changed the file, so the file's value is taken (`sync_answer_key`,
and the identity check in `_sync_submission`).
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
from typing import Callable
from uuid import uuid4

from opengrader.core.grading.edits import GradeEdit, grade_points
from opengrader.core.grading.rubric_edit import diff_rubric_options
from opengrader.core.history.journal import GradeJournal, resolve_history_dir
from opengrader.core.history.restore import Aspect, AspectValue
from opengrader.core.qa_file import (
    QARubricFlatOption,
    QARubricItem,
    find_qa_question,
    flatten_rubric_options,
    parse_qa_markdown,
)
from opengrader.core.qa_file.editing import QuestionEdit, edit_qa_markdown
from opengrader.core.submissions.student_file import set_student_identity_markdown
from opengrader.core.workspace.answer_key import (
    check_answer_key,
    replace_answer_key,
)
from opengrader.core.workspace.context import (
    inputs_for_assignment,
    resolve_path,
    submissions_path,
)
from opengrader.core.workspace.intake import publish_new_files
from opengrader.core.workspace.models import (
    Answer,
    FolderSessionInfo,
    FolderSyncResult,
    FolderWrite,
    RubricEditResult,
    Submission,
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
    _read_stable,
    _replace_atomically,
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
    "answer_key_path",
    "edit_rubric",
    "end_folder_session",
    "folder_lock",
    "folder_session_info",
    "load_folder_session",
    "set_student",
    "shared_workspace_for",
    "start_folder_session",
    "sync_answer_key",
    "sync_folder",
    "write_student_file",
    "write_through",
]

logger = logging.getLogger(__name__)

FOLDER_SESSION = Path(".opengrader") / "folder-session.json"
# Grade history actor for file values replaced from the browser, so
# `grade-restore apply --without-actor browser` undoes them.
BROWSER_ACTOR = "browser"
SAVE_SUMMARY = "saved from the browser"
SYNC_SUMMARY = "saved from the browser (written on sync)"
RUBRIC_SUMMARY = "rubric edited in the browser"
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
    from opengrader.core.workspace.folder_roster import roster_info

    return FolderSessionInfo(
        assignment_id=session.assignment_id,
        assignment_title=title,
        folder=str(folder),
        roster=roster_info(workspace),
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
    # The answer key first: a grade the terminal app saved against an
    # edited rubric can only be brought in once the workspace has that rubric.
    key_problem = sync_answer_key(workspace, assignment_id)
    imported, problems = publish_new_files(workspace, assignment_id, folder)
    if key_problem:
        problems = [key_problem, *problems]
    # Imported here: `folder_roster` builds on this module.
    from opengrader.core.workspace.folder_roster import sync_roster

    roster = sync_roster(workspace, assignment_id)
    problems = [*roster.problems, *problems]
    result = FolderSyncResult(
        imported=imported,
        failed=len(problems),
        problems=problems,
        roster_changed=roster.changed,
    )
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
        if _pull_identity(workspace, assignment_id, submission_id, plan):
            result.updated += 1
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


# ---------------------------------------------------------------------------
# The answer key: a question's rubric, edited from the browser
# ---------------------------------------------------------------------------


def answer_key_path(workspace: Workspace, assignment_id: str) -> Path | None:
    """The class folder's answer-key file, if the import recorded one that exists."""
    saved = inputs_for_assignment(workspace, assignment_id)
    if saved is None or not saved.qa:
        return None
    path = resolve_path(resolve_path(workspace.root, saved.folder), saved.qa)
    return path if path.is_file() else None


def _unix_newlines(text: str) -> str:
    # The import reads the answer key in text mode, so the workspace's copy
    # has plain newlines whatever the file uses. Compared this way a file
    # saved on Windows is not "changed" on every sync.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read_answer_key(path: Path) -> tuple[str, tuple[int, int]]:
    for attempt in range(_ATTEMPTS):
        try:
            return _read_stable(path)
        except FileChanged:
            if attempt == _ATTEMPTS - 1:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError("unreachable")


def sync_answer_key(workspace: Workspace, assignment_id: str) -> str | None:
    """Take the answer-key file's contents if they changed; a problem, or None.

    The file wins outright: the browser only ever changes it through
    `edit_rubric`, which writes the file before the workspace, so a
    difference means the terminal app or a person edited it. Call under
    `folder_lock`.
    """
    path = answer_key_path(workspace, assignment_id)
    if path is None:
        return None
    try:
        text, _ = _read_answer_key(path)
        source = _unix_newlines(text)
        if source == workspace.assignment_source(assignment_id):
            return None
        replace_answer_key(workspace, assignment_id, source, strict=False)
    except FileChanged:
        return f"{path.name} kept changing while it was read; try again."
    except (OSError, ValueError, sqlite3.Error) as exc:
        return (
            f"The answer key ({path.name}) changed in the class folder and "
            f"could not be brought into the browser: {exc}"
        )
    return None


def _flat_options(rubric: list[QARubricItem], points: float) -> list[QARubricFlatOption]:
    # A question with no rubric is graded Correct / Incorrect; those are the
    # labels its saved grades carry (see `grading.questions.rubric_options`).
    return flatten_rubric_options(rubric) or [
        QARubricFlatOption("Correct", 0.0, "correct", "exclusive"),
        QARubricFlatOption("Incorrect", -float(points), "incorrect", "exclusive"),
    ]


def edit_rubric(
    workspace: Workspace,
    assignment_id: str,
    question_id: str,
    *,
    version: str | None,
    rubric: list[QARubricItem],
    expected: list[QARubricItem],
    actor: str | None = None,
) -> RubricEditResult:
    """Save one question's rubric into the answer-key file, then everywhere else.

    In order, so that a failure leaves the class folder consistent:

    1. the file is read, and anything it gained since the import is taken;
    2. the edit is refused if the rubric is no longer the one the editor
       opened (`expected`), or if it drops an item saved grades still use —
       both checked before anything is written;
    3. only that question's rubric section of the file is rewritten;
    4. the workspace takes the new key, renaming and re-scoring the grades
       that selected a changed item;
    5. each of those grades is written into its `.submission.md`.

    A grade whose file cannot be written in step 5 is reported and written
    by the next sync, as a failed grade save is. Call under `folder_lock`.
    """
    path = answer_key_path(workspace, assignment_id)
    if path is None:
        raise WriteBackError(
            "The answer key file could not be found in the class folder; it "
            "may have moved. The rubric was not changed."
        )
    try:
        text, fingerprint = _read_answer_key(path)
    except FileChanged as exc:
        raise WorkspaceConflict(
            f"{path.name} is being saved by another program. Try again in a moment."
        ) from exc
    if _unix_newlines(text) != workspace.assignment_source(assignment_id):
        # Changed in the folder since the browser last looked. Refuse rather
        # than merge: what the editor showed may no longer be what is there.
        replace_answer_key(workspace, assignment_id, _unix_newlines(text), strict=False)

    document = parse_qa_markdown(text)
    question = find_qa_question(document, question_id, version=version)
    if getattr(question, "subquestions", None):
        raise ValueError(
            f"Question {question_id} is graded by its parts; edit the rubric "
            "of a part instead."
        )
    if question.rubric != expected:
        raise WorkspaceConflict(
            "This rubric was changed somewhere else since you opened it. "
            "Close this and open it again to see the current rubric."
        )
    if rubric == question.rubric:
        return RubricEditResult(changed=False)

    edited_text = edit_qa_markdown(
        text,
        [
            QuestionEdit(
                source_line=question.source_line,
                prompt=question.prompt,
                points=question.points,
                answers=question.answers,
                rubric=rubric,
                version=version,
            )
        ],
    )
    # Parsing is the validation: a rubric the answer-key format does not
    # allow is refused here, before the file is touched.
    edited = find_qa_question(
        parse_qa_markdown(edited_text), question_id, version=version
    )
    before = _flat_options(question.rubric, question.points)
    after = _flat_options(edited.rubric, edited.points)
    relabel = {(version, question_id): diff_rubric_options(before, after).renames}
    source = _unix_newlines(edited_text)
    check_answer_key(workspace, assignment_id, source, relabel=relabel)

    try:
        _replace_atomically(path, edited_text, fingerprint)
    except FileChanged as exc:
        raise WorkspaceConflict(
            f"{path.name} changed while the rubric was being saved, so "
            "nothing was written. Open the rubric again and retry."
        ) from exc
    except OSError as exc:
        raise WriteBackError(
            f"{path.name} could not be written, so the rubric was not "
            f"changed: {exc}"
        ) from exc

    update = replace_answer_key(
        workspace, assignment_id, source, relabel=relabel, actor=actor
    )
    problems: list[str] = []
    for submission_id, changed_question in update.regraded:
        written = write_through(workspace, assignment_id, submission_id, changed_question)
        if written.status == "failed" and written.message:
            problems.append(written.message)
    folder = locate_submissions_dir(workspace, assignment_id)
    if folder is not None:
        count = len(update.regraded)
        try:
            _journal(folder).append(
                kind="rubric",
                student_id="",
                student_name="",
                question_id=question_id,
                summary=f"{RUBRIC_SUMMARY} · {count} grade{'' if count == 1 else 's'} updated",
                old={"options": [option.label for option in before]},
                new={"options": [option.label for option in after]},
            )
        except OSError:
            pass  # history is a record of the edit, not a condition of it
    return RubricEditResult(
        changed=True, regraded=len(update.regraded), problems=problems
    )


# ---------------------------------------------------------------------------
# Who a submission belongs to
# ---------------------------------------------------------------------------


def _same_identity(submission: Submission, name: str, student_id: str) -> bool:
    return (
        " ".join(submission.student_name.split()) == " ".join(name.split())
        and submission.student_id.strip() == student_id.strip()
    )


def write_student_file(
    workspace: Workspace,
    assignment_id: str,
    submission_id: int,
    student_name: str,
    student_id: str,
) -> Callable[[], None]:
    """Write a student's name and ID into a submission's `.submission.md`.

    Only the name and ID lines of the file's header are rewritten; the graded
    answers below are not touched. Raises without changing anything if the
    file cannot be written.

    Returns a function to call once the workspace holds the same name and
    ID: it records that the file and the workspace agree again, so the next
    sync does not re-read the file only because this wrote it. Call under
    `folder_lock`.
    """
    folder = locate_submissions_dir(workspace, assignment_id)
    source_key = next(
        (
            key
            for found, key, _ in _submission_rows(workspace, assignment_id)
            if found == submission_id
        ),
        None,
    )
    if source_key is None:
        raise WorkspaceNotFound("Submission not found.")
    if folder is None:
        raise WriteBackError(
            "The class folder could not be found; it may have moved. The "
            "student was not changed."
        )
    path = folder / source_key.removeprefix("pipeline:")
    known = load_write_back_state(workspace.root, assignment_id).files.get(source_key)
    try:
        for attempt in range(_ATTEMPTS):
            try:
                text, fingerprint = _read_stable(path)
                updated = set_student_identity_markdown(
                    text, student_id=student_id, student_name=student_name
                )
                written = (
                    _replace_atomically(path, updated, fingerprint)
                    if updated != text
                    else fingerprint
                )
                break
            except FileChanged:
                if attempt == _ATTEMPTS - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except FileChanged as exc:
        raise WorkspaceConflict(
            f"{path.name} is being saved by another program, so the student "
            "was not changed. Try again in a moment."
        ) from exc
    except OSError as exc:
        raise WriteBackError(
            f"{path.name} could not be written, so the student was not "
            f"changed: {exc}"
        ) from exc

    def settle() -> None:
        if known == fingerprint:
            update_write_back_state(
                workspace.root, assignment_id, cells={}, files={source_key: written}
            )

    return settle


def set_student(
    workspace: Workspace,
    assignment_id: str,
    submission_id: int,
    student_name: str,
    student_id: str,
) -> Submission:
    """Set a submission's student in its `.submission.md`, then in the workspace.

    If the file cannot be written nothing changes anywhere, so the browser
    never shows a name the class folder does not have. Call under
    `folder_lock`.
    """
    settle = write_student_file(
        workspace, assignment_id, submission_id, student_name, student_id
    )
    submission = workspace.set_student(
        assignment_id, submission_id, student_name, student_id
    )
    settle()
    return submission


def _pull_identity(
    workspace: Workspace, assignment_id: str, submission_id: int, plan: SubmissionPlan
) -> bool:
    """Take the file's student name and ID if they differ; whether they did."""
    current = next(
        (
            item
            for item in workspace.submissions(assignment_id)
            if item.id == submission_id
        ),
        None,
    )
    name = plan.document.student_name or ""
    student_id = plan.document.student_id or ""
    if current is None or _same_identity(current, name, student_id):
        return False
    workspace.set_student(
        assignment_id, submission_id, " ".join(name.split()), student_id.strip()
    )
    return True


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
