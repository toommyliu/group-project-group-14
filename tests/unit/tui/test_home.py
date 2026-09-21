"""Home: the first screen of `tui` (tui/home.py), driven headless."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from textual.widgets import Button, Input, ListView, Static

from opengrader.core.recents import load_recents, record_recent
from opengrader.tui.home import Home, HomeChoice

REPO = Path(__file__).resolve().parents[3]
_SIZE = (120, 40)


@pytest.fixture
def quiz(tmp_path: Path) -> Path:
    target = tmp_path / "quiz"
    shutil.copytree(REPO / "samples" / "cs146-quiz1", target)
    return target


def _run(app: Home, script) -> object:  # noqa: ANN001
    async def run() -> object:
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await script(app, pilot)
            await pilot.pause()
        return app.return_value

    return asyncio.run(run())


def test_a_first_run_shows_no_recents_and_focuses_the_path_box() -> None:
    async def script(app, pilot):
        assert app.query_one("#home-recents").display is False
        assert app.query_one("#home-no-recents").display is True
        assert app.focused is app.query_one("#home-path", Input)
        assert app.query_one("#home-open", Button).disabled
        app.exit(None)

    assert _run(Home(), script) is None


def test_typing_a_class_folder_detects_it_and_open_grades_it(quiz: Path) -> None:
    async def script(app, pilot):
        app.query_one("#home-path", Input).value = str(quiz)
        await pilot.pause()
        assert "Ready to grade" in str(app.query_one("#home-detect", Static).render())
        app.query_one("#home-open", Button).press()

    choice = _run(Home(), script)

    assert choice == HomeChoice(
        "folder", folder=quiz, qa_file=quiz / "qa.md", submissions=quiz / "student-submissions"
    )


def test_an_ambiguous_answer_key_reveals_the_override(quiz: Path) -> None:
    (quiz / "qa.md").rename(quiz / "quiz-a.md")  # no preferred name: a tie
    shutil.copy(quiz / "quiz-a.md", quiz / "qa-copy.md")

    async def script(app, pilot):
        app.query_one("#home-path", Input).value = str(quiz)
        await pilot.pause()
        assert app.query_one("#home-qa-row").display is True
        assert app.query_one("#home-open", Button).disabled
        app.query_one("#home-qa", Input).value = str(quiz / "qa-copy.md")
        await pilot.pause()
        assert not app.query_one("#home-open", Button).disabled
        await pilot.press("enter")  # Enter in a box opens too

    choice = _run(Home(start_path=""), script)

    assert choice.qa_file == quiz / "qa-copy.md"


def test_open_keeps_one_green_label_and_says_what_it_will_do(tmp_path: Path, quiz: Path) -> None:
    scans = tmp_path / "new"
    scans.mkdir()
    (scans / "scans.pdf").write_bytes(b"%PDF-1.4\n")
    workspace = tmp_path / "ws"
    (workspace / ".opengrader").mkdir(parents=True)
    (workspace / ".opengrader" / "workspace.sqlite3").write_bytes(b"")

    async def open_(app, pilot, path, says, workspace_button_enabled):
        app.query_one("#home-path", Input).value = str(path)
        await pilot.pause()
        opener = app.query_one("#home-open", Button)
        assert str(opener.label) == "Open" and opener.variant == "success"
        assert says in str(app.query_one("#home-actions-hint", Static).render())
        assert app.query_one("#home-workspace", Button).disabled is not workspace_button_enabled
        opener.press()

    assert _run(
        Home(), lambda a, p: open_(a, p, scans, "to Tools", False)
    ) == HomeChoice("tools", pipeline_folder=scans)
    assert _run(
        Home(), lambda a, p: open_(a, p, workspace, "assignment list", False)
    ) == HomeChoice("workspace", folder=workspace)
    assert _run(
        Home(), lambda a, p: open_(a, p, quiz, "grades these files right here", True)
    ).kind == "folder"


def test_open_as_workspace_is_a_separate_explicit_choice(quiz: Path) -> None:
    async def script(app, pilot):
        app.query_one("#home-path", Input).value = str(quiz)
        await pilot.pause()
        app.query_one("#home-workspace", Button).press()

    choice = _run(Home(), script)

    assert choice == HomeChoice(
        "new_workspace", folder=quiz, qa_file=quiz / "qa.md",
        submissions=quiz / "student-submissions",
    )


def test_nothing_typed_disables_both_buttons() -> None:
    async def script(app, pilot):
        assert app.query_one("#home-open", Button).disabled
        assert app.query_one("#home-workspace", Button).disabled
        app.exit(None)

    _run(Home(), script)


def test_a_recent_folder_opens_with_its_saved_answer_key(quiz: Path) -> None:
    record_recent(
        "folder", quiz, title="CS146: Quiz 1", qa_file=quiz / "qa.md",
        submissions=quiz / "student-submissions",
    )

    async def script(app, pilot):
        listing = app.query_one("#home-recents", ListView)
        assert listing.display is True and app.focused is listing
        await pilot.press("enter")

    choice = _run(Home(), script)

    assert choice.kind == "folder"
    assert choice.qa_file == (quiz / "qa.md").resolve()
    assert choice.submissions == (quiz / "student-submissions").resolve()


def test_a_missing_recent_is_explained_and_can_be_forgotten(tmp_path: Path) -> None:
    record_recent("folder", tmp_path / "gone", title="Old class")

    async def script(app, pilot):
        await pilot.press("enter")
        await pilot.pause()
        assert "no longer there" in str(app.query_one("#home-message", Static).render())
        await pilot.press("x")
        await pilot.pause()
        assert app.query_one("#home-no-recents").display is True
        app.exit(None)

    assert _run(Home(), script) is None
    assert load_recents() == []


def test_tools_and_a_launcher_message(quiz: Path) -> None:
    async def script(app, pilot):
        message = app.query_one("#home-message", Static)
        assert message.display and "service could not start" in str(message.render())
        await pilot.press("f3")

    choice = _run(Home(message="Could not open the workspace: service could not start"), script)

    assert choice == HomeChoice("tools")
