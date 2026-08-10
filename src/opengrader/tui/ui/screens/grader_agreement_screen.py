"""Modal inter-grader agreement view, opened with I (teacher mode only).

A read-only pop-up answering the question a professor running a TA team has and
could not previously ask: *are we marking this the same way?* Where the session
view (``V``) reports how **fast** each grader worked from the audit journal,
this reports how **generously** — and whether either of them drifted over the
afternoon — from the same journal plus the submissions it refers to.

Two things it is careful about, both inherited from the engine
(:mod:`opengrader.core.analytics.grader_agreement`):

* **Leniency is a residual**, not a raw average. The TA handed the hard half of
  the exam has a low mean and may be the fairest person on the team, so every
  figure is measured against how that question was marked overall.
* **It says when it cannot compare.** On a cleanly divided cohort no two graders
  mark comparable work, and the honest answer is to say so and point at
  ``calibrate pack`` — not to print residuals that sit near zero only because
  each grader is their own sole reference.

Because it names graders, it is teacher-mode only, exactly like grade history
(``H``) and the session analytics (``V``). esc or I closes it.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from opengrader.core.analytics import GraderAgreementReport
from opengrader.tui.formatting import (
    format_grader_agreement_overview,
    format_grader_pair,
    format_grader_stats,
)


class GraderAgreementScreen(ModalScreen[None]):
    """Read-only leniency, drift and pairwise-agreement summary per grader."""

    DEFAULT_CSS = """
    GraderAgreementScreen {
        align: center middle;
    }

    #grader-agreement-dialog {
        width: 80%;
        max-width: 110;
        height: 80%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #grader-agreement-overview {
        width: 100%;
        height: auto;
        padding: 0 1 1 1;
        border-bottom: round $surface-lighten-2;
    }

    #grader-agreement-body {
        height: 1fr;
        margin-top: 1;
        scrollbar-size: 1 1;
    }

    #grader-agreement-body .grader-agreement-heading {
        color: $text-muted;
        text-style: bold;
        margin-bottom: 1;
    }

    #grader-agreement-body .grader-agreement-row {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }

    #grader-agreement-body .grader-agreement-legend {
        color: $text-muted;
        margin-bottom: 1;
    }

    #grader-agreement-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("I", "close", "Close"),
    ]

    def __init__(self, report: GraderAgreementReport) -> None:
        self._report = report
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="grader-agreement-dialog") as dialog:
            dialog.border_title = "▸ Grader Agreement"
            yield Static(
                format_grader_agreement_overview(self._report),
                id="grader-agreement-overview",
            )
            with VerticalScroll(id="grader-agreement-body"):
                if self._report.graders:
                    yield Static("PER GRADER", classes="grader-agreement-heading")
                    for grader in self._report.graders:
                        yield Static(
                            format_grader_stats(grader),
                            classes="grader-agreement-row",
                        )
                    yield Static(
                        "[dim]vs others = points per 100 above/below how those "
                        "questions were marked overall · drift = late minus "
                        "early in their own session[/dim]",
                        classes="grader-agreement-legend",
                    )
                # Same-answer overlap is a superset of same-work overlap, so
                # showing both would state the same finding twice, the second
                # time more weakly. Prefer the stronger basis when it exists.
                pairs = self._report.same_work_pairs or self._report.same_answer_pairs
                if pairs:
                    basis = (
                        "SAME STUDENTS' ANSWERS"
                        if self._report.same_work_pairs
                        else "SAME ANSWER, DIFFERENT STUDENTS"
                    )
                    yield Static(
                        f"AGREEMENT — {basis}",
                        classes="grader-agreement-heading",
                    )
                    for pair in pairs:
                        yield Static(
                            format_grader_pair(pair),
                            classes="grader-agreement-row",
                        )
            with Horizontal(id="grader-agreement-buttons"):
                yield Button("Close", id="grader-agreement-close", compact=True)

    def on_mount(self) -> None:
        self.query_one("#grader-agreement-close", Button).focus()

    @on(Button.Pressed, "#grader-agreement-close")
    def _on_close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
