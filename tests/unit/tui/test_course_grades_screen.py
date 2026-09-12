from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App
from textual.widgets import Input, RichLog, Select

from opengrader.tui.ui.screens import course_grades_screen as screen_module
from opengrader.tui.ui.screens.course_grades_screen import (
    VIEW_AT_RISK,
    VIEW_OUTCOMES,
    VIEW_SUMMARY,
    CourseGradesScreen,
)


class _Host(App):
    def __init__(self, screen: CourseGradesScreen) -> None:
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _patch_cli(monkeypatch, captured: list[list[str]]):  # noqa: ANN001, ANN202
    def _fake(argv):  # noqa: ANN001, ANN202
        captured.append(list(argv))
        print("(stub course report)")

    monkeypatch.setattr(screen_module, "run_course_grades_cli", _fake)


async def _settle(pilot) -> None:  # noqa: ANN001
    for _ in range(40):
        await pilot.pause()
        await asyncio.sleep(0.01)


def _run(screen: CourseGradesScreen, body):  # noqa: ANN001, ANN202
    result: list = []

    async def go() -> None:
        app = _Host(screen)
        async with app.run_test(size=(140, 46)) as pilot:
            await pilot.pause()
            result.append(await body(pilot, app.screen))

    asyncio.run(go())
    return result[0]


def test_the_course_path_is_passed_through(monkeypatch) -> None:  # noqa: ANN001
    captured: list[list[str]] = []
    _patch_cli(monkeypatch, captured)

    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.click("#course-grades-run")
        await _settle(pilot)

    _run(CourseGradesScreen(course_path="/term/course.toml"), body)
    assert captured == [["--course", "/term/course.toml"]]


def test_running_with_no_course_file_is_refused(monkeypatch) -> None:  # noqa: ANN001
    captured: list[list[str]] = []
    _patch_cli(monkeypatch, captured)

    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.click("#course-grades-run")
        await _settle(pilot)

    _run(CourseGradesScreen(), body)
    assert captured == []


def test_each_view_only_adds_flags(monkeypatch) -> None:  # noqa: ANN001
    captured: list[list[str]] = []
    _patch_cli(monkeypatch, captured)

    def body_for(value: str):  # noqa: ANN202
        async def body(pilot, screen):  # noqa: ANN001, ANN202
            screen.query_one("#course-grades-view", Select).value = value
            await pilot.pause()
            await pilot.click("#course-grades-run")
            await _settle(pilot)

        return body

    for value in (VIEW_SUMMARY, VIEW_AT_RISK, VIEW_OUTCOMES):
        _run(CourseGradesScreen(course_path="/c.toml"), body_for(value))

    assert captured[0] == ["--course", "/c.toml", "--summary"]
    assert captured[1] == ["--course", "/c.toml", "--summary", "--at-risk"]
    assert captured[2] == ["--course", "/c.toml", "--summary", "--outcomes"]


def test_a_csv_path_becomes_a_flag(monkeypatch) -> None:  # noqa: ANN001
    captured: list[list[str]] = []
    _patch_cli(monkeypatch, captured)

    async def body(pilot, screen):  # noqa: ANN001, ANN202
        screen.query_one("#course-grades-csv", Input).value = "  /out/grades.csv  "
        await pilot.pause()
        await pilot.click("#course-grades-run")
        await _settle(pilot)

    _run(CourseGradesScreen(course_path="/c.toml"), body)
    assert captured[0] == ["--course", "/c.toml", "--csv", "/out/grades.csv"]


def test_the_report_lands_in_the_log(monkeypatch) -> None:  # noqa: ANN001
    _patch_cli(monkeypatch, [])

    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.click("#course-grades-run")
        await _settle(pilot)
        return len(screen.query_one("#course-grades-log", RichLog).lines)

    assert _run(CourseGradesScreen(course_path="/c.toml"), body) > 0


def test_a_failing_report_leaves_the_screen_usable(monkeypatch) -> None:  # noqa: ANN001
    def _boom(_argv):  # noqa: ANN001, ANN202
        raise RuntimeError("bad course file")

    monkeypatch.setattr(screen_module, "run_course_grades_cli", _boom)

    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.click("#course-grades-run")
        await _settle(pilot)
        from textual.widgets import Button

        return screen.query_one("#course-grades-run", Button).disabled

    assert _run(CourseGradesScreen(course_path="/c.toml"), body) is False


def test_per_assignment_load_failures_reach_the_log(monkeypatch) -> None:  # noqa: ANN001
    import sys

    def _warn(_argv):  # noqa: ANN001, ANN202
        print("CS 146")
        print("  ! Midterm: no .submission.md files", file=sys.stderr)

    monkeypatch.setattr(screen_module, "run_course_grades_cli", _warn)

    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.click("#course-grades-run")
        await _settle(pilot)
        lines = screen.query_one("#course-grades-log", RichLog).lines
        return " ".join(str(line) for line in lines)

    text = _run(CourseGradesScreen(course_path="/c.toml"), body)
    assert "Midterm" in text


def test_the_course_file_is_found_by_walking_up_from_the_submissions(tmp_path: Path) -> None:
    from opengrader.tui.ui.screens.setup_screen import _find_course_file

    (tmp_path / "course.toml").write_text('name = "X"\n')
    deep = tmp_path / "exams" / "midterm" / "student-submissions"
    deep.mkdir(parents=True)
    assert _find_course_file(deep) == tmp_path / "course.toml"


def test_no_course_file_nearby_means_an_empty_field(tmp_path: Path) -> None:
    from opengrader.tui.ui.screens.setup_screen import _find_course_file

    deep = tmp_path / "a" / "b" / "subs"
    deep.mkdir(parents=True)
    assert _find_course_file(deep) is None


def test_new_opens_the_builder_and_takes_back_the_written_path(tmp_path: Path) -> None:
    import asyncio

    from opengrader.tui.ui.screens.course_builder_screen import CourseBuilderScreen

    written = tmp_path / "course.toml"

    async def go() -> str:
        app = _Host(CourseGradesScreen())
        async with app.run_test(size=(140, 46)) as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.click("#course-grades-new")
            await pilot.pause()
            builder = app.screen
            assert isinstance(builder, CourseBuilderScreen), type(builder)
            builder.dismiss(written)
            await pilot.pause()
            return screen.query_one("#course-grades-path", Input).value

    assert asyncio.run(go()) == str(written)


def test_cancelling_the_builder_leaves_the_course_field_alone(tmp_path: Path) -> None:
    import asyncio

    async def go() -> str:
        app = _Host(CourseGradesScreen(course_path="/kept/course.toml"))
        async with app.run_test(size=(140, 46)) as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.click("#course-grades-new")
            await pilot.pause()
            app.screen.dismiss(None)
            await pilot.pause()
            return screen.query_one("#course-grades-path", Input).value

    assert asyncio.run(go()) == "/kept/course.toml"
