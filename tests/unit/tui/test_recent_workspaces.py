"""The recent-workspace list a bare `opengrader tui` offers outside a workspace."""

import asyncio
from pathlib import Path

from textual.widgets import ListView

from opengrader.core.recents import load_recents, record_recent
from opengrader.core.workspace.store import Workspace
from opengrader.tui.recent_workspaces import RecentWorkspaces


def _workspaces(tmp_path: Path) -> tuple[Path, Path]:
    """One real workspace and one whose folder has gone (recorded, then moved)."""
    live = tmp_path / "cs146"
    live.mkdir()
    Workspace.create(live)
    gone = tmp_path / "old-drive" / "cmpe30"
    record_recent("workspace", live, assignment="a1", assignment_title="Quiz 1")
    record_recent("workspace", gone)  # newest, but missing
    return live, gone


def test_it_starts_on_the_newest_workspace_that_still_exists(tmp_path: Path) -> None:
    live, _gone = _workspaces(tmp_path)

    async def run() -> Path | None:
        app = RecentWorkspaces(load_recents(), here=tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause()
            assert app.query_one(ListView).index == 1  # skips the missing one
            await pilot.press("enter")
        return app.return_value

    assert asyncio.run(run()) == live.resolve()


def test_a_missing_workspace_is_marked_and_cannot_be_opened(tmp_path: Path) -> None:
    _live, gone = _workspaces(tmp_path)

    async def run() -> tuple[bool, bool, str]:
        app = RecentWorkspaces(load_recents(), here=tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause()
            listing = app.query_one(ListView)
            listing.index = 0
            missing_row = listing.children[0]
            detail = str(missing_row.query(".recent-detail").first().render())
            await pilot.press("enter")
            await pilot.pause()
            return app.is_running, missing_row.has_class("recent-missing"), detail

    still_open, marked, detail = asyncio.run(run())
    assert still_open and marked
    assert detail.startswith("Missing") and gone.name in detail


def test_the_last_opened_assignment_is_named(tmp_path: Path) -> None:
    _workspaces(tmp_path)

    async def run() -> str:
        app = RecentWorkspaces(load_recents(), here=tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause()
            row = app.query_one(ListView).children[1]
            return str(row.query(".recent-detail").first().render())

    assert asyncio.run(run()).startswith("Last opened: Quiz 1")


def test_d_forgets_a_workspace_for_good_and_q_quits(tmp_path: Path) -> None:
    live, _gone = _workspaces(tmp_path)

    async def run() -> tuple[int, Path | None]:
        app = RecentWorkspaces(load_recents(), here=tmp_path)
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause()
            app.query_one(ListView).index = 0
            await pilot.press("d")
            await pilot.pause()
            rows = len(app.query_one(ListView).children)
            await pilot.press("q")
        return rows, app.return_value

    rows, chosen = asyncio.run(run())
    assert rows == 1 and chosen is None
    assert [Path(item.path) for item in load_recents()] == [live.resolve()]
