from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Input,
    RadioButton,
    RadioSet,
    Select,
    Static,
)

from opengrader.core.curve import Curve, solve_curve, summarize_curve
from opengrader.tui.formatting import format_curve_effect, format_curve_label
from opengrader.tui.statistics import curve_distribution_chart_image
from opengrader.tui.ui.widgets.sharp_image import SharpImage

_METHODS: tuple[str, ...] = ("none", "bonus", "scale", "sqrt")
_METHOD_HINTS: dict[str, str] = {
    "none": "No curve — raw totals are used as-is.",
    "bonus": "Value = points added to every total (may be negative).",
    "scale": "Value = multiplier (e.g. 1.1 raises every total by 10%).",
    "sqrt": "new% = √(raw%). Lifts low scores more than high ones. Value ignored.",
}


class CurveScreen(ModalScreen["Curve | None"]):
    DEFAULT_CSS = """
    CurveScreen {
        align: center middle;
    }

    #curve-dialog {
        width: 84%;
        max-width: 120;
        height: 86%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #curve-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #curve-controls {
        height: auto;
        margin-top: 1;
    }

    #curve-method {
        width: 40;
        height: auto;
        border: round $surface-lighten-2;
    }

    #curve-controls-right {
        width: 1fr;
        height: auto;
        margin-left: 2;
    }

    CurveScreen .curve-field-label {
        width: 100%;
        height: auto;
        margin-top: 1;
        text-style: bold;
    }

    #curve-method-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #curve-target-row {
        height: auto;
        margin-top: 1;
    }

    #curve-target-stat {
        width: 16;
    }

    #curve-target-value {
        width: 1fr;
        margin-left: 1;
    }

    #curve-target-row Button {
        margin-left: 1;
    }

    #curve-target-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #curve-body {
        height: 1fr;
        margin-top: 1;
        scrollbar-size: 1 1;
    }

    #curve-effect {
        width: 100%;
        height: auto;
        padding: 1 0;
    }

    #curve-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #curve-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
    ]

    def __init__(
        self,
        curve: Curve,
        distribution: list[tuple[float, float | None]],
    ) -> None:
        self._initial = curve
        self._distribution = distribution
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="curve-dialog") as dialog:
            dialog.border_title = "▸ Score Curve"
            yield Static(
                "Adjust the curve and watch raw → curved shift. Saving writes "
                "curve.json; it never edits a grade and Clear restores raw totals.",
                id="curve-hint",
            )
            with Horizontal(id="curve-controls"):
                with RadioSet(id="curve-method"):
                    yield RadioButton("No curve", value=self._initial.method == "none")
                    yield RadioButton(
                        "Add points (bonus)", value=self._initial.method == "bonus"
                    )
                    yield RadioButton(
                        "Scale (×)", value=self._initial.method == "scale"
                    )
                    yield RadioButton(
                        "Square-root curve", value=self._initial.method == "sqrt"
                    )
                with Vertical(id="curve-controls-right"):
                    yield Static("Value", classes="curve-field-label")
                    yield Input(
                        value=f"{self._initial.value:g}",
                        placeholder="e.g. 5 or 1.1",
                        id="curve-value",
                    )
                    yield Static(
                        _METHOD_HINTS[self._initial.method], id="curve-method-hint"
                    )
                    yield Checkbox(
                        "Cap at max (no extra credit past 100%)",
                        value=self._initial.cap_at_max,
                        id="curve-cap",
                    )
                    yield Static("Solve for a target", classes="curve-field-label")
                    with Horizontal(id="curve-target-row"):
                        yield Select(
                            [("Mean", "mean"), ("Median", "median")],
                            value="mean",
                            allow_blank=False,
                            id="curve-target-stat",
                        )
                        yield Input(
                            placeholder="e.g. 78",
                            id="curve-target-value",
                        )
                        yield Button("Solve", id="curve-solve", compact=True)
                    yield Static(
                        "Pick Add points or Scale, type a target class average "
                        "(%), and Solve to set the value.",
                        id="curve-target-hint",
                    )
            with VerticalScroll(id="curve-body"):
                yield SharpImage(
                    None,
                    id="curve-chart",
                    fit_width_fraction=0.94,
                    allow_upscale=True,
                    max_cell_height=18,
                )
                yield Static("", id="curve-effect")
            with Horizontal(id="curve-buttons"):
                yield Button("Save", variant="primary", id="curve-save", compact=True)
                yield Button("Clear curve", id="curve-clear", compact=True)
                yield Button("Close", id="curve-close", compact=True)

    def on_mount(self) -> None:
        self._refresh_preview()


    def _selected_method(self) -> str:
        index = self.query_one("#curve-method", RadioSet).pressed_index
        if 0 <= index < len(_METHODS):
            return _METHODS[index]
        return "none"

    def _current_curve(self) -> Curve:
        method = self._selected_method()
        raw = self.query_one("#curve-value", Input).value.strip()
        default = 1.0 if method == "scale" else 0.0
        try:
            value = float(raw) if raw else default
        except ValueError:
            value = default
        cap = self.query_one("#curve-cap", Checkbox).value
        return Curve(method=method, value=value, cap_at_max=cap)


    def _refresh_preview(self) -> None:
        curve = self._current_curve()
        self.query_one("#curve-method-hint", Static).update(_METHOD_HINTS[curve.method])

        effect = summarize_curve(self._distribution, curve)
        header = f"[b]Curve:[/b] {format_curve_label(curve)}\n"
        self.query_one("#curve-effect", Static).update(
            header + format_curve_effect(effect)
        )

        chart = self.query_one("#curve-chart", SharpImage)
        image = curve_distribution_chart_image(self._distribution, curve)
        if image is not None:
            chart.image = image
            chart.display = True
        else:
            chart.display = False

    @on(RadioSet.Changed, "#curve-method")
    def _on_method_changed(self, event: RadioSet.Changed) -> None:
        event.stop()
        self._refresh_preview()

    @on(Input.Changed, "#curve-value")
    def _on_value_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._refresh_preview()

    @on(Checkbox.Changed, "#curve-cap")
    def _on_cap_changed(self, event: Checkbox.Changed) -> None:
        event.stop()
        self._refresh_preview()

    @on(Button.Pressed, "#curve-solve")
    def _on_solve(self, event: Button.Pressed) -> None:
        event.stop()
        method = self._selected_method()
        if method not in ("bonus", "scale"):
            self.app.notify(
                "Pick Add points or Scale to solve for a target average.",
                severity="warning",
            )
            return

        raw = self.query_one("#curve-target-value", Input).value.strip()
        try:
            target = float(raw)
        except ValueError:
            self.app.notify(
                "Enter a target class average as a percentage.", severity="error"
            )
            return

        statistic = str(self.query_one("#curve-target-stat", Select).value)
        cap = self.query_one("#curve-cap", Checkbox).value
        curve = solve_curve(
            self._distribution,
            method,
            target,
            statistic=statistic,  # type: ignore[arg-type]
            cap_at_max=cap,
        )
        if curve is None:
            self.app.notify(
                "Couldn't reach that average with this method. Try the other "
                "method, uncap the max, or a target closer to the current one.",
                severity="warning",
            )
            return

        self.query_one("#curve-value", Input).value = f"{curve.value:g}"
        self.app.notify(
            f"Solved: {method} {curve.value:g} for a {statistic} of {target:g}%."
        )


    @on(Button.Pressed, "#curve-save")
    def _on_save(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(self._current_curve())

    @on(Button.Pressed, "#curve-clear")
    def _on_clear(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(Curve())

    @on(Button.Pressed, "#curve-close")
    def _on_close(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
