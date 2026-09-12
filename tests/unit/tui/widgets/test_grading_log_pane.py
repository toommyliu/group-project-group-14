import asyncio

from textual.app import App, ComposeResult

from opengrader.tui.state.grading_log import GradingLog, GradingLogEntry
from opengrader.tui.ui.widgets.grading_log_pane import (
    GradingLogPane,
    _GradingLogEntryItem,
)


class _LogApp(App[None]):
    def __init__(self) -> None:
        self.pane = GradingLogPane(id="grading-log")
        self.selected_entries: list[GradingLogEntry] = []
        super().__init__()

    def compose(self) -> ComposeResult:
        yield self.pane

    def on_grading_log_pane_entry_selected(
        self, event: GradingLogPane.EntrySelected
    ) -> None:
        self.selected_entries.append(event.entry)


def test_clicking_a_log_row_posts_entry_selected() -> None:
    async def run() -> None:
        app = _LogApp()
        async with app.run_test() as pilot:
            log = GradingLog()
            log.record(
                GradingLogEntry(
                    kind="grade",
                    question_id="q1",
                    summary="8/10 pts",
                    student_item_id="s1",
                    student_name="Tommy Liu",
                    student_display_id="1234",
                )
            )
            app.pane.update_log(
                log,
                teacher_mode=False,
                student_item_id=None,
                question_id=None,
            )
            await pilot.pause()

            await pilot.click(_GradingLogEntryItem)
            await pilot.pause()

            assert len(app.selected_entries) == 1
            assert app.selected_entries[0].question_id == "q1"
            assert app.selected_entries[0].student_item_id == "s1"

    asyncio.run(run())


def test_empty_state_row_is_not_clickable() -> None:
    async def run() -> None:
        app = _LogApp()
        async with app.run_test() as pilot:
            app.pane.update_log(
                GradingLog(),
                teacher_mode=False,
                student_item_id=None,
                question_id=None,
            )
            await pilot.pause()

            assert not app.pane.query(_GradingLogEntryItem)
            await pilot.click("#grading-log-latest")
            await pilot.pause()

            assert app.selected_entries == []

    asyncio.run(run())
