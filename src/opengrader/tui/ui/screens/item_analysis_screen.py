from __future__ import annotations

from typing import Callable

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from opengrader.core.analytics import ItemAnalysisReport, OutcomeReport
from opengrader.tui.formatting import (
    format_item_difficulty,
    format_item_distractor,
    format_item_headline,
    format_item_separation,
    format_outcome_coverage,
    format_outcome_headline,
    format_outcome_mastery,
    format_outcome_summary,
)
from opengrader.tui.statistics import item_analysis_overview_chart_image
from opengrader.tui.ui.widgets.sharp_image import SharpImage


class ItemAnalysisScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    ItemAnalysisScreen {
        align: center middle;
    }

    #item-analysis-dialog {
        width: 84%;
        max-width: 120;
        height: 88%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #item-analysis-summary {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #item-analysis-outcome-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
        text-style: italic;
    }

    #item-analysis-body {
        height: 1fr;
        margin-top: 1;
        scrollbar-size: 1 1;
    }

    #item-analysis-chart {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }

    ItemAnalysisScreen .item-card {
        width: 100%;
        height: auto;
        border: round $surface-lighten-2;
        border-title-align: left;
        padding: 0 1;
        margin-bottom: 1;
    }

    ItemAnalysisScreen .item-card.flagged {
        border: round #BF616A;
    }

    ItemAnalysisScreen .item-metric {
        width: 100%;
        height: auto;
    }

    ItemAnalysisScreen .item-distractor-title {
        width: 100%;
        height: auto;
        color: $text-muted;
        margin-top: 1;
    }

    ItemAnalysisScreen .item-distractor {
        width: 100%;
        height: auto;
    }

    ItemAnalysisScreen .item-analysis-empty {
        color: $text-muted;
        padding: 1 2;
    }

    #item-analysis-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("s", "close", "Close"),
        Binding("f", "cycle_section", "Section", show=False),
        Binding("o", "toggle_view", "Outcomes", show=False),
    ]

    VIEW_QUESTIONS = "questions"
    VIEW_OUTCOMES = "outcomes"

    def __init__(
        self,
        report: ItemAnalysisReport,
        *,
        report_builder: Callable[[str | None], ItemAnalysisReport] | None = None,
        sections: tuple[str, ...] = (),
        outcome_report: OutcomeReport | None = None,
        outcome_builder: Callable[[str | None], OutcomeReport | None] | None = None,
    ) -> None:
        self._report = report
        self._report_builder = report_builder
        self._outcome_report = outcome_report
        self._outcome_builder = outcome_builder
        self._view = self.VIEW_QUESTIONS
        self._scopes: tuple[str | None, ...] = (
            (None, *sections) if report_builder and sections else (None,)
        )
        self._scope_index = 0
        super().__init__()

    @property
    def _current_scope(self) -> str | None:
        return self._scopes[self._scope_index]

    @property
    def _has_outcomes(self) -> bool:
        return self._outcome_report is not None

    def compose(self) -> ComposeResult:
        with Vertical(id="item-analysis-dialog") as dialog:
            dialog.border_title = "▸ Item Analysis"
            yield Static(self._summary_text(), id="item-analysis-summary")
            yield Static("", id="item-analysis-outcome-hint")
            with VerticalScroll(id="item-analysis-body"):
                yield SharpImage(
                    None,
                    id="item-analysis-chart",
                    fit_width_fraction=0.92,
                    allow_upscale=True,
                    max_cell_height=18,
                )
                yield Vertical(id="item-analysis-cards")
            with Horizontal(id="item-analysis-buttons"):
                yield Button("Close", id="item-analysis-close", compact=True)

    def on_mount(self) -> None:
        self._render_report(rebuild=False)
        self.query_one("#item-analysis-close", Button).focus()

    def _render_report(self, *, rebuild: bool) -> None:
        if rebuild:
            if self._report_builder is not None:
                self._report = self._report_builder(self._current_scope)
            if self._outcome_builder is not None:
                self._outcome_report = self._outcome_builder(self._current_scope)

        self.query_one("#item-analysis-summary", Static).update(self._summary_text())
        dialog = self.query_one("#item-analysis-dialog", Vertical)
        dialog.border_title = (
            "▸ Item Analysis — Learning Outcomes"
            if self._view == self.VIEW_OUTCOMES
            else "▸ Item Analysis"
        )

        self._update_outcome_hint()

        cards = self.query_one("#item-analysis-cards", Vertical)
        cards.remove_children()

        if self._view == self.VIEW_OUTCOMES:
            self.query_one("#item-analysis-chart", SharpImage).display = False
            self._render_outcome_cards(cards)
            return

        report = self._report
        chart = self.query_one("#item-analysis-chart", SharpImage)
        chart_image = (
            item_analysis_overview_chart_image(report) if report.items else None
        )
        if chart_image is not None:
            chart.image = chart_image
            chart.display = True
        else:
            chart.display = False

        if not report.items:
            cards.mount(
                Label(
                    "No graded questions in this view — grade some responses first.",
                    classes="item-analysis-empty",
                )
            )
        else:
            cards.mount(*[self._card_widget(item) for item in report.items])

    def _update_outcome_hint(self) -> None:
        hint = self.query_one("#item-analysis-outcome-hint", Static)
        show = not self._has_outcomes and bool(self._report.items)
        hint.display = show
        if show:
            hint.update(
                "Tag questions with Outcome: in your answer key to see "
                "per-outcome mastery here."
            )

    def _render_outcome_cards(self, cards: Vertical) -> None:
        report = self._outcome_report
        if report is None or not report.outcomes:
            cards.mount(
                Label(
                    "No learning outcomes measured in this view yet. Tag "
                    "questions with **Outcome** bullets in the answer key, then "
                    "grade some responses.",
                    classes="item-analysis-empty",
                )
            )
            return
        cards.mount(*[self._outcome_card_widget(o) for o in report.outcomes])

    def _outcome_card_widget(self, outcome) -> Vertical:  # noqa: ANN001
        labels = [
            Label(format_outcome_mastery(outcome), classes="item-metric"),
            Label(format_outcome_coverage(outcome), classes="item-metric"),
        ]
        rate = outcome.mastery_rate
        classes = (
            "item-card flagged"
            if rate is not None and rate < 0.5
            else "item-card"
        )
        card = Vertical(*labels, classes=classes)
        card.border_title = format_outcome_headline(outcome)
        return card

    def _card_widget(self, item) -> Vertical:
        labels: list[Label] = [
            Label(format_item_difficulty(item), classes="item-metric"),
            Label(format_item_separation(item), classes="item-metric"),
        ]
        if item.distractors:
            labels.append(Label("Choices", classes="item-distractor-title"))
            max_count = max(d.count for d in item.distractors)
            for distractor in item.distractors:
                labels.append(
                    Label(
                        format_item_distractor(distractor, max_count=max_count),
                        classes="item-distractor",
                    )
                )
        classes = "item-card flagged" if item.needs_attention else "item-card"
        card = Vertical(*labels, classes=classes)
        card.border_title = format_item_headline(item)
        return card

    def _scope_label(self) -> str:
        scope = self._current_scope
        return "All sections" if scope is None else f"Section {scope}"

    def _summary_text(self) -> str:
        has_sections = len(self._scopes) > 1
        scope_note = f"[{self._scope_label()}]  " if has_sections else ""
        cycle_note = "  f cycles section." if has_sections else ""
        view_note = (
            "  o shows questions." if self._view == self.VIEW_OUTCOMES
            else ("  o shows outcomes." if self._has_outcomes else "")
        )

        if self._view == self.VIEW_OUTCOMES:
            outcome_report = self._outcome_report
            body = (
                format_outcome_summary(outcome_report)
                if outcome_report is not None
                else "No learning outcomes tagged."
            )
            return f"{scope_note}{body}{view_note}{cycle_note}  esc closes."

        report = self._report
        if not report.items:
            return (
                f"{scope_note}Nothing graded in this view — item analysis appears "
                f"once responses are scored.{view_note}{cycle_note}  esc closes."
            )
        flagged = report.flagged_count
        flag_note = (
            f"  {flagged} flagged for attention (⚠)." if flagged else "  none flagged."
        )
        return (
            f"{scope_note}{len(report.items)} question(s) over "
            f"{report.class_size} submission(s).{flag_note}{view_note}"
            f"{cycle_note}  esc closes."
        )

    def action_cycle_section(self) -> None:
        if len(self._scopes) <= 1:
            return
        self._scope_index = (self._scope_index + 1) % len(self._scopes)
        self._render_report(rebuild=True)

    def action_toggle_view(self) -> None:
        if not self._has_outcomes:
            return
        self._view = (
            self.VIEW_OUTCOMES
            if self._view == self.VIEW_QUESTIONS
            else self.VIEW_QUESTIONS
        )
        self._render_report(rebuild=False)

    @on(Button.Pressed, "#item-analysis-close")
    def _on_close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
