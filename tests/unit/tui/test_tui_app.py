import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.widgets import ContentSwitcher, ListView, Static

from opengrader.tui.controllers.input_controller import (
    InputController,
    InputProfileName,
)
from opengrader.core import get_database_path
from opengrader.tui.main import run_app
from opengrader.tui.tui import TuiApp
from opengrader.tui.data import AssignmentSummary, StudentSummary
from opengrader.tui.ui.widgets import AssignmentPane, IndexedListView, SelectionDetails

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


def test_course_selection_populates_tabs() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
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


def test_course_navigation_updates_content_without_enter() -> None:
    class VariableCountsProvider(FakeDataProvider):
        def list_assignments(self, course_id: int | None) -> list[AssignmentSummary]:
            if course_id == 1:
                return [
                    AssignmentSummary(1, "HW 1", ""),
                    AssignmentSummary(2, "HW 2", ""),
                ]
            if course_id == 2:
                return [AssignmentSummary(3, "Quiz 1", "")]
            return []

        def list_students(self, course_id: int | None) -> list[StudentSummary]:
            if course_id == 1:
                return [
                    StudentSummary(1, "Tommy Liu"),
                    StudentSummary(2, "Andy Cortes"),
                ]
            if course_id == 2:
                return [StudentSummary(3, "Ada Lovelace")]
            return []

    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=VariableCountsProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            assert app.workflow_state.selection.course_id == 1

            assignment_list = app.get_home_screen().query_one(
                "#home-assignment-list", ListView
            )
            student_list = app.get_home_screen().query_one(
                "#home-student-list", ListView
            )
            assert len(assignment_list.children) == 2
            assert len(student_list.children) == 2

            await pilot.press("down")
            await pilot.pause()

            assert app.workflow_state.selection.course_id == 2
            assert len(assignment_list.children) == 1
            assert len(student_list.children) == 1

    asyncio.run(run())


def test_sidebar_subtitles_show_current_index_out_of_total() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            course_list = app.get_home_screen().query_one(
                "#home-course-list", IndexedListView
            )
            assert course_list.border_subtitle == "1 of 2"

            await pilot.press("enter")
            await pilot.pause()

            assignment_list = app.get_home_screen().query_one(
                "#home-assignment-list", IndexedListView
            )
            student_list = app.get_home_screen().query_one(
                "#home-student-list", IndexedListView
            )
            assert assignment_list.border_subtitle == "0 of 2"
            assert student_list.border_subtitle == "0 of 2"

            await pilot.press("right")
            await pilot.pause()
            assert assignment_list.border_subtitle == "1 of 2"

            await pilot.press("down")
            await pilot.pause()
            assert assignment_list.border_subtitle == "2 of 2"

    asyncio.run(run())


def test_clicking_pane_title_selects_first_item_when_none_highlighted() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assignment_list = app.get_home_screen().query_one(
                "#home-assignment-list", ListView
            )
            assignment_pane = app.get_home_screen().query_one(
                "#home-assignment-pane", AssignmentPane
            )

            assert assignment_list.highlighted_child is None

            assignment_pane.action_pane_activate_tab(0)
            await pilot.pause()

            assert assignment_list.has_focus
            assert assignment_list.index == 0
            assert assignment_list.highlighted_child is not None

    asyncio.run(run())


def test_refocusing_pane_restores_previous_highlighted_index() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            course_list = app.get_home_screen().query_one(
                "#home-course-list", ListView
            )
            assignment_list = app.get_home_screen().query_one(
                "#home-assignment-list", ListView
            )

            await pilot.press("down")
            await pilot.pause()
            assert course_list.index == 1

            await pilot.press("enter")
            await pilot.pause()

            await pilot.press("right")
            await pilot.pause()
            assert assignment_list.has_focus
            assert assignment_list.index == 0

            await pilot.press("down")
            await pilot.pause()
            assert assignment_list.index == 1

            await pilot.press("left")
            await pilot.pause()
            assert course_list.has_focus
            assert course_list.index == 1

            await pilot.press("right")
            await pilot.pause()
            assert assignment_list.has_focus
            assert assignment_list.index == 1

    asyncio.run(run())


def test_switching_panes_updates_status_immediately() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            status = app.get_home_screen().query_one("#home-status", Static)
            assert "Course:" in str(status.content)

            await pilot.press("right")
            await pilot.pause()
            assert "HW 1" in str(status.content)

            await pilot.press("right")
            await pilot.pause()
            assert "Student: Tommy Liu" in str(status.content)

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


def test_tab_navigation_reaches_details_pane() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            course_list = app.get_home_screen().query_one("#home-course-list", ListView)
            assignment_list = app.get_home_screen().query_one(
                "#home-assignment-list", ListView
            )
            student_list = app.get_home_screen().query_one(
                "#home-student-list", ListView
            )
            details = app.get_home_screen().query_one(
                "#home-selection-details", SelectionDetails
            )

            course_list.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            await pilot.press("tab")
            await pilot.pause()
            assert assignment_list.has_focus

            await pilot.press("tab")
            await pilot.pause()
            assert student_list.has_focus

            await pilot.press("tab")
            await pilot.pause()
            assert details.has_focus

    asyncio.run(run())


def test_switching_course_updates_sidebar_totals() -> None:
    class VariableCountsProvider(FakeDataProvider):
        def list_assignments(self, course_id: int | None) -> list[AssignmentSummary]:
            if course_id == 1:
                return [
                    AssignmentSummary(1, "HW 1", ""),
                    AssignmentSummary(2, "HW 2", ""),
                ]
            if course_id == 2:
                return [AssignmentSummary(3, "Quiz 1", "")]
            return []

        def list_students(self, course_id: int | None) -> list[StudentSummary]:
            if course_id == 1:
                return [
                    StudentSummary(1, "Tommy Liu"),
                    StudentSummary(2, "Andy Cortes"),
                ]
            if course_id == 2:
                return [StudentSummary(3, "Ada Lovelace")]
            return []

    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=VariableCountsProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()

            assignment_list = app.get_home_screen().query_one(
                "#home-assignment-list", IndexedListView
            )
            assert assignment_list.border_subtitle.endswith("of 2")

            await pilot.press("1")
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()

            assert assignment_list.border_subtitle.endswith("of 1")

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
