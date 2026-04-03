import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.widgets import ContentSwitcher, ListView, TabbedContent

from opengrader.tui.controllers.input_controller import (
    InputController,
    InputProfileName,
)
from opengrader.core import get_database_path
from opengrader.tui.main import run_app
from opengrader.tui.tui import TuiApp

from .fakes import DEFAULT_IMAGE_PATH, FakeDataProvider


def test_home_course_list_populates_on_mount() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            course_list = app.get_home_screen().query_one("#home-course-list", ListView)
            assert len(course_list.children) == 2

    asyncio.run(run())


def test_tabs_hidden_until_course_selected() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            course_list = app.get_home_screen().query_one("#home-course-list", ListView)
            course_list.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            tabs = app.get_home_screen().query_one("#home-content-tabs", TabbedContent)
            assert tabs.display is True

    asyncio.run(run())


def test_course_selection_populates_tabs() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            course_list = app.get_home_screen().query_one("#home-course-list", ListView)
            course_list.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assignment_list = app.get_home_screen().query_one(
                "#home-assignment-list", ListView
            )
            student_list = app.get_home_screen().query_one(
                "#home-student-list", ListView
            )
            assert len(assignment_list.children) == 2
            assert len(student_list.children) == 2

    asyncio.run(run())


def test_happy_path_opens_grading_via_assignment_tab() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            course_list = app.get_home_screen().query_one("#home-course-list", ListView)
            course_list.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("right")
            await pilot.pause()

            assignment_list = app.get_home_screen().query_one(
                "#home-assignment-list", ListView
            )
            assert len(assignment_list.children) == 2
            assert assignment_list.highlighted_child is not None

            await pilot.press("g")
            await pilot.pause()

            switcher = app.query_one(ContentSwitcher)
            assert switcher.current == "page-grade"
            assert app.workflow_state.selection_labels["student"] == "Tommy Liu"

    asyncio.run(run())


def test_finalize_loads_next_student() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            course_list = app.get_home_screen().query_one("#home-course-list", ListView)
            course_list.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("right")
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


def test_vim_profile_navigates_with_j_k_h_l() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
            input_profile=InputProfileName.VIM,
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            course_list = app.get_home_screen().query_one("#home-course-list", ListView)
            course_list.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("l")
            await pilot.pause()

            assignment_list = app.get_home_screen().query_one(
                "#home-assignment-list", ListView
            )
            assert assignment_list.has_focus

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

            course_list = app.get_home_screen().query_one("#home-course-list", ListView)
            course_list.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("l")
            await pilot.pause()

            assignment_list = app.get_home_screen().query_one(
                "#home-assignment-list", ListView
            )
            assert assignment_list.has_focus

    asyncio.run(run())


def test_emacs_profile_navigates_with_ctrl_keys() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
            input_profile=InputProfileName.EMACS,
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            course_list = app.get_home_screen().query_one("#home-course-list", ListView)
            course_list.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("ctrl+f")
            await pilot.pause()

            assignment_list = app.get_home_screen().query_one(
                "#home-assignment-list", ListView
            )
            assert assignment_list.has_focus

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
