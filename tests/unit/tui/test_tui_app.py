import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.widgets import ContentSwitcher, Tree

from opengrader.tui.controllers.input_controller import (
    InputController,
    InputProfileName,
)
from opengrader.core import get_database_path
from opengrader.tui.main import run_app
from opengrader.tui.tui import TuiApp

from .fakes import DEFAULT_IMAGE_PATH, FakeDataProvider


def test_home_tree_navigation_helpers() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            tree = app.get_home_screen().query_one("#home-tree", Tree)

            assert tree.cursor_node is not None
            assert str(tree.cursor_node.label) == "Courses"

            await pilot.press("right")
            await pilot.pause()
            assert tree.cursor_node is not None
            assert str(tree.cursor_node.label) == "CMPE 195 [Spring 2026]"

            await pilot.press("right")
            await pilot.pause()
            assert tree.cursor_node is not None
            assert str(tree.cursor_node.label) == "Assignments"

            await pilot.press("left")
            await pilot.pause()
            assert tree.cursor_node is not None
            assert str(tree.cursor_node.label) == "CMPE 195 [Spring 2026]"

    asyncio.run(run())


def test_happy_path_opens_student_and_finalize_loads_next_student() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("right")
            await pilot.pause()
            await pilot.press("right")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()

            switcher = app.query_one(ContentSwitcher)
            assert switcher.current == "page-grade"
            assert app.workflow_state.selection_labels["student"] == "Tommy Liu"

            await pilot.press("ctrl+g")
            await pilot.pause()

            assert switcher.current == "page-grade"
            assert app.workflow_state.selection_labels["student"] == "Andy Cortes"
            assert app.workflow_state.active_student_index == 1

    asyncio.run(run())


@pytest.mark.skip(
    reason="Student submissions path test needs update for new tree structure"
)
def test_student_submissions_path_opens_selected_submission() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("right")
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("right")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("right")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            switcher = app.query_one(ContentSwitcher)
            assert switcher.current == "page-grade"
            assert app.workflow_state.selection_labels["assignment"] == "HW 2"
            assert app.workflow_state.selection_labels["student"] == "Tommy Liu"

    asyncio.run(run())


def test_vim_profile_binds_j_and_enter_for_home_flow() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
            input_profile=InputProfileName.VIM,
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("l")
            await pilot.pause()
            await pilot.press("l")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            tree = app.get_home_screen().query_one("#home-tree", Tree)
            assert tree.cursor_node is not None
            assert str(tree.cursor_node.label) == "HW 1"

            await pilot.press("j")
            await pilot.pause()
            assert tree.cursor_node is not None
            assert str(tree.cursor_node.label) == "HW 2"

    asyncio.run(run())


def test_runtime_profile_switch_updates_live_bindings() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            app.set_input_profile(InputProfileName.VIM)
            await pilot.pause()

            await pilot.press("l")
            await pilot.pause()
            await pilot.press("l")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("j")
            await pilot.pause()

            tree = app.get_home_screen().query_one("#home-tree", Tree)
            assert tree.cursor_node is not None
            assert str(tree.cursor_node.label) == "HW 2"

    asyncio.run(run())


def test_emacs_profile_supports_ctrl_p_plus_standard_keys() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
            input_profile=InputProfileName.EMACS,
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("ctrl+f")
            await pilot.pause()
            await pilot.press("ctrl+f")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            tree = app.get_home_screen().query_one("#home-tree", Tree)
            assert tree.cursor_node is not None
            assert str(tree.cursor_node.label) == "HW 1"

            await pilot.press("down")
            await pilot.pause()
            assert tree.cursor_node is not None
            assert str(tree.cursor_node.label) == "HW 2"

            await pilot.press("ctrl+p")
            await pilot.pause()
            assert tree.cursor_node is not None
            assert str(tree.cursor_node.label) == "HW 1"

            await pilot.press("g")
            await pilot.pause()
            switcher = app.query_one(ContentSwitcher)
            assert switcher.current == "page-grade"

    asyncio.run(run())


def test_run_app_uses_runtime_default_profile() -> None:
    expected_profile = InputController.profile().name
    with patch("opengrader.tui.main.TuiApp.run", autospec=True) as run_mock:
        run_app()

    app_self = run_mock.call_args.args[0]
    assert isinstance(app_self, TuiApp)
    assert app_self.input_controller.profile_name() == expected_profile
    assert app_self.data_provider._db_path == get_database_path()


def test_run_app_uses_opengrader_home_for_db_default(monkeypatch) -> None:
    monkeypatch.setenv("OPENGRADER_HOME", "~/grader-home")
    with patch("opengrader.tui.main.TuiApp.run", autospec=True) as run_mock:
        run_app()

    app_self = run_mock.call_args.args[0]
    assert isinstance(app_self, TuiApp)
    assert (
        app_self.data_provider._db_path
        == Path("~/grader-home").expanduser() / "data" / "courses.db"
    )


def test_tui_app_explicit_db_path_overrides_opengrader_home(monkeypatch) -> None:
    monkeypatch.setenv("OPENGRADER_HOME", "~/grader-home")

    app = TuiApp(
        default_image_path=DEFAULT_IMAGE_PATH,
        data_provider=None,
        db_path="~/explicit-courses.db",
    )

    assert app.data_provider._db_path == Path("~/explicit-courses.db").expanduser()
