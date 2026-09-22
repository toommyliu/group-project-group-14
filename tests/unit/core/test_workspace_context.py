from pathlib import Path

import pytest

from opengrader.core.workspace.context import (
    assignment_for_directory,
    resolve_path,
    stored_path,
)
from opengrader.core.workspace.models import AssignmentInputs
from opengrader.core.workspace.store import Workspace, discover_workspace


def _bind(workspace: Workspace, folder: Path) -> str:
    assignment_id = workspace.assignment_id_for_folder(folder)
    workspace.save_assignment_inputs(
        AssignmentInputs(
            id=assignment_id,
            folder=stored_path(workspace.root, folder),
            scan="scans.pdf",
            output=".",
        )
    )
    return assignment_id


def test_course_workspace_selects_the_assignment_by_directory(tmp_path: Path) -> None:
    course = tmp_path / "course"
    midterm = course / "midterm"
    final = course / "final"
    scans = midterm / "incoming"
    scans.mkdir(parents=True)
    final.mkdir()
    workspace = Workspace.create(course)
    midterm_id = _bind(workspace, midterm)
    final_id = _bind(workspace, final)

    assert discover_workspace(scans) == course
    assert assignment_for_directory(workspace, scans, include_pending=True) == midterm_id
    assert assignment_for_directory(workspace, final, include_pending=True) == final_id
    assert assignment_for_directory(workspace, final) is None
    with pytest.raises(ValueError, match="several assignments"):
        assignment_for_directory(workspace, course, include_pending=True)
    assert (
        assignment_for_directory(
            workspace, course, assignment_id=final_id, include_pending=True
        ) == final_id
    )


def test_saved_paths_survive_moving_the_workspace(tmp_path: Path) -> None:
    root = tmp_path / "first"
    folder = root / "midterm"
    folder.mkdir(parents=True)
    workspace = Workspace.create(root)
    assignment_id = _bind(workspace, folder)
    moved = tmp_path / "moved"
    root.rename(moved)
    reopened = Workspace(moved)

    assert (
        assignment_for_directory(reopened, moved / "midterm", include_pending=True)
        == assignment_id
    )
    assert resolve_path(moved / "midterm", "scans.pdf") == moved / "midterm" / "scans.pdf"


def test_old_workspace_schema_is_not_modified(tmp_path: Path) -> None:
    import sqlite3

    database = tmp_path / ".opengrader" / "workspace.sqlite3"
    database.parent.mkdir()
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA user_version = 1")

    with pytest.raises(ValueError, match="compatible version"):
        Workspace(tmp_path)
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
