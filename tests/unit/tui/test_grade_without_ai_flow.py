"""The pipeline card's answer to "the AI that grades written answers can't be
reached": a dialog that grades without AI, or skips auto-grading, and runs
again either way."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Input

import opengrader.tui.ui.screens.setup_screen as setup_screen
from opengrader.core.pipeline import PipelineReport, StageOutcome, StageStatus
from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens.setup_screen import SetupScreen

from .fakes import DEFAULT_IMAGE_PATH

PROBLEM = "Ollama isn't running. Open the Ollama app, then try again."


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


def _fake_pipeline(calls: list):
    def run(request, *, progress=None):
        calls.append(request)
        if len(calls) == 1:
            outcome = StageOutcome(
                stage="auto-grade",
                status=StageStatus.PAUSED,
                message="the AI can't be reached; nothing graded yet",
                detail=f"ai-unavailable:\n{PROBLEM}",
            )
        else:
            outcome = StageOutcome(stage="auto-grade", status=StageStatus.RAN, message="ok")
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
            await _settle(pilot, lambda: _has(app, "#grade-without-ai-dialog"))
            seen["problem"] = str(app.screen.query_one("#grade-without-ai-problem").render())
            await answer(app, pilot)
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: len(calls) == 2 and screen.extraction_in_progress is False)

    asyncio.run(run())
    return calls, seen


def test_yes_runs_again_grading_without_ai(tmp_path, monkeypatch) -> None:
    async def answer(app, pilot) -> None:  # noqa: ANN001
        app.screen.query_one("#grade-without-ai-yes").press()

    calls, seen = _drive(tmp_path, monkeypatch, answer)

    assert PROBLEM in seen["problem"]
    assert len(calls) == 2
    assert calls[1].grade_without_ai is True and calls[1].auto_grade == calls[0].auto_grade


@pytest.mark.parametrize("how", ["button", "escape"])
def test_no_runs_again_with_auto_grading_off(tmp_path, monkeypatch, how) -> None:
    async def answer(app, pilot) -> None:  # noqa: ANN001
        if how == "button":
            app.screen.query_one("#grade-without-ai-no").press()
        else:
            await pilot.press("escape")

    calls, _seen = _drive(tmp_path, monkeypatch, answer)

    assert len(calls) == 2
    assert calls[1].auto_grade is False and calls[1].grade_without_ai is False
