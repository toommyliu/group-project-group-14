import asyncio

from textual.app import App

from opengrader.tui.data.models import RegradeReportItem
from opengrader.tui.ui.screens import RegradeChoice, RegradeQueueScreen


def _item(**kwargs) -> RegradeReportItem:
    base = dict(
        request_id="rg-1",
        student_item_id="s1",
        student_id="012345",
        student_name="Ada",
        question_id="q3",
        reason="method is valid",
        status="open",
    )
    base.update(kwargs)
    return RegradeReportItem(**base)


class _Harness(App):
    def __init__(self, items, teacher_mode=False):
        super().__init__()
        self._items = items
        self._teacher_mode = teacher_mode
        self.result = "unset"

    async def on_mount(self) -> None:
        await self.push_screen(
            RegradeQueueScreen(self._items, teacher_mode=self._teacher_mode),
            lambda value: setattr(self, "result", value),
        )


def test_empty_queue_mounts_without_crashing() -> None:
    async def run() -> None:
        app = _Harness([])
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.screen.query_one("#regrade-close")

    asyncio.run(run())


def test_enter_opens_the_disputed_question() -> None:
    async def run() -> None:
        app = _Harness([_item()])
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
        assert isinstance(app.result, RegradeChoice)
        assert app.result.action == "open"
        assert app.result.student_item_id == "s1"
        assert app.result.question_id == "q3"

    asyncio.run(run())


def test_r_resolves_and_d_denies() -> None:
    async def resolve() -> None:
        app = _Harness([_item()])
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
        assert isinstance(app.result, RegradeChoice)
        assert app.result.action == "resolve"
        assert app.result.request_id == "rg-1"

    async def deny() -> None:
        app = _Harness([_item()])
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
        assert app.result.action == "deny"

    asyncio.run(resolve())
    asyncio.run(deny())


def test_unmatched_dispute_cannot_open_but_can_resolve() -> None:
    async def run() -> None:
        app = _Harness([_item(student_item_id="", student_name="")])
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.result == "unset"
            await pilot.press("r")
            await pilot.pause()
        assert isinstance(app.result, RegradeChoice)
        assert app.result.action == "resolve"

    asyncio.run(run())
