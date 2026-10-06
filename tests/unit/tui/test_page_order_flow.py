"""The pipeline card's answer to "these submissions look out of order": a
dialog that sorts what is ticked, keeps the rest, and runs again — or stops
with nothing moved."""

from __future__ import annotations

import asyncio
from pathlib import Path

import fitz
import pytest
from textual.widgets import Checkbox, Input, Static

import opengrader.tui.ui.screens.setup_screen as setup_screen
from opengrader.core.pages.order_review import (
    KEPT,
    SORTED,
    OrderFinding,
    pending_findings,
    read_order_review,
    write_order_review,
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


def _flag(split_dir: Path, name: str, *, by_template: bool) -> Path:
    split_dir.mkdir(parents=True, exist_ok=True)
    pdf = split_dir / f"{name}.pdf"
    doc = fitz.open()
    for number in (1, 3, 2):
        doc.new_page(width=612, height=792).insert_text((280, 770), f"Page {number}")
    doc.save(str(pdf))
    doc.close()
    existing, _positions = read_order_review(split_dir)
    write_order_review(split_dir, [*existing, OrderFinding(name=name, keys=(1, 3, 2), by_template=by_template)])
    return pdf


def _footers(pdf: Path) -> list[str]:
    with fitz.open(str(pdf)) as doc:
        return [page.get_text().strip() for page in doc]


def _fake_pipeline(calls: list):
    def run(request, *, progress=None):
        calls.append(request)
        if pending_findings(request.split_dir):
            outcome = StageOutcome(
                stage="extract", status=StageStatus.PAUSED, message="look out of order", detail="page-order:\n…"
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
            await _settle(pilot, lambda: _has(app, "#page-order-dialog"))
            seen["ticked"] = [box.value for box in app.screen.query(Checkbox)]
            await answer(app, pilot)
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: not _has(app, "#page-order-dialog") and screen.extraction_in_progress is False)
            seen["status"] = str(app.query_one("#pipe-progress-status", Static).render())

    asyncio.run(run())
    return calls, seen


def test_continue_sorts_what_is_ticked_keeps_the_rest_and_runs_again(tmp_path, monkeypatch) -> None:
    split = tmp_path / "cohort" / "split"
    by_template = _flag(split, "submission-0001", by_template=True)
    by_number = _flag(split, "submission-0002", by_template=False)

    async def answer(app, pilot) -> None:  # noqa: ANN001
        app.screen.query_one("#page-order-continue").press()

    calls, seen = _drive(tmp_path, monkeypatch, answer)

    # Only the template-backed finding starts ticked.
    assert seen["ticked"] == [True, False]
    assert _footers(by_template) == ["Page 1", "Page 2", "Page 3"]
    assert _footers(by_number) == ["Page 1", "Page 3", "Page 2"]
    assert [finding.status for finding in read_order_review(split)[0]] == [SORTED, KEPT]
    assert len(calls) == 2 and calls[0] == calls[1]


def test_stop_moves_nothing_and_does_not_run_again(tmp_path, monkeypatch) -> None:
    split = tmp_path / "cohort" / "split"
    pdf = _flag(split, "submission-0001", by_template=True)

    async def answer(app, pilot) -> None:  # noqa: ANN001
        app.screen.query_one("#page-order-stop").press()

    calls, seen = _drive(tmp_path, monkeypatch, answer)

    assert _footers(pdf) == ["Page 1", "Page 3", "Page 2"]
    assert len(pending_findings(split)) == 1
    assert len(calls) == 1
    assert "nothing was moved" in seen["status"].lower()
