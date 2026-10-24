"""Classic's Workspace card (Helper Tools) and Launch Grading on a class that
became a workspace."""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Button, Input

from opengrader.tui.make_workspace import MakeWorkspaceRequest, OpenWorkspaceRequest
from opengrader.tui.ui.widgets.browser_launcher import BrowserLauncher
from opengrader.tui.ui.widgets.workspace_card import WorkspaceCard

from .test_launch_prepare import (
    _Session,
    _add_submission,
    _buttons,
    _class_folder,
    _dialog,
    _drive,
    _settle,
    _text,
)


def _ready_folder(tmp_path: Path) -> Path:
    folder = _class_folder(tmp_path)
    _add_submission(folder)
    return folder


async def _press_make(session: _Session, folder: Path) -> WorkspaceCard:
    card = session.app.query_one(WorkspaceCard)
    card.query_one("#workspace-folder", Input).value = str(folder)
    card.refresh_plan()
    card.query_one("#workspace-make", Button).press()
    await session.asked()
    return card


def test_the_card_explains_and_asks_before_making_anything(tmp_path) -> None:  # noqa: ANN001
    folder = _ready_folder(tmp_path)

    async def steps(session: _Session):  # noqa: ANN202
        await _press_make(session, folder)
        found = _text(session.app, "#prepare-folder-found")
        note = _text(session.app, "#prepare-folder-note")
        buttons = _buttons(session.app)
        await session.choose("cancel")
        return found, note, buttons

    found, note, buttons = _drive(steps)

    assert "One assignment" in found
    assert "not moved or deleted" in note and "Write grades back" in note
    assert buttons == ["cancel", "terminal", "browser"]
    # Cancel made nothing.
    assert not (folder / ".opengrader" / "workspace.sqlite3").exists()


def test_open_in_browser_makes_it_through_the_launcher_and_keeps_classic(
    tmp_path, monkeypatch  # noqa: ANN001
) -> None:
    folder = _ready_folder(tmp_path)
    calls: list = []

    def fake_open(self, root, request=None, *, on_progress=None, on_done=None):  # noqa: ANN001, ANN202
        calls.append((root, request))
        on_progress("Making it a workspace…")
        on_done(root, None)

    monkeypatch.setattr(BrowserLauncher, "open_workspace", fake_open)

    async def steps(session: _Session):  # noqa: ANN202
        card = await _press_make(session, folder)
        await session.choose("browser")
        await _settle(session.pilot, lambda: bool(calls))
        await session.pilot.pause()
        return str(card.query_one("#workspace-progress").render()), session.app.return_value

    progress, result = _drive(steps)

    root, request = calls[0]
    assert root == folder.resolve()
    assert request == MakeWorkspaceRequest(
        root=folder.resolve(), assignments=((folder / "qa.md", folder / "submissions"),)
    )
    assert progress.startswith("✓") and "is a workspace" in progress
    assert result is None  # classic is still open


def test_this_window_closes_classic_with_the_request(tmp_path) -> None:  # noqa: ANN001
    folder = _ready_folder(tmp_path)

    async def steps(session: _Session):  # noqa: ANN202
        await _press_make(session, folder)
        await session.choose("terminal")
        await _settle(session.pilot, lambda: session.app.return_value is not None)
        return session.app.return_value

    result = _drive(steps)

    assert isinstance(result, MakeWorkspaceRequest) and result.root == folder.resolve()


def test_the_card_follows_the_start_grading_folder_until_one_is_typed(tmp_path) -> None:  # noqa: ANN001
    first = _ready_folder(tmp_path)
    second = _class_folder(tmp_path, "quiz2")

    async def steps(session: _Session):  # noqa: ANN202
        card = session.app.query_one(WorkspaceCard)
        box = card.query_one("#workspace-folder", Input)
        session.app.query_one("#tui-class-folder", Input).value = str(first)
        session.screen._scan_launch_folder()
        filled = box.value
        label = str(card.query_one("#workspace-make", Button).label)
        box.value = str(tmp_path)  # typed by the teacher
        session.app.query_one("#tui-class-folder", Input).value = str(second)
        session.screen._scan_launch_folder()
        return filled, label, box.value

    filled, label, kept = _drive(steps)

    assert filled == str(first)
    assert label == "Make workspace…"
    assert kept == str(tmp_path)


def test_launch_grading_on_a_class_that_became_a_workspace_asks_first(
    tmp_path, monkeypatch  # noqa: ANN001
) -> None:
    import opengrader.core.workspace.folder_session as folder_session

    folder = _ready_folder(tmp_path)
    monkeypatch.setattr(folder_session, "shared_workspace_for", lambda _subs: folder)

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        buttons = _buttons(session.app)
        await session.choose("terminal")
        await _settle(session.pilot, lambda: session.app.return_value is not None)
        return buttons, session.launched, session.app.return_value

    buttons, launched, result = _drive(steps)

    assert buttons == ["cancel", "files", "terminal", "browser"]
    assert launched == []  # the files were not opened
    assert result == OpenWorkspaceRequest(root=folder)


def test_grade_the_files_anyway_still_opens_them(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    import opengrader.core.workspace.folder_session as folder_session

    folder = _ready_folder(tmp_path)
    monkeypatch.setattr(folder_session, "shared_workspace_for", lambda _subs: folder)

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        await session.choose("files")
        return session.launched, _dialog(session.app)

    launched, dialog = _drive(steps)

    assert launched == [(str(folder / "qa.md"), str(folder / "submissions"))]
    assert dialog is None


_FILE_TOOLS = ("#section-policy", "#section-handoff", "#section-backup")


def test_file_tools_are_hidden_for_a_class_in_a_workspace_until_asked(tmp_path) -> None:  # noqa: ANN001
    from opengrader.core.workspace.store import Workspace

    workspace_class = _ready_folder(tmp_path)
    Workspace.create(workspace_class)
    plain_class = _class_folder(tmp_path, "quiz2")
    _add_submission(plain_class)

    def state(app):  # noqa: ANN001, ANN202
        hidden = [app.query_one(section).has_class("hidden") for section in _FILE_TOOLS]
        return hidden, app.query_one("#file-tools-hidden").has_class("hidden")

    async def steps(session: _Session):  # noqa: ANN202
        app = session.app
        before = state(app)
        app.query_one("#tui-class-folder", Input).value = str(workspace_class)
        session.screen._scan_launch_folder()
        in_workspace = state(app)
        note = str(app.query_one("#file-tools-hidden-text").render())
        app.query_one("#file-tools-show", Button).press()
        await session.pilot.pause()
        shown_anyway = state(app)
        app.query_one("#tui-class-folder", Input).value = str(plain_class)
        session.screen._scan_launch_folder()
        plain = state(app)
        return before, in_workspace, note, shown_anyway, plain

    before, in_workspace, note, shown_anyway, plain = _drive(steps)

    assert before == ([False, False, False], True)
    assert in_workspace == ([True, True, True], False)
    assert "quiz1" in note and "Write grades back" in note
    assert shown_anyway == ([False, False, False], True)
    assert plain == ([False, False, False], True)


def test_file_tools_hide_once_the_card_makes_the_workspace(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from opengrader.core.workspace.store import Workspace

    folder = _ready_folder(tmp_path)

    def fake_open(self, root, request=None, *, on_progress=None, on_done=None):  # noqa: ANN001, ANN202
        Workspace.create(root)
        on_done(root, None)

    monkeypatch.setattr(BrowserLauncher, "open_workspace", fake_open)

    async def steps(session: _Session):  # noqa: ANN202
        session.app.query_one("#tui-class-folder", Input).value = str(folder)
        session.screen._scan_launch_folder()
        before = session.app.query_one("#section-backup").has_class("hidden")
        await _press_make(session, folder)
        await session.choose("browser")
        await _settle(
            session.pilot, lambda: session.app.query_one("#section-backup").has_class("hidden")
        )
        return before

    assert _drive(steps) is False
