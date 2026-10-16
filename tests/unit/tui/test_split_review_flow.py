"""The pipeline card's answer to "the split looks wrong here": a dialog that
applies the ticked fixes, keeps the rest, and runs again — or stops with
nothing changed."""

from __future__ import annotations

import asyncio
from pathlib import Path

import fitz
import pytest
from textual.widgets import Checkbox, Input, Static

import opengrader.tui.ui.screens.setup_screen as setup_screen
from opengrader.core.pages.split_review import (
    APPLIED,
    KEPT,
    pending_split_findings,
    read_part,
    read_split_review,
    record_split_review,
)
from opengrader.core.pipeline import PipelineReport, StageOutcome, StageStatus
from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens.setup_screen import SetupScreen

from .fakes import DEFAULT_IMAGE_PATH


async def _settle(pilot, predicate, limit: int = 150) -> None:  # noqa: ANN001
    for _ in range(limit):
        await pilot.pause()
        await asyncio.sleep(0.01)
        if predicate():
            return


def _has(app, selector: str) -> bool:  # noqa: ANN001
    try:
        app.screen.query_one(selector)
        return True
    except Exception:  # noqa: BLE001 - "not there yet" is exactly what we're polling for
        return False


def _pdf(split_dir: Path, name: str, labels: list[str]) -> None:
    split_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for label in labels:
        doc.new_page(width=612, height=792).insert_text((72, 72), label)
    doc.save(str(split_dir / f"{name}.pdf"))
    doc.close()


def _two_bad_places(split_dir: Path) -> None:
    """A 5 + 1 split (a merge is proposed) and a short last submission (no fix)."""
    _pdf(split_dir, "submission-0001", ["A1", "A2", "A3", "A4", "A5"])
    _pdf(split_dir, "submission-0002", ["A6"])
    _pdf(split_dir, "submission-0003", ["B1", "B2", "B3", "B4"])
    names = ("submission-0001", "submission-0002", "submission-0003")
    record_split_review(split_dir, [read_part(split_dir, name) for name in names], 6)


def _pages(pdf: Path) -> list[str]:
    with fitz.open(str(pdf)) as doc:
        return [page.get_text().strip() for page in doc]


def _fake_pipeline(calls: list):
    def run(request, *, progress=None):
        calls.append(request)
        if pending_split_findings(request.split_dir):
            outcome = StageOutcome(
                stage="extract", status=StageStatus.PAUSED, message="split looks wrong", detail="split-review:\n…"
            )
        else:
            outcome = StageOutcome(stage="extract", status=StageStatus.RAN, message="ok")
        return PipelineReport(stages=(outcome,), submissions_dir=request.submissions_dir, qa_file=request.qa_file)

    return run


def _drive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer) -> tuple[list, dict]:  # noqa: ANN001
    calls: list = []
    seen: dict = {}
    monkeypatch.setattr(setup_screen, "run_pipeline", _fake_pipeline(calls))

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#pipe-scan", Input).value = str(tmp_path / "scan.pdf")
            app.query_one("#pipe-out", Input).value = str(tmp_path / "cohort")
            screen._run_pipeline()
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: _has(app, "#split-review-dialog"))
            seen["ticked"] = [box.value for box in app.screen.query(Checkbox)]
            seen["text"] = " ".join(
                str(widget.render()) for widget in app.screen.query(".split-review-read")
            )
            await answer(app, pilot)
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: not _has(app, "#split-review-dialog") and screen.extraction_in_progress is False)
            seen["status"] = str(app.query_one("#pipe-progress-status", Static).render())

    asyncio.run(run())
    return calls, seen


def test_continue_applies_the_proposed_merge_keeps_the_rest_and_runs_again(tmp_path, monkeypatch) -> None:
    split = tmp_path / "cohort" / "split"
    _two_bad_places(split)

    async def answer(app, pilot) -> None:  # noqa: ANN001
        app.screen.query_one("#split-review-continue").press()

    calls, seen = _drive(tmp_path, monkeypatch, answer)

    # One box: only the place with a fix can be fixed from here.
    assert seen["ticked"] == [True]
    assert "Merge submission-0001 and submission-0002" in seen["text"]
    assert "No fix can be proposed" in seen["text"]
    assert _pages(split / "submission-0001.pdf") == ["A1", "A2", "A3", "A4", "A5", "A6"]
    assert not (split / "submission-0002.pdf").exists()
    assert sorted(f.status for f in read_split_review(split)[0]) == [APPLIED, KEPT]
    assert len(calls) == 2


def test_unticking_the_fix_keeps_the_split_as_it_is(tmp_path, monkeypatch) -> None:
    split = tmp_path / "cohort" / "split"
    _two_bad_places(split)

    async def answer(app, pilot) -> None:  # noqa: ANN001
        app.screen.query_one("#split-review-fix-0", Checkbox).value = False
        await pilot.pause()
        app.screen.query_one("#split-review-continue").press()

    calls, _seen = _drive(tmp_path, monkeypatch, answer)

    assert (split / "submission-0002.pdf").exists()
    assert [f.status for f in read_split_review(split)[0]] == [KEPT, KEPT]
    assert len(calls) == 2


def test_stop_changes_nothing_and_does_not_run_again(tmp_path, monkeypatch) -> None:
    split = tmp_path / "cohort" / "split"
    _two_bad_places(split)

    async def answer(app, pilot) -> None:  # noqa: ANN001
        app.screen.query_one("#split-review-stop").press()

    calls, seen = _drive(tmp_path, monkeypatch, answer)

    assert _pages(split / "submission-0002.pdf") == ["A6"]
    assert len(pending_split_findings(split)) == 2
    assert len(calls) == 1
    assert "nothing was changed" in seen["status"].lower()
