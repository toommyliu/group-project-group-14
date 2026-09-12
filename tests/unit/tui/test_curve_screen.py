import asyncio

from textual.app import App
from textual.widgets import Input, RadioButton, Select

from opengrader.core.curve import Curve
from opengrader.tui.ui.screens.curve_screen import CurveScreen

_DIST = [(50.0, 100.0), (60.0, 100.0), (70.0, 100.0), (40.0, 100.0), (80.0, 100.0)]


class _CurveApp(App):
    def __init__(self, distribution) -> None:
        super().__init__()
        self._distribution = distribution

    def on_mount(self) -> None:
        self.push_screen(CurveScreen(Curve(), self._distribution))


def _select_method(screen, index: int) -> None:
    list(screen.query(RadioButton))[index].value = True


def test_solve_scale_sets_value_input() -> None:
    async def run() -> None:
        app = _CurveApp(_DIST)
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            screen = app.screen
            _select_method(screen, 2)
            screen.query_one("#curve-target-value", Input).value = "75"
            await pilot.pause()

            await pilot.click("#curve-solve")
            await pilot.pause()

            assert screen.query_one("#curve-value", Input).value == "1.25"

    asyncio.run(run())


def test_solve_bonus_sets_value_input() -> None:
    async def run() -> None:
        app = _CurveApp(_DIST)
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            screen = app.screen
            _select_method(screen, 1)
            screen.query_one("#curve-target-value", Input).value = "75"
            screen.query_one("#curve-target-stat", Select).value = "mean"
            await pilot.pause()

            await pilot.click("#curve-solve")
            await pilot.pause()

            assert screen.query_one("#curve-value", Input).value == "15"

    asyncio.run(run())


def test_solve_with_no_method_notifies_and_leaves_value() -> None:
    async def run() -> None:
        app = _CurveApp(_DIST)
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#curve-target-value", Input).value = "75"
            await pilot.pause()
            before = screen.query_one("#curve-value", Input).value

            await pilot.click("#curve-solve")
            await pilot.pause()

            assert screen.query_one("#curve-value", Input).value == before

    asyncio.run(run())


def test_solve_unreachable_target_leaves_value_unchanged() -> None:
    async def run() -> None:
        app = _CurveApp([(0.0, 100.0), (50.0, 100.0)])
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            screen = app.screen
            _select_method(screen, 2)
            screen.query_one("#curve-value", Input).value = "1"
            screen.query_one("#curve-target-value", Input).value = "100"
            await pilot.pause()

            await pilot.click("#curve-solve")
            await pilot.pause()

            assert screen.query_one("#curve-value", Input).value == "1"

    asyncio.run(run())
