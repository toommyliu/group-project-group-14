import asyncio

from textual.widgets import ContentSwitcher, ListView, Static

from opengrader.tui.controllers.input_controller import InputProfileName
from opengrader.tui.data.stub_data_provider import StubDataProvider
from opengrader.tui.tui import TuiApp

from .fakes import DEFAULT_IMAGE_PATH, FakeDataProvider


def test_app_launches_directly_to_qa_picker() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            switcher = app.query_one(ContentSwitcher)
            assert switcher.current == "page-qa-picker"
            assert app.workflow_state.selection.course_id == 1
            assert app.workflow_state.selection.assignment_id == 1
            assert app.workflow_state.selection_labels["course"] == "CMPE 195"
            assert app.workflow_state.selection_labels["assignment"] == "HW 1"
            assert [item.item_id for item in app.workflow_state.question_items] == [
                "Q1",
                "Q2",
            ]

    asyncio.run(run())


def test_happy_path_selects_qa_then_opens_grading() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            switcher = app.query_one(ContentSwitcher)
            assert switcher.current == "page-grade"
            assert app.workflow_state.selection_labels["student"] == "Tommy Liu"
            assert app.workflow_state.selection_labels["question"] == "Q1"

            rubric_list = app.get_grade_screen().query_one("#rubric-list", ListView)
            assert len(rubric_list.children) == 2

    asyncio.run(run())


def test_escape_from_grade_returns_to_qa_picker() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            switcher = app.query_one(ContentSwitcher)
            assert switcher.current == "page-grade"

            await pilot.press("escape")
            await pilot.pause()

            assert switcher.current == "page-qa-picker"

    asyncio.run(run())


def test_finalize_loads_next_student() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert app.workflow_state.selection_labels["student"] == "Tommy Liu"

            await pilot.press("ctrl+g")
            await pilot.pause()

            assert app.workflow_state.selection_labels["student"] == "Andy Cortes"
            assert app.workflow_state.active_student_index == 1

    asyncio.run(run())


def test_escape_on_qa_picker_shows_status_message() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

            status = app.get_qa_picker_screen().query_one("#qa-status", Static)
            assert "Already viewing the grading queue." in str(status.content)

    asyncio.run(run())


def test_default_app_uses_stub_data_provider() -> None:
    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)

        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.data_provider, StubDataProvider)

    asyncio.run(run())


def test_runtime_profile_switch_updates_controller() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            app.set_input_profile(InputProfileName.VIM)
            assert app.input_controller.profile_name() == InputProfileName.VIM

    asyncio.run(run())
