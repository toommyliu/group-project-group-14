"""Shift+Q: an instructor approves or dismisses graders' change requests."""

from __future__ import annotations

import asyncio

from opengrader.core.workspace.models import ChangeRequest
from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens.change_requests_screen import ChangeRequestsScreen, describe_request

from .fakes import DEFAULT_IMAGE_PATH


def _request(number: int) -> ChangeRequest:
    return ChangeRequest(
        id=number, assignment_id="a", submission_id=12, question_id="q3",
        requested_by="bob", graded_by="alice", created="now", status="pending",
        rubric_labels=["Partially Correct"], points_awarded=None, score_mode="rubric",
        analysis="Shows the work.", current_rubric_labels=["Incorrect"], current_points=0,
    )


def test_a_request_reads_as_who_asks_for_what_against_what_stands() -> None:
    assert describe_request(_request(1)) == (
        'Submission 12 · q3: bob asks for Partially Correct — "Shows the work.". '
        "Now: Incorrect (0 pts), given by alice."
    )


def test_approve_and_dismiss_send_the_decision_and_take_the_row_away() -> None:
    decided: list[tuple[int, bool]] = []
    seen: dict = {}

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            app.push_screen(ChangeRequestsScreen([_request(1), _request(2)], lambda rid, ok: decided.append((rid, ok))))
            await pilot.pause()
            app.screen.query_one("#request-approve-1").press()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.screen.query_one("#request-dismiss-2").press()
            await app.workers.wait_for_complete()
            await pilot.pause()
            seen["rows"] = len(app.screen.query(".request"))
            seen["intro"] = str(app.screen.query_one("#requests-intro").render())

    asyncio.run(run())

    assert decided == [(1, True), (2, False)]
    assert seen["rows"] == 0
    assert seen["intro"] == "No grader is waiting on a change."
