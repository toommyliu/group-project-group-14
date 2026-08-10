"""The region-map editor, driven headlessly through Textual's pilot.

The editing keys are the *primary* interface — Opengrader runs in terminals that
cannot draw a pixel, so mouse drag is an enhancement and everything asserted
here is reachable from the keyboard.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import fitz
import pytest
from PIL import Image, ImageDraw
from textual.app import App
from textual.widgets import Input

from opengrader.core.region_map import (
    MappedQuestion,
    RegionBox,
    blank_region_map,
    load_region_map,
    save_region_map,
)
from opengrader.tui.ui.screens.region_map_screen import NUDGE_STEP, RegionMapScreen

# Generous, per the pilot recipe: the default 80x24 clips widgets and clicks miss.
SIZE = (140, 45)


def _image_only_pdf(path: Path, pages: int = 2, marker: str = "ASSIGNMENT") -> Path:
    doc = fitz.open()
    for index in range(pages):
        canvas = Image.new("RGB", (600, 780), "white")
        draw = ImageDraw.Draw(canvas)
        draw.text((40, 50), f"{marker} page {index + 1}", fill="black")
        raster = path.parent / f"{path.stem}-{index}.png"
        canvas.save(raster)
        page = doc.new_page(width=612, height=792)
        page.insert_image(page.rect, filename=str(raster))
    doc.save(str(path))
    doc.close()
    return path


class _EditorApp(App):
    """A bare host for the screen, so it is tested exactly as it is shipped."""

    def __init__(self, pdf: Path, map_path: Path | None = None, **kwargs) -> None:
        super().__init__()
        self._pdf = pdf
        self._map_path = map_path
        self._kwargs = kwargs
        self.result: object = "unset"

    def on_mount(self) -> None:
        self.push_screen(
            RegionMapScreen(self._pdf, self._map_path, **self._kwargs),
            lambda value: setattr(self, "result", value),
        )


@pytest.fixture()
def assignment(tmp_path: Path) -> Path:
    return _image_only_pdf(tmp_path / "midterm.pdf")


def _saved_map(assignment: Path, path: Path) -> Path:
    region_map = replace(
        blank_region_map(assignment, assignment_title="Paper Midterm"),
        name_field=RegionBox(1, (0.55, 0.03, 0.40, 0.06)),
        student_id_field=RegionBox(1, (0.08, 0.03, 0.40, 0.06)),
        questions=(
            MappedQuestion("q1", 1, 1, (0.10, 0.30, 0.80, 0.20), possible_points=10.0),
            MappedQuestion("q2", 2, 2, (0.10, 0.55, 0.80, 0.20), possible_points=5.0),
        ),
    )
    return save_region_map(region_map, path)


# ── opening ────────────────────────────────────────────────────────────────


def test_it_opens_an_existing_map_and_shows_the_first_page(assignment: Path, tmp_path: Path) -> None:
    async def run() -> None:
        app = _EditorApp(assignment, _saved_map(assignment, tmp_path / "m.json"))
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            assert len(screen._map.questions) == 2
            assert screen._page == 1

    asyncio.run(run())


def test_it_starts_empty_for_an_assignment_with_no_map(assignment: Path) -> None:
    async def run() -> None:
        app = _EditorApp(assignment)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert app.screen._map.questions == ()

    asyncio.run(run())


def test_a_map_for_a_different_assignment_is_reported_and_not_adopted(
    assignment: Path, tmp_path: Path
) -> None:
    """Its boxes describe a different page; silently using them mis-crops everything."""
    other = _image_only_pdf(tmp_path / "final.pdf", marker="OTHER")
    foreign = _saved_map(other, tmp_path / "foreign.json")

    async def run() -> None:
        app = _EditorApp(assignment, foreign)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            assert screen._map.questions == ()
            assert "not the file this region map was drawn on" in screen._status

    asyncio.run(run())


# ── keyboard editing ───────────────────────────────────────────────────────


def test_a_adds_a_region_on_the_current_page(assignment: Path) -> None:
    async def run() -> None:
        app = _EditorApp(assignment)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()

            screen = app.screen
            assert [q.question_id for q in screen._map.questions] == ["q1"]
            assert screen._map.questions[0].page == 1

            await pilot.press("a")
            await pilot.pause()
            assert [q.question_id for q in screen._map.questions] == ["q1", "q2"]

    asyncio.run(run())


def test_j_and_k_walk_the_selection_and_follow_it_to_its_page(
    assignment: Path, tmp_path: Path
) -> None:
    async def run() -> None:
        app = _EditorApp(assignment, _saved_map(assignment, tmp_path / "m.json"))
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            # Targets: name field, id field, q1, q2.
            assert screen._selection == 0

            await pilot.press("j", "j")
            await pilot.pause()
            assert screen._selected()[0] == "question"
            assert screen._map.questions[screen._selected()[1]].question_id == "q1"

            await pilot.press("j")
            await pilot.pause()
            assert screen._map.questions[screen._selected()[1]].question_id == "q2"
            # The page followed the selection — a cursor you cannot see is no cursor.
            assert screen._page == 2

            await pilot.press("k")
            await pilot.pause()
            assert screen._map.questions[screen._selected()[1]].question_id == "q1"

    asyncio.run(run())


def test_arrows_move_the_selected_box(assignment: Path, tmp_path: Path) -> None:
    async def run() -> None:
        app = _EditorApp(assignment, _saved_map(assignment, tmp_path / "m.json"))
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.press("j", "j")  # name -> id -> q1
            await pilot.pause()
            before = screen._map.questions[0].bbox

            await pilot.press("down", "right")
            await pilot.pause()

            after = screen._map.questions[0].bbox
            assert after[0] == pytest.approx(before[0] + NUDGE_STEP, abs=1e-6)
            assert after[1] == pytest.approx(before[1] + NUDGE_STEP, abs=1e-6)
            assert (after[2], after[3]) == (before[2], before[3])  # size unchanged

    asyncio.run(run())


def test_shift_arrows_resize_the_selected_box(assignment: Path, tmp_path: Path) -> None:
    async def run() -> None:
        app = _EditorApp(assignment, _saved_map(assignment, tmp_path / "m.json"))
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.press("j", "j")
            await pilot.pause()
            before = screen._map.questions[0].bbox

            await pilot.press("shift+down")
            await pilot.pause()
            assert screen._map.questions[0].bbox[3] == pytest.approx(
                before[3] + NUDGE_STEP, abs=1e-6
            )

            await pilot.press("shift+left")
            await pilot.pause()
            assert screen._map.questions[0].bbox[2] == pytest.approx(
                before[2] - NUDGE_STEP, abs=1e-6
            )
            # The origin never moved: resizing works from the top-left anchor.
            assert screen._map.questions[0].bbox[:2] == before[:2]

    asyncio.run(run())


def test_a_box_cannot_be_shrunk_to_nothing(assignment: Path) -> None:
    """A held key must not manufacture the very error the editor then refuses."""

    async def run() -> None:
        app = _EditorApp(assignment)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            for _ in range(60):
                await pilot.press("shift+up")
            await pilot.pause()

            _, _, width, height = app.screen._map.questions[0].bbox
            assert width > 0 and height > 0

    asyncio.run(run())


def test_d_deletes_the_selected_region(assignment: Path, tmp_path: Path) -> None:
    async def run() -> None:
        app = _EditorApp(assignment, _saved_map(assignment, tmp_path / "m.json"))
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.press("j", "j")  # q1
            await pilot.pause()

            await pilot.press("d")
            await pilot.pause()
            assert [q.question_id for q in screen._map.questions] == ["q2"]

    asyncio.run(run())


def test_d_on_an_identity_field_removes_just_that_field(assignment: Path, tmp_path: Path) -> None:
    async def run() -> None:
        app = _EditorApp(assignment, _saved_map(assignment, tmp_path / "m.json"))
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            assert screen._selected()[0] == "name"

            await pilot.press("d")
            await pilot.pause()
            assert screen._map.name_field is None
            assert screen._map.student_id_field is not None
            assert len(screen._map.questions) == 2

    asyncio.run(run())


def test_c_marks_a_continuation_on_the_current_page(assignment: Path, tmp_path: Path) -> None:
    async def run() -> None:
        app = _EditorApp(assignment, _saved_map(assignment, tmp_path / "m.json"))
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.press("j", "j")  # q1, on page 1
            await pilot.pause()

            await pilot.press("pagedown")  # to page 2
            await pilot.pause()
            assert screen._page == 2

            await pilot.press("c")
            await pilot.pause()

            q1 = screen._map.questions[0]
            assert len(q1.continuations) == 1
            assert q1.continuations[0].page == 2
            # ...and the new segment is what the arrows now move.
            assert screen._selected()[2] == 1

    asyncio.run(run())


def test_f_adds_the_missing_identity_fields_in_turn(assignment: Path) -> None:
    async def run() -> None:
        app = _EditorApp(assignment)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            screen = app.screen

            await pilot.press("f")
            await pilot.pause()
            assert screen._map.name_field is not None
            assert screen._map.student_id_field is None

            await pilot.press("f")
            await pilot.pause()
            assert screen._map.student_id_field is not None

    asyncio.run(run())


def test_g_fills_a_grid_as_a_first_class_starting_point(assignment: Path) -> None:
    async def run() -> None:
        app = _EditorApp(assignment)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()
            # 4 per page over a 2-page assignment.
            assert len(app.screen._map.questions) == 8

    asyncio.run(run())


def test_u_jumps_to_the_box_the_detector_was_least_sure_of(
    assignment: Path, tmp_path: Path
) -> None:
    """Correcting a proposal is not a linear job — the 40% box is the work."""
    path = tmp_path / "proposed.json"
    region_map = replace(
        blank_region_map(assignment),
        questions=(
            MappedQuestion("q1", 1, 1, (0.1, 0.2, 0.8, 0.1), confidence=0.95),
            MappedQuestion("q2", 2, 1, (0.1, 0.4, 0.8, 0.1), confidence=0.40),
            MappedQuestion("q3", 3, 2, (0.1, 0.6, 0.8, 0.1), confidence=0.80),
        ),
    )
    save_region_map(region_map, path)

    async def run() -> None:
        app = _EditorApp(assignment, path)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            screen = app.screen

            await pilot.press("u")
            await pilot.pause()
            assert screen._map.questions[screen._selected()[1]].question_id == "q2"

            await pilot.press("u")
            await pilot.pause()
            assert screen._map.questions[screen._selected()[1]].question_id == "q3"
            assert screen._page == 2  # and the page followed

    asyncio.run(run())


def test_u_says_so_when_every_box_has_been_reviewed(assignment: Path) -> None:
    async def run() -> None:
        app = _EditorApp(assignment)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("a")  # hand-drawn: no detector confidence
            await pilot.pause()

            await pilot.press("u")
            await pilot.pause()
            assert "reviewed by hand" in app.screen._status

    asyncio.run(run())


def test_page_keys_turn_the_page_and_stop_at_the_ends(assignment: Path) -> None:
    async def run() -> None:
        app = _EditorApp(assignment)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            screen = app.screen

            await pilot.press("pageup")
            await pilot.pause()
            assert screen._page == 1  # already at the front

            await pilot.press("pagedown", "pagedown", "pagedown")
            await pilot.pause()
            assert screen._page == 2  # a 2-page assignment has no page 3

    asyncio.run(run())


# ── metadata panel ─────────────────────────────────────────────────────────


def test_the_panel_edits_points_and_renumbers_the_selected_question(
    assignment: Path, tmp_path: Path
) -> None:
    async def run() -> None:
        app = _EditorApp(assignment, _saved_map(assignment, tmp_path / "m.json"))
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.press("j", "j")  # q1
            await pilot.pause()

            screen.query_one("#region-points", Input).value = "17.5"
            await pilot.pause()
            assert screen._map.questions[0].possible_points == 17.5

            screen.query_one("#region-number", Input).value = "6"
            await pilot.pause()
            # The default `q<N>` id follows the number, or the two would disagree
            # and the crop would be filed under the wrong rubric entry.
            assert screen._map.questions[0].number == 6
            assert screen._map.questions[0].question_id == "q6"

    asyncio.run(run())


def test_escape_in_a_text_field_returns_focus_instead_of_discarding_the_map(
    assignment: Path, tmp_path: Path
) -> None:
    async def run() -> None:
        app = _EditorApp(assignment, _saved_map(assignment, tmp_path / "m.json"))
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            screen = app.screen

            await pilot.press("e")  # focus the number input
            await pilot.pause()
            assert isinstance(screen.focused, Input)

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(screen.focused, Input)
            assert app.result == "unset"  # still open, nothing thrown away

    asyncio.run(run())


# ── saving ─────────────────────────────────────────────────────────────────


def test_save_writes_a_map_that_round_trips_to_disk(assignment: Path, tmp_path: Path) -> None:
    output = tmp_path / "written.regions.json"

    async def run() -> None:
        app = _EditorApp(assignment, output_path=output)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("f", "a")  # a name field and one answer region
            await pilot.pause()
            edited = app.screen._map

            await pilot.press("down", "down", "shift+right")
            await pilot.pause()
            edited = app.screen._map

            await pilot.press("ctrl+s")
            await pilot.pause()

            assert app.result == output
            restored = load_region_map(output)
            assert restored == edited
            assert json.loads(output.read_text())["source"]["filename"] == "midterm.pdf"

    asyncio.run(run())


def test_save_is_refused_while_an_error_finding_stands(assignment: Path, tmp_path: Path) -> None:
    """An empty map would extract into a submission with no questions at all."""
    output = tmp_path / "written.regions.json"

    async def run() -> None:
        app = _EditorApp(assignment, output_path=output)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()

            assert not output.exists()
            assert app.result == "unset"  # still open, so it can be fixed
            assert "Cannot save" in app.screen._status

    asyncio.run(run())


def test_save_is_allowed_with_warnings(assignment: Path, tmp_path: Path) -> None:
    """Leaving the extra-credit question unmapped is an ordinary Tuesday."""
    output = tmp_path / "written.regions.json"

    async def run() -> None:
        app = _EditorApp(assignment, output_path=output)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("a")  # one region: no points, no identity fields
            await pilot.pause()

            await pilot.press("ctrl+s")
            await pilot.pause()

            assert app.result == output
            assert output.exists()

    asyncio.run(run())


def test_escape_cancels_without_writing_anything(assignment: Path, tmp_path: Path) -> None:
    output = tmp_path / "written.regions.json"

    async def run() -> None:
        app = _EditorApp(assignment, output_path=output)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()

            await pilot.press("escape")
            await pilot.pause()

            assert app.result is None
            assert not output.exists()

    asyncio.run(run())


def test_the_findings_panel_tracks_the_map_as_it_is_edited(assignment: Path) -> None:
    async def run() -> None:
        from textual.widgets import Static

        app = _EditorApp(assignment)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            panel = app.screen.query_one("#region-findings", Static)
            assert "No answer regions are mapped" in str(panel.content)

            await pilot.press("a")
            await pilot.pause()
            assert "No answer regions are mapped" not in str(panel.content)

    asyncio.run(run())
