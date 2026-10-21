from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App
from textual.widgets import Input, Static

from opengrader.core.course import DiscoveredAssignment, load_course_config
from opengrader.tui.ui.screens.course_builder_screen import (
    CourseBuilderScreen,
    _AssignmentRow,
)


class _Host(App):
    def __init__(self, screen: CourseBuilderScreen) -> None:
        super().__init__()
        self._screen = screen
        self.result: Path | None = None

    def on_mount(self) -> None:
        self.push_screen(self._screen, lambda value: setattr(self, "result", value))


def _run(body, screen: CourseBuilderScreen | None = None):  # noqa: ANN001, ANN202
    holder: list = []

    async def go() -> None:
        app = _Host(screen or CourseBuilderScreen())
        async with app.run_test(size=(150, 46)) as pilot:
            await pilot.pause()
            holder.append(await body(pilot, app.screen, app))

    asyncio.run(go())
    return holder[0]


def _found(name: str, path: Path, solutions: Path | None = None) -> DiscoveredAssignment:
    return DiscoveredAssignment(name=name, path=path, solutions=solutions)


async def _settle(pilot) -> None:  # noqa: ANN001
    await pilot.pause()
    await pilot.pause()


def test_new_rows_default_to_equal_weight(tmp_path: Path) -> None:
    async def body(pilot, screen, app):  # noqa: ANN001, ANN202
        screen._add_rows([_found("A", tmp_path / "a"), _found("B", tmp_path / "b")])
        await _settle(pilot)
        rows = list(screen.query(_AssignmentRow))
        return [row.weight_input.value for row in rows], [
            str(row.share_label.render()) for row in rows
        ]

    weights, shares = _run(body)
    assert weights == ["1", "1"]
    assert shares == ["→ 50%", "→ 50%"]


def test_the_share_updates_as_a_weight_is_typed(tmp_path: Path) -> None:
    async def body(pilot, screen, app):  # noqa: ANN001, ANN202
        screen._add_rows([_found("A", tmp_path / "a"), _found("B", tmp_path / "b")])
        await _settle(pilot)
        rows = list(screen.query(_AssignmentRow))
        rows[0].weight_input.value = "3"
        await _settle(pilot)
        return [str(row.share_label.render()) for row in rows]

    assert _run(body) == ["→ 75%", "→ 25%"]


def test_percentages_and_ratios_produce_the_same_shares(tmp_path: Path) -> None:
    async def body(pilot, screen, app):  # noqa: ANN001, ANN202
        screen._add_rows([_found("A", tmp_path / "a"), _found("B", tmp_path / "b")])
        await _settle(pilot)
        rows = list(screen.query(_AssignmentRow))
        rows[0].weight_input.value = "25"
        rows[1].weight_input.value = "75"
        await _settle(pilot)
        return [str(row.share_label.render()) for row in rows]

    assert _run(body) == ["→ 25%", "→ 75%"]


def test_a_half_typed_weight_does_not_break_the_other_rows(tmp_path: Path) -> None:
    async def body(pilot, screen, app):  # noqa: ANN001, ANN202
        screen._add_rows([_found("A", tmp_path / "a"), _found("B", tmp_path / "b")])
        await _settle(pilot)
        rows = list(screen.query(_AssignmentRow))
        rows[0].weight_input.value = ""
        await _settle(pilot)
        return [str(row.share_label.render()) for row in rows]

    assert _run(body) == ["→ —", "→ 100%"]


def test_add_assignment_appends_a_blank_row() -> None:
    async def body(pilot, screen, app):  # noqa: ANN001, ANN202
        await pilot.click("#course-builder-add")
        await _settle(pilot)
        rows = list(screen.query(_AssignmentRow))
        return len(rows), rows[0].name_input.value, rows[0].weight_input.value

    count, name, weight = _run(body)
    assert count == 1
    assert name == ""
    assert weight == "1"


def test_removing_a_row_rebalances_the_shares(tmp_path: Path) -> None:
    async def body(pilot, screen, app):  # noqa: ANN001, ANN202
        screen._add_rows(
            [
                _found("A", tmp_path / "a"),
                _found("B", tmp_path / "b"),
                _found("C", tmp_path / "c"),
            ]
        )
        await _settle(pilot)
        rows = list(screen.query(_AssignmentRow))
        await pilot.click(rows[0].remove_button)
        await _settle(pilot)
        remaining = list(screen.query(_AssignmentRow))
        return [row.name_input.value for row in remaining], [
            str(row.share_label.render()) for row in remaining
        ]

    names, shares = _run(body)
    assert names == ["B", "C"]
    assert shares == ["→ 50%", "→ 50%"]


def test_the_status_line_counts_rows_that_are_ready(tmp_path: Path) -> None:
    async def body(pilot, screen, app):  # noqa: ANN001, ANN202
        screen._add_rows([_found("A", tmp_path / "a"), None])
        await _settle(pilot)
        return str(screen.query_one("#course-builder-status", Static).render())

    status = _run(body)
    assert "1 assignment(s) ready" in status
    assert "1 row(s) still need" in status


def test_saving_writes_a_file_that_loads_back(tmp_path: Path) -> None:
    subs = tmp_path / "quiz" / "student-submissions"
    subs.mkdir(parents=True)

    async def body(pilot, screen, app):  # noqa: ANN001, ANN202
        screen.query_one("#course-builder-name", Input).value = "CS 146"
        screen.query_one("#course-builder-output", Input).value = str(tmp_path)
        screen._add_rows([_found("Quiz 1", subs)])
        await _settle(pilot)
        list(screen.query(_AssignmentRow))[0].weight_input.value = "40"
        await _settle(pilot)
        await pilot.click("#course-builder-save")
        await _settle(pilot)
        return app.result

    written = _run(body)
    assert written == tmp_path / "course.toml"
    config = load_course_config(written)
    assert config.name == "CS 146"
    assert config.assignments[0].name == "Quiz 1"
    assert config.assignments[0].weight == 40.0


def test_saving_with_no_assignments_is_refused(tmp_path: Path) -> None:
    async def body(pilot, screen, app):  # noqa: ANN001, ANN202
        screen.query_one("#course-builder-output", Input).value = str(tmp_path)
        await pilot.click("#course-builder-save")
        await _settle(pilot)
        return app.result, (tmp_path / "course.toml").exists()

    result, written = _run(body)
    assert result is None
    assert written is False


def test_saving_with_no_destination_is_refused(tmp_path: Path) -> None:
    async def body(pilot, screen, app):  # noqa: ANN001, ANN202
        screen._add_rows([_found("A", tmp_path / "a")])
        await _settle(pilot)
        await pilot.click("#course-builder-save")
        await _settle(pilot)
        return app.result

    assert _run(body) is None


def test_an_unfilled_row_is_skipped_rather_than_blocking_the_save(tmp_path: Path) -> None:
    async def body(pilot, screen, app):  # noqa: ANN001, ANN202
        screen.query_one("#course-builder-output", Input).value = str(tmp_path)
        screen._add_rows([_found("Quiz 1", tmp_path / "subs"), None])
        await _settle(pilot)
        await pilot.click("#course-builder-save")
        await _settle(pilot)
        return app.result

    written = _run(body)
    assert written is not None
    assert len(load_course_config(written).assignments) == 1


def test_cancelling_writes_nothing(tmp_path: Path) -> None:
    async def body(pilot, screen, app):  # noqa: ANN001, ANN202
        screen.query_one("#course-builder-output", Input).value = str(tmp_path)
        screen._add_rows([_found("A", tmp_path / "a")])
        await _settle(pilot)
        await pilot.click("#course-builder-cancel")
        await _settle(pilot)
        return app.result, (tmp_path / "course.toml").exists()

    result, written = _run(body)
    assert result is None
    assert written is False


def test_a_scan_prefills_names_paths_and_answer_keys(tmp_path: Path) -> None:
    key = tmp_path / "quiz" / "solutions.md"
    key.parent.mkdir(parents=True)
    key.write_text("x")

    async def body(pilot, screen, app):  # noqa: ANN001, ANN202
        screen._add_rows([_found("Quiz 1", tmp_path / "quiz" / "subs", key)])
        await _settle(pilot)
        row = list(screen.query(_AssignmentRow))[0]
        return (
            row.name_input.value,
            row.submissions_input.value,
            row.solutions_input.value,
        )

    name, subs, solutions = _run(body)
    assert name == "Quiz 1"
    assert subs.endswith("subs")
    assert solutions.endswith("solutions.md")


def test_the_help_screen_documents_outcomes_and_the_course_builder() -> None:
    from textual.widgets import Static

    from opengrader.tui.tui import TuiApp
    from opengrader.tui.ui.screens import HelpScreen

    async def go() -> str:
        app = TuiApp()
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause()
            # Classic starts in the class-folder box, which would take the key.
            app.set_focus(None)
            await pilot.press("question_mark")
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen), type(app.screen)
            return " ".join(str(w.render()) for w in app.screen.query(Static))

    text = asyncio.run(go())
    assert "**Outcome**" in text
    assert "CLO3" in text
    assert "measured in points" in text
    assert "New…" in text
    assert "10/10/30/50 and 1/1/3/5" in text
    assert "live share of the final grade" in text
