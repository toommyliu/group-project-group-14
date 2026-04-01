import asyncio
from unittest.mock import patch

from textual.widgets import ContentSwitcher

from local_grader.tui.tui import TuiApp
from local_grader.tui.controllers.input_controller import (
    InputController,
    InputProfileName,
)
from local_grader.tui.main import run_app

from .fakes import DEFAULT_IMAGE_PATH, FakeSampleDataService


def test_browse_screen_navigation_helpers_and_auto_advance() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_service=FakeSampleDataService(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            browse_screen = app.get_browse_screen()

            assert browse_screen.focused_column_id() == "courses"
            assert browse_screen.highlighted_item("courses").item_id == "c1"

            await pilot.press("enter")
            await pilot.pause()

            assert browse_screen.focused_column_id() == "assignments"
            assert browse_screen.highlighted_item("assignments").item_id == "a1"

            await pilot.press("left")
            await pilot.pause()
            assert browse_screen.focused_column_id() == "courses"

            await pilot.press("right")
            await pilot.pause()
            assert browse_screen.focused_column_id() == "assignments"

    asyncio.run(run())


def test_happy_path_opens_student_and_finalize_loads_next_student() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_service=FakeSampleDataService(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
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


def test_vim_profile_binds_j_and_enter_for_browse_flow() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_service=FakeSampleDataService(),
            input_profile=InputProfileName.VIM,
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.get_browse_screen().focused_column_id() == "assignments"

            await pilot.press("j")
            await pilot.pause()
            assert (
                app.get_browse_screen().highlighted_item("assignments").item_id == "a2"
            )

    asyncio.run(run())


def test_runtime_profile_switch_updates_live_bindings() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_service=FakeSampleDataService(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            app.set_input_profile(InputProfileName.VIM)
            await pilot.pause()

            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("j")
            await pilot.pause()

            assert (
                app.get_browse_screen().highlighted_item("assignments").item_id == "a2"
            )

    asyncio.run(run())


def test_emacs_profile_supports_ctrl_p_plus_standard_keys() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_service=FakeSampleDataService(),
            input_profile=InputProfileName.EMACS,
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.get_browse_screen().focused_column_id() == "assignments"

            await pilot.press("down")
            await pilot.pause()
            assert (
                app.get_browse_screen().highlighted_item("assignments").item_id == "a2"
            )

            await pilot.press("ctrl+p")
            await pilot.pause()
            assert (
                app.get_browse_screen().highlighted_item("assignments").item_id == "a1"
            )

    asyncio.run(run())


def test_run_app_uses_runtime_default_profile() -> None:
    expected_profile = InputController.profile().name
    with patch("local_grader.tui.main.TuiApp.run", autospec=True) as run_mock:
        run_app()

    app_self = run_mock.call_args.args[0]
    assert isinstance(app_self, TuiApp)
    assert app_self.input_controller.profile_name() == expected_profile
