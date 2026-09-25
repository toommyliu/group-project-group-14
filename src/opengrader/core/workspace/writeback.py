from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Collection, Literal

from opengrader.core.handoff.merge import (
    MergeChange,
    MergeOutcome,
    MergePlan,
    apply_merge_plan,
    merge_actor,
    plan_merge,
    resolve_conflicts,
)
from opengrader.core.history.journal import GradeJournal, resolve_history_dir
from opengrader.core.history.restore import (
    Aspect,
    AspectValue,
    CellState,
    current_cells_from_documents,
    journal_payload,
    merge_update_for_question,
)
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    load_student_submission_markdown,
    parse_student_submission_markdown,
    update_student_submission_markdown,
)
from opengrader.core.workspace.store import DOCUMENT, Workspace
from opengrader.core.workspace.context import inputs_for_assignment, resolve_path

__all__ = [
    "WRITE_BACK_ASPECTS",
    "WRITE_BACK_SOURCE",
    "WriteBackError",
    "WriteBackPlan",
    "locate_submissions_dir",
    "plan_write_back",
    "apply_write_back",
    "FileChanged",
    "SubmissionPlan",
    "plan_submission",
    "apply_submission_plan",
    "WriteBackState",
    "load_write_back_state",
    "update_write_back_state",
    "workspace_cells",
    "file_fingerprint",
]

WRITE_BACK_ASPECTS = ("grade", "comment")
WRITE_BACK_SOURCE = "workspace"
_PIPELINE = "pipeline:"
_STATE_DIR = Path(".opengrader") / "write-back"

Cells = dict[tuple[str, str], CellState]
# (size, mtime_ns): cheap enough to check every file on each browser focus.
Fingerprint = tuple[int, int]


class WriteBackError(ValueError):
    """Workspace grades cannot be written to their source files."""


@dataclass(frozen=True)
class WriteBackPlan:
    assignment_id: str
    assignment_title: str
    submissions_dir: Path
    plan: MergePlan
    paths_by_key: dict[str, Path]
    theirs: Cells = field(repr=False)
    base: Cells = field(repr=False, default_factory=dict)
    base_from: Literal["import", "last write-back"] = "import"
    missing_files: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()

    @property
    def nothing_to_do(self) -> bool:
        return not self.plan.changes and not self.plan.conflicts


def locate_submissions_dir(workspace: Workspace, assignment_id: str) -> Path | None:
    saved = inputs_for_assignment(workspace, assignment_id)
    if saved is None or saved.submissions is None:
        return None
    folder = resolve_path(workspace.root, saved.folder)
    source = resolve_path(folder, saved.submissions)
    return source if source.is_dir() else None


def plan_write_back(root: str | Path, assignment_id: str) -> WriteBackPlan:
    workspace = Workspace(Path(root))
    title = next(
        (item.title for item in workspace.assignments() if item.id == assignment_id),
        None,
    )
    if title is None:
        raise WriteBackError("That assignment is no longer in this workspace.")
    folder = locate_submissions_dir(workspace, assignment_id)
    if folder is None:
        raise WriteBackError(
            f"'{title}' has no available source submissions folder. "
            "The folder may have moved, or this assignment was created without one."
        )

    rows = _submission_rows(workspace, assignment_id)
    ours: Cells = {}
    theirs: Cells = {}
    base: Cells = {}
    labels: dict[str, str] = {}
    paths: dict[str, Path] = {}
    missing: list[str] = []
    unreadable: list[str] = []
    saved = _load_state(workspace.root, assignment_id)

    for submission_id, source_key, original in rows:
        relative = source_key[len(_PIPELINE):]
        path = folder / relative
        if not path.is_file():
            missing.append(relative)
            continue
        try:
            document = load_student_submission_markdown(path)
        except (OSError, ValueError) as exc:
            unreadable.append(f"{relative}: {exc}")
            continue
        paths[source_key] = path
        labels[source_key] = _label(document, path)
        ours.update(_cells(source_key, document))
        theirs.update(_cells(source_key, workspace.document(assignment_id, submission_id)))
        base.update(_cells(source_key, original))

    # Use the last successful write-back as the merge base for existing cells.
    if saved is not None:
        base.update(saved)

    # Skip blank workspace placeholders for questions missing from source files.
    theirs_here = {
        key: cell
        for key, cell in theirs.items()
        if key in ours or not _blank(cell)
    }
    plan = plan_merge(ours, theirs_here, base, labels=labels, aspects=WRITE_BACK_ASPECTS)
    return WriteBackPlan(
        assignment_id=assignment_id,
        assignment_title=title,
        submissions_dir=folder,
        plan=plan,
        paths_by_key=paths,
        theirs=theirs,
        base=base,
        base_from="import" if saved is None else "last write-back",
        missing_files=tuple(missing),
        unreadable=tuple(unreadable),
    )


def apply_write_back(
    root: str | Path,
    write_back: WriteBackPlan,
    *,
    decisions: dict[str, str] | None = None,
    policy: str = "ours",
) -> MergeOutcome:
    plan = write_back.plan
    changes = list(plan.changes) + list(
        resolve_conflicts(plan.conflicts, policy=policy, decisions=decisions or {})
    )
    outcome = apply_merge_plan(changes, write_back.paths_by_key, journal=None)
    _journal(write_back, outcome.written)

    failed = {(change.student_key, change.question_id) for change, _ in outcome.failed}
    # Failed cells keep their old base so retries still offer the change.
    state = {
        key: (write_back.base[key] if key in failed and key in write_back.base else cell)
        for key, cell in write_back.theirs.items()
        if key not in failed or key in write_back.base
    }
    _save_state(Path(root), write_back.assignment_id, state)
    return outcome


class FileChanged(OSError):
    """A submission file changed while it was read or about to be replaced.

    Classic mode may be saving the same file; the caller retries from a
    fresh read rather than overwrite (or parse) a half-written file.
    """


def file_fingerprint(path: Path) -> Fingerprint:
    info = path.stat()
    return (info.st_size, info.st_mtime_ns)


@dataclass(frozen=True)
class SubmissionPlan:
    """One submission file against its workspace answers and merge base.

    `ours` is the file and `theirs` the workspace, as in `plan_write_back`,
    so the same `plan_merge` rules apply to a single file or cell.
    """

    assignment_id: str
    submission_id: int
    source_key: str
    path: Path
    fingerprint: Fingerprint
    text: str = field(repr=False)
    document: StudentSubmissionDocument = field(repr=False)
    ours: Cells = field(repr=False)
    theirs: Cells = field(repr=False)
    base: Cells = field(repr=False)
    plan: MergePlan = field(repr=False)

    @property
    def pushes(self) -> tuple[MergeChange, ...]:
        # A cell edited on both sides goes to the workspace value: in a folder
        # session that is the newest explicit save (see `folder_session`).
        return tuple(self.plan.changes) + resolve_conflicts(
            self.plan.conflicts, policy="theirs"
        )

    @property
    def unknown_questions(self) -> tuple[str, ...]:
        """Graded in the workspace, but the file has no block to hold them."""
        return tuple(
            sorted(
                question
                for key, question in self.theirs
                if (key, question) not in self.ours
            )
        )

    def pulls(self) -> dict[str, dict[Aspect, AspectValue]]:
        """File edits the workspace has not seen: the file moved, it did not."""
        found: dict[str, dict[Aspect, AspectValue]] = {}
        for cell_key, file_cell in sorted(self.ours.items()):
            workspace_cell = self.theirs.get(cell_key)
            base_cell = self.base.get(cell_key)
            if workspace_cell is None or base_cell is None:
                continue
            for aspect in WRITE_BACK_ASPECTS:
                mine = file_cell.value(aspect) or AspectValue()
                theirs = workspace_cell.value(aspect) or AspectValue()
                base = base_cell.value(aspect)
                if base is None:
                    continue
                if not mine.matches(base, aspect) and theirs.matches(base, aspect):
                    found.setdefault(cell_key[1], {})[aspect] = mine
        return found


def plan_submission(
    workspace: Workspace,
    assignment_id: str,
    submission_id: int,
    *,
    folder: Path,
    base: Cells | None = None,
    questions: Collection[str] | None = None,
) -> SubmissionPlan:
    """Plan one file without reading the rest of the folder.

    `base` is the saved write-back state (cells of other files are ignored);
    cells it lacks fall back to the imported copy, as in `plan_write_back`.
    """
    with workspace.connect() as db:
        row = db.execute(
            "SELECT source_key, original FROM submissions WHERE assignment_id=? AND id=?",
            (assignment_id, submission_id),
        ).fetchone()
    if row is None:
        raise WriteBackError("That submission is no longer in this workspace.")
    source_key = str(row["source_key"])
    if not source_key.startswith(_PIPELINE):
        raise WriteBackError("This submission was not imported from the class folder.")
    relative = source_key[len(_PIPELINE):]
    path = folder / relative
    if not path.is_file():
        raise WriteBackError(f"{relative} is no longer in the class folder.")
    text, fingerprint = _read_stable(path)
    try:
        document = parse_student_submission_markdown(text, base_path=path.parent)
    except ValueError as exc:
        raise WriteBackError(f"{path.name} could not be read: {exc}") from exc

    def wanted(cells: Cells) -> Cells:
        if questions is None:
            return cells
        return {key: cell for key, cell in cells.items() if key[1] in questions}

    ours = wanted(_cells(source_key, document))
    stored = DOCUMENT.validate_json(row["original"])
    merged_base = _cells(source_key, stored)
    merged_base.update(
        {key: cell for key, cell in (base or {}).items() if key[0] == source_key}
    )
    theirs = wanted(
        workspace_cells(workspace, assignment_id, submission_id).get(source_key, {})
    )
    # Blank placeholders for questions the file lacks are not changes.
    theirs = {
        key: cell for key, cell in theirs.items() if key in ours or not _blank(cell)
    }
    plan = plan_merge(
        ours,
        theirs,
        wanted(merged_base),
        labels={source_key: _label(document, path)},
        aspects=WRITE_BACK_ASPECTS,
    )
    return SubmissionPlan(
        assignment_id=assignment_id,
        submission_id=submission_id,
        source_key=source_key,
        path=path,
        fingerprint=fingerprint,
        text=text,
        document=document,
        ours=ours,
        theirs=theirs,
        base=wanted(merged_base),
        plan=plan,
    )


def apply_submission_plan(
    plan: SubmissionPlan,
    *,
    journal: GradeJournal | None,
    summary: str,
) -> tuple[tuple[MergeChange, ...], Fingerprint | None]:
    """Write the plan's pushes in one atomic replace of the file.

    Returns what was written and the file's new fingerprint (None when
    nothing needed writing). Raises `FileChanged` if the file moved since
    the plan read it; nothing is written then.
    """
    changes = plan.pushes
    if not changes:
        return (), None
    by_question: dict[str, dict[Aspect, AspectValue]] = {}
    for change in changes:
        by_question.setdefault(change.question_id, {})[change.aspect] = change.theirs
    text = plan.text
    for question_id, values in sorted(by_question.items()):
        text = update_student_submission_markdown(
            text, merge_update_for_question(question_id, values)
        )
    fingerprint = _replace_atomically(plan.path, text, plan.fingerprint)
    if journal is not None:
        for change in changes:
            try:
                journal.append(
                    kind=change.aspect,
                    student_id=plan.document.student_id,
                    student_name=plan.document.student_name,
                    question_id=change.question_id,
                    summary=summary,
                    old=journal_payload(change.aspect, change.ours),
                    new=journal_payload(change.aspect, change.theirs),
                )
            except OSError:
                pass
    return changes, fingerprint


def workspace_cells(
    workspace: Workspace, assignment_id: str, submission_id: int | None = None
) -> dict[str, Cells]:
    """Workspace grade and comment per cell, keyed by source file.

    Read straight from the answers table in one query: a folder sync checks
    every submission on each browser focus.
    """
    query = (
        "SELECT s.source_key, a.question_id, a.value FROM answers a "
        "JOIN submissions s ON s.assignment_id=a.assignment_id AND s.id=a.submission_id "
        "WHERE a.assignment_id=?"
    )
    parameters: tuple[object, ...] = (assignment_id,)
    if submission_id is not None:
        query += " AND a.submission_id=?"
        parameters += (submission_id,)
    found: dict[str, Cells] = {}
    with workspace.connect() as db:
        for row in db.execute(query, parameters):
            value = json.loads(row["value"])
            found.setdefault(row["source_key"], {})[
                (row["source_key"], row["question_id"])
            ] = CellState(
                values={
                    "grade": AspectValue(
                        points=value.get("points_awarded"),
                        rubric=tuple(value.get("rubric_labels") or ()),
                    ),
                    "comment": AspectValue(text=value.get("analysis") or ""),
                }
            )
    return found


def _read_stable(path: Path) -> tuple[str, Fingerprint]:
    # Classic saves with a plain truncate-and-write; a read that overlaps one
    # sees a changed size or mtime, or fewer bytes than the file now holds.
    before = file_fingerprint(path)
    data = path.read_bytes()
    after = file_fingerprint(path)
    if before != after or len(data) != after[0]:
        raise FileChanged(f"{path.name} changed while it was being read.")
    return data.decode("utf-8"), after


def _replace_atomically(path: Path, text: str, expected: Fingerprint) -> Fingerprint:
    """Replace the file whole, so a reader sees the old or the new, never half.

    The replace is skipped if the file changed since `expected` was read, so
    a classic save in between is not lost. (A save landing in the moment
    between that check and the rename can still be lost; classic's own
    modified-time check then refuses its next save instead of clobbering.)
    """
    if not os.access(path, os.W_OK):
        raise PermissionError(f"{path.name} is read-only.")
    mode = stat.S_IMODE(path.stat().st_mode)
    # A hidden name that no *.submission.md scan picks up.
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
            written = os.fstat(stream.fileno())
        os.chmod(temporary, mode)
        if file_fingerprint(path) != expected:
            raise FileChanged(f"{path.name} changed while it was being saved.")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return (written.st_size, written.st_mtime_ns)


def _submission_rows(
    workspace: Workspace, assignment_id: str
) -> list[tuple[int, str, StudentSubmissionDocument]]:
    with workspace.connect() as db:
        rows = db.execute(
            "SELECT id, source_key, original FROM submissions "
            "WHERE assignment_id=? ORDER BY id",
            (assignment_id,),
        ).fetchall()
    return [
        (row["id"], row["source_key"], DOCUMENT.validate_json(row["original"]))
        for row in rows
        if str(row["source_key"]).startswith(_PIPELINE)
    ]


def _cells(key: str, document: StudentSubmissionDocument) -> Cells:
    # Match by source file even if the student ID changes.
    anonymous = replace(document, student_id=key, student_name="")
    return {
        (key, question_id): CellState(
            values={a: cell.values[a] for a in WRITE_BACK_ASPECTS if a in cell.values}
        )
        for (_student, question_id), cell in current_cells_from_documents([anonymous]).items()
    }


def _blank(cell: CellState) -> bool:
    grade = cell.value("grade")
    comment = cell.value("comment")
    return (grade is None or (grade.points is None and not grade.rubric)) and (
        comment is None or not comment.text.strip()
    )


def _label(document: StudentSubmissionDocument, path: Path) -> str:
    return document.student_id.strip() or path.parent.name or path.stem


def _journal(write_back: WriteBackPlan, written: tuple[MergeChange, ...]) -> None:
    if not written:
        return
    journal = GradeJournal(
        resolve_history_dir(write_back.submissions_dir),
        actor=merge_actor(WRITE_BACK_SOURCE),
    )
    documents: dict[str, StudentSubmissionDocument] = {}
    for change in written:
        path = write_back.paths_by_key[change.student_key]
        if change.student_key not in documents:
            try:
                documents[change.student_key] = load_student_submission_markdown(path)
            except (OSError, ValueError):
                continue
        document = documents[change.student_key]
        try:
            journal.append(
                kind=change.aspect,
                student_id=document.student_id,
                student_name=document.student_name,
                question_id=change.question_id,
                summary=f"wrote back {change.aspect} from the workspace",
                old=journal_payload(change.aspect, change.ours),
                new=journal_payload(change.aspect, change.theirs),
            )
        except OSError:
            pass


def _state_path(root: Path, assignment_id: str) -> Path:
    return root / _STATE_DIR / f"{assignment_id}.json"


@dataclass(frozen=True)
class WriteBackState:
    """The merge base per cell, and the files known to agree with it.

    `files` holds the fingerprint of each file as it was when every cell in
    it last matched the base, so a folder sync can skip files nobody touched.
    A full write-back rewrites the state without fingerprints, which only
    means the next sync reads every file once.
    """

    cells: Cells = field(default_factory=dict)
    files: dict[str, Fingerprint] = field(default_factory=dict)


def load_write_back_state(root: Path, assignment_id: str) -> WriteBackState:
    cells = _load_state(root, assignment_id) or {}
    files: dict[str, Fingerprint] = {}
    try:
        raw = json.loads(_state_path(root, assignment_id).read_text(encoding="utf-8"))
        for key, value in (raw.get("files") or {}).items():
            size, mtime = value
            files[str(key)] = (int(size), int(mtime))
    except (OSError, ValueError, TypeError, AttributeError):
        files = {}
    return WriteBackState(cells=cells, files=files)


def update_write_back_state(
    root: Path,
    assignment_id: str,
    *,
    cells: Cells,
    files: dict[str, Fingerprint | None] | None = None,
) -> None:
    """Merge a few cells (and fingerprints; None forgets one) into the state.

    Callers serialize this per workspace; see `folder_session.folder_lock`.
    """
    state = load_write_back_state(root, assignment_id)
    merged = {**state.cells, **cells}
    known = dict(state.files)
    for key, fingerprint in (files or {}).items():
        if fingerprint is None:
            known.pop(key, None)
        else:
            known[key] = fingerprint
    _save_state(root, assignment_id, merged, known)


def _load_state(root: Path, assignment_id: str) -> Cells | None:
    try:
        raw = json.loads(_state_path(root, assignment_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cells: Cells = {}
    try:
        entries = raw.get("cells", [])
    except AttributeError:
        return None
    for entry in entries:
        try:
            values = {
                aspect: AspectValue(
                    points=value.get("points"),
                    rubric=tuple(value.get("rubric", ())),
                    text=value.get("text", ""),
                )
                for aspect, value in entry["values"].items()
            }
            cells[(entry["file"], entry["question"])] = CellState(values=values)
        except (KeyError, TypeError, AttributeError):
            return None
    return cells


def _save_state(
    root: Path,
    assignment_id: str,
    cells: Cells,
    files: dict[str, Fingerprint] | None = None,
) -> None:
    body: dict[str, object] = {
        "version": 1,
        "cells": [
            {
                "file": key,
                "question": question,
                "values": {
                    aspect: {k: v for k, v in asdict(value).items() if k in ("points", "rubric", "text")}
                    for aspect, value in cell.values.items()
                },
            }
            for (key, question), cell in sorted(cells.items())
        ],
    }
    if files:
        body["files"] = {key: list(value) for key, value in sorted(files.items())}
    target = _state_path(root, assignment_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
    temporary.replace(target)
