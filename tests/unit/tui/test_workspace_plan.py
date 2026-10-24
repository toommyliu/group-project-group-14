"""`make_workspace.plan_workspace`: what the Workspace card says it would do
with a folder, read from files alone."""

from __future__ import annotations

from pathlib import Path

from opengrader.tui.make_workspace import (
    MakeWorkspaceRequest,
    _assignment_folder,
    _end_browser_session,
    plan_workspace,
)

from .test_launch_prepare import _QA_TEXT, _add_submission


def _assignment(folder: Path, title: str = "Quiz", *, read: bool = True) -> Path:
    folder.mkdir(parents=True)
    (folder / "qa.md").write_text(_QA_TEXT.replace("title: Quiz", f"title: {title}"), encoding="utf-8")
    (folder / "scans.pdf").write_bytes(b"%PDF-1.4\n")
    if read:
        _add_submission(folder)
    return folder


def test_a_read_class_folder_becomes_its_own_assignment(tmp_path) -> None:  # noqa: ANN001
    folder = _assignment(tmp_path / "Quiz 1")

    plan = plan_workspace(folder)

    assert plan.action == "make"
    assert plan.request == MakeWorkspaceRequest(
        root=folder.resolve(),
        assignments=((folder / "qa.md", folder / "submissions"),),
    )
    assert "One assignment: Quiz 1, with 1 student paper(s)" in plan.lines[0]
    # The single-folder request is one `make_workspace` can import.
    assert _assignment_folder(folder, folder / "qa.md", folder / "submissions") == folder


def test_a_class_folder_whose_scans_are_not_read_is_refused(tmp_path) -> None:  # noqa: ANN001
    plan = plan_workspace(_assignment(tmp_path / "Quiz 1", read=False))

    assert plan.action == "none"
    assert "Prepare it first" in plan.lines[0]


def test_a_course_folder_takes_its_ready_assignments_and_names_the_rest(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "Fall 2026"
    quiz = _assignment(root / "Quiz 1", "Quiz 1")
    midterm = _assignment(root / "Midterm", "Midterm")
    _assignment(root / "Final", "Final", read=False)

    plan = plan_workspace(root)

    assert plan.action == "make" and plan.request is not None
    assert plan.request.root == root.resolve()
    assert sorted(plan.request.assignments) == sorted(
        [(quiz / "qa.md", quiz / "submissions"), (midterm / "qa.md", midterm / "submissions")]
    )
    assert plan.lines[0].startswith("2 assignments:")
    assert "Left out for now" in plan.lines[1] and "Final" in plan.lines[1]


def test_a_course_folder_with_one_assignment_points_at_it(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "Fall 2026"
    _assignment(root / "Quiz 1")

    plan = plan_workspace(root)

    assert plan.action == "none"
    assert "Choose that folder instead" in plan.lines[0]


def test_sections_of_one_assignment_are_refused(tmp_path) -> None:  # noqa: ANN001
    root = tmp_path / "Fall 2026"
    _assignment(root / "Sec 1")
    _assignment(root / "Sec 2")

    plan = plan_workspace(root)

    assert plan.action == "none" and "sections of one assignment" in plan.lines[0]


def test_a_workspace_or_a_folder_inside_one_is_offered_to_open(tmp_path) -> None:  # noqa: ANN001
    from opengrader.core.workspace.store import Workspace

    root = tmp_path / "Fall 2026"
    quiz = _assignment(root / "Quiz 1")
    Workspace.create(root)

    assert plan_workspace(root).action == "open"
    inside = plan_workspace(quiz)
    assert inside.action == "open" and inside.root == root.resolve()


def test_a_subfolder_with_its_own_browser_copy_is_refused(tmp_path) -> None:  # noqa: ANN001
    from opengrader.core.workspace.folder_session import start_folder_session
    from opengrader.core.workspace.store import Workspace

    root = tmp_path / "Fall 2026"
    quiz = _assignment(root / "Quiz 1", "Quiz 1")
    _assignment(root / "Midterm", "Midterm")
    Workspace.create(quiz)
    start_folder_session(quiz, "a")

    plan = plan_workspace(root)

    assert plan.action == "none"
    assert "Quiz 1/ was opened in the browser app on its own" in plan.lines[0]


def test_a_class_folder_open_in_the_browser_says_so_and_ends_that_session(tmp_path) -> None:  # noqa: ANN001
    from opengrader.core.workspace.folder_session import load_folder_session, start_folder_session
    from opengrader.core.workspace.store import Workspace

    folder = _assignment(tmp_path / "Quiz 1")
    Workspace.create(folder)
    start_folder_session(folder, "a")

    plan = plan_workspace(folder)
    said: list[str] = []
    _end_browser_session(folder, said.append)

    assert plan.action == "make"
    assert any("open in the browser app" in line for line in plan.lines)
    assert load_folder_session(folder) is None and said


def test_nothing_to_make_a_workspace_from(tmp_path) -> None:  # noqa: ANN001
    empty = tmp_path / "empty"
    empty.mkdir()

    assert plan_workspace(empty).action == "none"
    assert plan_workspace(tmp_path / "missing").lines == ("Choose a folder that exists.",)
    assert plan_workspace("").action == "none"
