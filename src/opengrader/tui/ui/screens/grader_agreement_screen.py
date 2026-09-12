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
