"""Resolve the workspace and assignment implied by a directory."""

import os
import json
from pathlib import Path

from opengrader.core.workspace.models import AssignmentInputs
from opengrader.core.workspace.store import Workspace, discover_workspace


def stored_path(base: Path, path: Path) -> str:
    return os.path.relpath(path.expanduser().resolve(), base.resolve())


def resolve_path(base: Path, path: str) -> Path:
    return (base / path).resolve()


def input_state(paths: dict[str, Path | None]) -> str:
    files: list[tuple[str, str, int | None, int | None]] = []
    for role, source in paths.items():
        if source is None:
            continue
        source = source.expanduser().resolve()
        candidates = sorted(source.glob("*.pdf")) if source.is_dir() else [source]
        for path in candidates:
            relative = path.relative_to(source).as_posix() if source.is_dir() else ""
            try:
                stat = path.stat()
                files.append((role, relative, stat.st_size, stat.st_mtime_ns))
            except FileNotFoundError:
                files.append((role, relative, None, None))
    return json.dumps(files, separators=(",", ":"))


def assignment_for_directory(
    workspace: Workspace,
    directory: Path,
    *,
    assignment_id: str | None = None,
    include_pending: bool = False,
) -> str | None:
    assignments = {item.id for item in workspace.assignments()}
    inputs = workspace.assignment_inputs()
    known = assignments | {item.id for item in inputs} if include_pending else assignments
    if assignment_id is not None:
        if assignment_id not in known:
            raise ValueError("Assignment not found in this workspace.")
        return assignment_id

    directory = directory.expanduser().resolve()
    matches = [
        item
        for item in inputs
        if item.id in known
        and (
            (folder := resolve_path(workspace.root, item.folder)) == directory
            or folder in directory.parents
        )
    ]
    if matches:
        return max(
            matches,
            key=lambda item: len(resolve_path(workspace.root, item.folder).parts),
        ).id
    if len(known) == 1 and directory == workspace.root:
        return next(iter(known))
    if len(known) > 1:
        raise ValueError("This workspace has several assignments. Pass --assignment.")
    return None


def inputs_for_assignment(
    workspace: Workspace, assignment_id: str
) -> AssignmentInputs | None:
    return next(
        (item for item in workspace.assignment_inputs() if item.id == assignment_id),
        None,
    )


def current_assignment(
    *, workspace_root: Path | None = None, assignment_id: str | None = None
) -> tuple[Workspace, str]:
    root = workspace_root or discover_workspace(Path.cwd())
    if root is None:
        raise ValueError("No workspace found. Pass --workspace or run inside one.")
    workspace = Workspace(root)
    selected = assignment_for_directory(
        workspace, Path.cwd(), assignment_id=assignment_id
    )
    if selected is None:
        raise ValueError("This workspace has no gradeable assignment yet.")
    return workspace, selected


def submissions_path(workspace: Workspace, assignment_id: str) -> Path:
    saved = inputs_for_assignment(workspace, assignment_id)
    if saved is None:
        return workspace.root / ".opengrader" / "assignments" / assignment_id
    folder = resolve_path(workspace.root, saved.folder)
    if saved.submissions is not None:
        return resolve_path(folder, saved.submissions)
    return resolve_path(folder, saved.output) / "submissions"
