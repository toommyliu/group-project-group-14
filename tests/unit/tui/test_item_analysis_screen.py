"""The item-analysis modal's per-section cycler (f)."""

import asyncio

from opengrader.core.analytics import ItemAnalysisReport
from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens import ItemAnalysisScreen

from .fakes import DEFAULT_IMAGE_PATH

# Empty reports keep the test off matplotlib (no chart when there are no items);
# the cohort size stands in for "which scope am I looking at".
_REPORTS = {
    None: ItemAnalysisReport(items=(), class_size=3),
    "MW": ItemAnalysisReport(items=(), class_size=2),
    "TTh": ItemAnalysisReport(items=(), class_size=1),
}


def _build(section: str | None) -> ItemAnalysisReport:
    return _REPORTS[section]


def test_cycle_section_advances_scope_and_rebuilds() -> None:
    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = ItemAnalysisScreen(
                _build(None),
                report_builder=_build,
                sections=("MW", "TTh"),
            )
            await app.push_screen(screen)
            await pilot.pause()

            assert screen._current_scope is None

            await pilot.press("f")
            await pilot.pause()
            assert screen._current_scope == "MW"
            assert screen._report.class_size == 2

            await pilot.press("f")
            await pilot.pause()
            assert screen._current_scope == "TTh"

            # Wraps back to all-sections.
            await pilot.press("f")
            await pilot.pause()
            assert screen._current_scope is None
            assert screen._report.class_size == 3

    asyncio.run(run())


def test_no_cycler_without_sections() -> None:
    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = ItemAnalysisScreen(_build(None))
            await app.push_screen(screen)
            await pilot.pause()

            # A single scope: pressing f is a no-op, not a crash.
            await pilot.press("f")
            await pilot.pause()
            assert screen._current_scope is None

    asyncio.run(run())
