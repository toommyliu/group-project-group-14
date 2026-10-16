"""Turn class folders into workspace assignments.

Two front doors use it:

* `opengrader tui FOLDER` on a class folder that is ready to grade: the folder
  becomes the workspace root and its one assignment (`tui/main.py`).
* classic's *Make this a workspace* on a folder of assignments ("Fall 2026/"
  holding "Quiz 1/", "Midterm/", …, each with its own answer key): the
  folder becomes the root and each ready subfolder one assignment. Classic
  itself never opens a workspace, so the Start grading card exits with a
  `MakeWorkspaceRequest` and `classic_command` does the rest outside the app.
  On a folder that already is a workspace, or holds one, the same button is
  *Open workspace* and the app exits with an `OpenWorkspaceRequest` instead.

An import carries the grades already in the files. The `.submission.md`
files stay where they are as the import source and the write-back target
(`W` in the assignment list); from then on the workspace is where grading
happens, so the same folder should not also be graded in place.

Imports go through the workspace service, like every other workspace write,
so this module starts it (`ensure_service`); nothing is imported at module
scope, to keep classic's start-up free of the service code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

__all__ = [
    "MakeWorkspaceRequest",
    "OpenWorkspaceRequest",
    "import_assignment",
    "make_workspace",
]


@dataclass(frozen=True)
class MakeWorkspaceRequest:
    """What classic's *Make this a workspace* asks `classic_command` to do."""

    root: Path
    # (answer key, submissions folder) for each assignment to import; each
    # pair lives in its own subfolder of `root`, which names the assignment.
    assignments: tuple[tuple[Path, Path], ...]


@dataclass(frozen=True)
class OpenWorkspaceRequest:
    """Classic's *Open workspace*: a workspace that already exists, opened
    on its assignment list. Nothing is imported or asked first."""

    root: Path


def import_assignment(client, workspace, folder: Path, qa_file: Path, submissions: Path) -> str:
    """Import one class folder's answer key and graded submissions as the
    workspace assignment for `folder`, and record where its files are.

    `folder` is the assignment's own folder: the workspace root itself, or a
    subfolder of it. Its saved inputs are stored as the pipeline stores them
    (`cli/run._save_inputs`): the folder relative to the root, everything
    else relative to the folder. Returns the assignment ID.
    """
    from opengrader.core.workspace.context import inputs_for_assignment, stored_path
    from opengrader.core.workspace.models import (
        AssignmentInputs,
        PipelineOutput,
        PipelinePublication,
    )

    folder = folder.expanduser().resolve()
    qa = qa_file.expanduser().resolve()
    subs = submissions.expanduser().resolve()
    assignment_id = workspace.assignment_id_for_folder(folder)
    output = PipelineOutput(
        qa_markdown=qa.read_text(encoding="utf-8"),
        submissions_dir=str(subs),
    )
    result = PipelinePublication.model_validate_json(
        client.request(
            "POST", f"/api/assignments/{assignment_id}/imports", output, timeout=300
        )
    )
    saved = inputs_for_assignment(workspace, assignment_id)
    if saved is None:
        saved = AssignmentInputs(
            id=assignment_id,
            folder=stored_path(workspace.root, folder),
            scan="",
            output=stored_path(folder, subs.parent),
            qa=stored_path(folder, qa),
        )
    workspace.save_assignment_inputs(
        saved.model_copy(update={"submissions": stored_path(folder, subs)})
    )
    return result.assignment.id


def make_workspace(
    request: MakeWorkspaceRequest, *, echo: Callable[[str], None] = print
) -> Path:
    """Make `request.root` a workspace and import each assignment into it.

    Stops at the first assignment that cannot be imported, with the ones
    before it already in: running it again re-imports them harmlessly
    (re-publishing keeps saved grades). Returns the workspace root.
    """
    from opengrader.core.workspace.store import Workspace
    from opengrader.server.workspace.client import WorkspaceClient, ensure_service

    root = request.root.expanduser().resolve()
    echo(f"Making {root} a workspace…")
    client = WorkspaceClient(ensure_service(root))
    workspace = Workspace(root)
    for qa_file, submissions in request.assignments:
        folder = _assignment_folder(root, qa_file, submissions)
        echo(f"  importing {folder.relative_to(root).as_posix() or folder.name}…")
        import_assignment(client, workspace, folder, qa_file, submissions)
    return root


def _assignment_folder(root: Path, qa_file: Path, submissions: Path) -> Path:
    """The subfolder of `root` both files are in: the assignment's folder."""
    qa = qa_file.expanduser().resolve()
    subs = submissions.expanduser().resolve()
    for candidate in (qa.parent, *qa.parents):
        if candidate == root:
            break
        if candidate.parent == root:
            if subs == candidate or candidate in subs.parents:
                return candidate
            break
    raise ValueError(f"{qa_file} and {submissions} are not in one subfolder of {root}")
