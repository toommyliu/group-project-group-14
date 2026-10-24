"""Turn class folders into workspace assignments.

Two front doors use it:

* `opengrader tui FOLDER` on a class folder that is ready to grade: the folder
  becomes the workspace root and its one assignment (`tui/main.py`).
* classic's *Make this a workspace* on a folder of assignments ("Fall 2026/"
  holding "Quiz 1/", "Midterm/", …, each with its own answer key): the
  folder becomes the root and each ready subfolder one assignment.
* classic's Workspace card in Helper Tools (`ui/widgets/workspace_card.py`),
  on either kind of folder. `plan_workspace` says what it would do; the
  card makes it on a yes and opens the browser app, keeping classic open. Classic
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
from typing import Callable, Literal

__all__ = [
    "MakeWorkspaceRequest",
    "OpenWorkspaceRequest",
    "WorkspacePlan",
    "import_assignment",
    "make_workspace",
    "plan_workspace",
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
    _end_browser_session(root, echo)
    return root


def _end_browser_session(root: Path, echo: Callable[[str], None]) -> None:
    """A class folder the browser had open (*Open in browser*) becomes a real
    workspace: its folder-session marker goes, so grades saved from now on
    stay in the workspace instead of being written into the files. Nothing
    is lost — a folder session wrote every grade into its files as it went,
    and the import above carried them in. Removed only after every import
    worked, so a failure leaves the folder as it was."""
    from opengrader.core.workspace.folder_session import (
        end_folder_session,
        load_folder_session,
    )

    if load_folder_session(root) is not None:
        end_folder_session(root)
        echo("  the browser now opens it as a workspace")


def _assignment_folder(root: Path, qa_file: Path, submissions: Path) -> Path:
    """The subfolder of `root` both files are in: the assignment's folder."""
    qa = qa_file.expanduser().resolve()
    subs = submissions.expanduser().resolve()
    for candidate in (qa.parent, *qa.parents):
        if candidate == root:
            # One class folder made a workspace: it is its own assignment.
            if subs == root or root in subs.parents:
                return root
            break
        if candidate.parent == root:
            if subs == candidate or candidate in subs.parents:
                return candidate
            break
    raise ValueError(f"{qa_file} and {submissions} are not in one subfolder of {root}")


@dataclass(frozen=True)
class WorkspacePlan:
    """What making `folder` a workspace would do, in words for a teacher.

    `action` is "make" (with `request`), "open" (it is one already, or is
    inside one: `root`), or "none" (`lines` say why not and what to do).
    """

    folder: Path
    action: Literal["make", "open", "none"]
    lines: tuple[str, ...] = ()
    request: MakeWorkspaceRequest | None = None
    root: Path | None = None


def plan_workspace(folder: str | Path) -> WorkspacePlan:
    """Read `folder` and say what *Make workspace* would do with it.

    Two kinds of folder can become one: a class folder whose scans have been
    read (its one assignment), or a folder holding two or more of those
    (each ready one an assignment). Reads files only; nothing is written and
    no service is started.
    """
    from opengrader.core.class_folder import (
        ROLE_QA,
        find_assignment_folders,
        is_workspace,
        scan_class_folder,
    )
    from opengrader.core.workspace.folder_session import load_folder_session
    from opengrader.core.workspace.store import DATABASE, discover_workspace

    chosen = Path(folder).expanduser()
    if not str(folder).strip() or not chosen.is_dir():
        return WorkspacePlan(chosen, "none", ("Choose a folder that exists.",))
    root = chosen.resolve()
    if is_workspace(root):
        return WorkspacePlan(root, "open", ("This folder is already a workspace.",), root=root)
    holder = discover_workspace(root.parent) if root.parent != root else None
    if holder is not None and is_workspace(holder):
        return WorkspacePlan(
            root,
            "open",
            (f"This folder is already part of the workspace in {holder}.",),
            root=holder,
        )

    try:
        found = scan_class_folder(root)
    except OSError as exc:
        return WorkspacePlan(root, "none", (f"Could not read this folder: {exc}",))
    if found.qa_file is not None:
        if found.submissions is None:
            return WorkspacePlan(
                root,
                "none",
                (
                    "This class folder has an answer key, but its scans have not "
                    "been read yet. Prepare it first: choose it on the Start "
                    "grading card and press Launch Grading.",
                ),
            )
        count = sum(1 for _ in found.submissions.rglob("*.submission.md"))
        lines = [
            f"One assignment: {root.name}, with {count} student paper(s). Any "
            "grades already given come along."
        ]
        if load_folder_session(root) is not None:
            lines.append(
                "It is open in the browser app as a class folder right now. It "
                "becomes a workspace instead, and nothing is lost."
            )
        return WorkspacePlan(
            root,
            "make",
            tuple(lines),
            request=MakeWorkspaceRequest(
                root=root, assignments=((found.qa_file, found.submissions),)
            ),
        )

    assignments = find_assignment_folders(root)
    if assignments.workspaces:
        names = ", ".join(f"{path.name}/" for path in assignments.workspaces)
        return WorkspacePlan(
            root,
            "none",
            (
                f"{names} is already a workspace of its own, and a workspace "
                "cannot hold another. Choose that folder to open it.",
            ),
        )
    if assignments.same_key:
        group = " and ".join(f"{path.name}/" for path in assignments.same_key[0])
        return WorkspacePlan(
            root,
            "none",
            (
                f"{group} have the same answer key, so they are sections of one "
                "assignment. Put their scans in one folder and prepare it once "
                "(Prepare an assignment → Advanced settings), then try again.",
            ),
        )
    if len(assignments.assignments) == 1:
        only = assignments.assignments[0].folder
        return WorkspacePlan(
            root,
            "none",
            (f"This folder holds one assignment, {only.name}/. Choose that folder instead.",),
        )
    if len(assignments.assignments) >= 2:
        ready = assignments.ready
        if not ready:
            return WorkspacePlan(
                root,
                "none",
                (
                    "None of the assignments in this folder has had its scans "
                    "read yet. Prepare each one first (choose it on the Start "
                    "grading card and press Launch Grading).",
                ),
            )
        # A subfolder the browser opened on its own keeps a database of its
        # own; a workspace inside it would find that one instead of this.
        alone = [item.folder.name for item in ready if (item.folder / DATABASE).is_file()]
        if alone:
            name = alone[0]
            return WorkspacePlan(
                root,
                "none",
                (
                    f"{name}/ was opened in the browser app on its own, so it keeps "
                    "its own browser copy. Stop the browser server (the status "
                    "line on the Start grading card), delete "
                    f"{name}/.opengrader/workspace.sqlite3 — its grades are safe "
                    "in its files — and try again.",
                ),
            )
        lines = [
            f"{len(ready)} assignments: "
            + ", ".join(item.folder.name for item in ready)
            + ". Any grades already given come along."
        ]
        waiting = [item.folder.name for item in assignments.assignments if item not in ready]
        if waiting:
            lines.append(
                "Left out for now, because their scans have not been read yet: "
                + ", ".join(waiting)
                + ". Prepare each one later and it joins the workspace."
            )
        return WorkspacePlan(
            root,
            "make",
            tuple(lines),
            request=MakeWorkspaceRequest(
                root=root,
                assignments=tuple(
                    (item.qa_file, item.submissions)  # type: ignore[misc]
                    for item in ready
                ),
            ),
        )

    if found.ambiguity(ROLE_QA) is not None:
        reason = (
            "More than one file here looks like an answer key. Keep one (or "
            "choose it on the Start grading card) and try again."
        )
    else:
        reason = (
            "There is nothing here to make a workspace from. Choose a class "
            "folder whose scans have been read (an answer key and student "
            "papers), or a folder holding several of them."
        )
    return WorkspacePlan(root, "none", (reason,))
