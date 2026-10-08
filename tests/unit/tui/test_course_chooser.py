import asyncio
import shutil
from pathlib import Path

from rich.console import Console
from textual.widgets import OptionList

from opengrader.core.workspace.models import CourseDetails
from opengrader.core.workspace.store import Workspace
from opengrader.server.registry import WorkspaceRegistry
from opengrader.tui.course_chooser import CourseChooser


def _courses(tmp_path: Path) -> tuple[Path, Path]:
    registry = WorkspaceRegistry()
    roots = {}
    for folder, course in (
        ("cs146", CourseDetails(number="CS 146", name="Data Structures", term="fall", year=2026)),
        ("cs49j", CourseDetails(number="CS 49J", term="spring", year=2026)),
        ("cmpe30", CourseDetails(number="CMPE 30", term="fall", year=2026)),
    ):
        root = roots[folder] = tmp_path / folder
        root.mkdir()
        Workspace.create(root)
        registry.opened(registry.register(root, course).id)
    shutil.rmtree(roots["cmpe30"])
    return roots["cs146"].resolve(), roots["cmpe30"].resolve()


def _plain(prompt) -> str:
    console = Console(width=200, color_system=None, highlight=False)
    with console.capture() as capture:
        console.print(prompt)
    return "\n".join(line.rstrip() for line in capture.get().splitlines())


def test_courses_are_grouped_by_term_and_the_last_opened_available_one_is_chosen(
    tmp_path: Path,
) -> None:
    live, gone = _courses(tmp_path)

    async def run():
        app = CourseChooser(WorkspaceRegistry().list())
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause()
            listing = app.query_one(OptionList)
            rows = [
                _plain(listing.get_option_at_index(i).prompt)
                for i in range(listing.option_count)
            ]
            await pilot.press("enter")
        return rows, app.return_value

    rows, chosen = asyncio.run(run())
    assert rows == [
        "Fall 2026",
        f"CS 146   Data Structures\n         {live}",
        "",
        "Spring 2026",
        f"CS 49J\n         {tmp_path.resolve() / 'cs49j'}",
        "",
        "No term",
        f"cmpe30\n         Folder unavailable · {gone}",
    ]
    assert chosen == tmp_path.resolve() / "cs49j"


def test_an_unavailable_course_cannot_be_opened(tmp_path: Path) -> None:
    _courses(tmp_path)

    async def run() -> bool:
        app = CourseChooser(WorkspaceRegistry().list())
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause()
            listing = app.query_one(OptionList)
            listing.highlighted = listing.option_count - 1
            await pilot.press("enter")
            await pilot.pause()
            return app.is_running

    assert asyncio.run(run())


def test_slash_filters_courses_from_the_status_bar(tmp_path: Path) -> None:
    _courses(tmp_path)

    async def run():
        app = CourseChooser(WorkspaceRegistry().list())
        async with app.run_test(size=(110, 30)) as pilot:
            await pilot.pause()
            listing = app.query_one(OptionList)

            def shown() -> list[str | None]:
                return [
                    listing.get_option_at_index(i).id
                    for i in range(listing.option_count)
                    if listing.get_option_at_index(i).id
                ]

            await pilot.press("/", "s", "p", "r", "i", "n", "q")
            await pilot.pause()
            nothing = app.query_one("#courses-empty").display, app.is_running
            await pilot.press("ctrl+u", "2", "0", "2", "6")
            await pilot.pause()
            top = next(
                c.number for c in app.courses if c.id == listing.highlighted_option.id
            )
            await pilot.press("ctrl+u", "s", "p", "r", "i", "n", "g")
            await pilot.pause()
            spring = [c.number for c in app.courses if c.id in shown()]
            count = str(app.query_one("#filter-count").render())
            await pilot.press("enter")
            await pilot.pause()
            focused = app.focused is listing
            await pilot.press("escape")
            await pilot.pause()
            cleared = len(shown()), app.query_one("#filter-bar").display
        return nothing, top, spring, count, focused, cleared

    nothing, top, spring, count, focused, cleared = asyncio.run(run())
    assert top == "CS 146"
    assert nothing == (True, True)
    assert spring == ["CS 49J"]
    assert count == "1 of 3"
    assert focused
    assert cleared == (3, False)

