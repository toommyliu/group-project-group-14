"""Changes graders asked for to grades someone else gave (`Q`, instructors).

In a workspace shared with other accounts, a grader who changes a grade
another person gave does not change it: the change is kept here, and an
instructor approves it (the grade is saved, recorded under the instructor)
or dismisses it. Submissions are named by number, never by student: grading
is anonymous by default.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from opengrader.tui.ui.button_roles import OutlineButtons

if TYPE_CHECKING:  # pragma: no cover
    from opengrader.core.workspace.models import ChangeRequest


def _grade(labels: Sequence[str], points: float | None) -> str:
    shown = ", ".join(labels) if labels else "no rubric item"
    return f"{shown} ({'—' if points is None else f'{points:g}'} pts)"


def describe_request(request: "ChangeRequest") -> str:
    proposed = (
        f"{request.points_awarded:g} pts (score)"
        if request.score_mode == "manual" and request.points_awarded is not None
        else ", ".join(request.rubric_labels) or "no rubric item"
    )
    comment = f' — "{request.analysis.strip()}"' if request.analysis.strip() else ""
    return (
        f"Submission {request.submission_id} · {request.question_id}: "
        f"{request.requested_by} asks for {proposed}{comment}. Now: "
        f"{_grade(request.current_rubric_labels, request.current_points)}, "
        f"given by {request.graded_by}."
    )


class ChangeRequestsScreen(OutlineButtons, ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close")]

    DEFAULT_CSS = """
    ChangeRequestsScreen {
        align: center middle;
    }

    #requests-dialog {
        width: 84%;
        max-width: 110;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-style: bold;
        padding: 1 2;
    }

    #requests-intro, .request-text {
        width: 100%;
        height: auto;
    }

    .request {
        height: auto;
        margin-top: 1;
    }

    .request-buttons {
        height: auto;
        align-horizontal: right;
    }

    .request-buttons Button {
        margin-left: 1;
    }

    #requests-close-row {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    def __init__(
        self,
        requests: Sequence["ChangeRequest"],
        decide: Callable[[int, bool], None],
    ) -> None:
        super().__init__()
        self._requests = {request.id: request for request in requests}
        self._decide = decide

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="requests-dialog") as dialog:
            dialog.border_title = "▸ Change requests"
            yield Static(self._intro(), id="requests-intro", markup=False)
            for request in self._requests.values():
                with Vertical(classes="request", id=f"request-{request.id}"):
                    yield Static(describe_request(request), classes="request-text", markup=False)
                    with Horizontal(classes="request-buttons"):
                        yield Button("Dismiss", id=f"request-dismiss-{request.id}")
                        yield Button("Approve", id=f"request-approve-{request.id}", variant="primary")
            with Horizontal(id="requests-close-row"):
                yield Button("Close", id="requests-close")

    def _intro(self) -> str:
        if not self._requests:
            return "No grader is waiting on a change."
        return (
            f"{len(self._requests)} change(s) graders asked for to a grade someone "
            "else gave. Approve saves the grade they asked for, recorded under you; "
            "Dismiss keeps the grade as it is."
        )

    @on(Button.Pressed, ".request-buttons Button")
    def _on_decision(self, event: Button.Pressed) -> None:
        event.stop()
        _, decision, number = (event.button.id or "").split("-", 2)
        self._send(int(number), decision == "approve")

    @work(thread=True, exclusive=True, group="change-request")
    def _send(self, request_id: int, approve: bool) -> None:
        try:
            self._decide(request_id, approve)
        except Exception as exc:  # noqa: BLE001 - shown, the request stays listed
            self.app.call_from_thread(
                self.app.notify, f"Not done: {exc}", severity="error", timeout=20
            )
            return
        self.app.call_from_thread(self._settled, request_id, approve)

    def _settled(self, request_id: int, approve: bool) -> None:
        request = self._requests.pop(request_id, None)
        self.query_one(f"#request-{request_id}").remove()
        self.query_one("#requests-intro", Static).update(self._intro())
        if request is not None:
            self.app.notify(
                f"{'Approved' if approve else 'Dismissed'} {request.requested_by}'s "
                f"change to submission {request.submission_id} · {request.question_id}."
            )

    @on(Button.Pressed, "#requests-close")
    def _on_close(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
