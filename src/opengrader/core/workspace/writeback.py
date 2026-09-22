from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Literal

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
    AspectValue,
    CellState,
    current_cells_from_documents,
    journal_payload,
)
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    load_student_submission_markdown,
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
]

WRITE_BACK_ASPECTS = ("grade", "comment")
WRITE_BACK_SOURCE = "workspace"
_PIPELINE = "pipeline:"
_STATE_DIR = Path(".opengrader") / "write-back"

Cells = dict[tuple[str, str], CellState]


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


def _load_state(root: Path, assignment_id: str) -> Cells | None:
    try:
        raw = json.loads(_state_path(root, assignment_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cells: Cells = {}
    for entry in raw.get("cells", []):
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


def _save_state(root: Path, assignment_id: str, cells: Cells) -> None:
    body = {
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
    target = _state_path(root, assignment_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
    temporary.replace(target)
