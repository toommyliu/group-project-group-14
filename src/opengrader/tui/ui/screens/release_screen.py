from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Static

from opengrader.core.release import (
    GradeRelease,
    ReadinessReport,
    ReleaseComparison,
)
from opengrader.tui.formatting import (
    format_readiness_issue,
    format_readiness_summary,
    format_release_change,
    format_release_comparison_summary,
    format_release_row,
    format_release_total_change,
)

VIEW_READINESS = "readiness"
VIEW_CHANGES = "changes"

MAX_LISTED_CHANGES = 40


class ReleaseScreen(ModalScreen[str | None]):
    DEFAULT_CSS = """
    ReleaseScreen {
        align: center middle;
    }

    #release-dialog {
        width: 84%;
        max-width: 120;
        height: 84%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #release-summary {
        width: 100%;
        height: auto;
    }

    #release-previous {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #release-body {
        height: 1fr;
        margin-top: 1;
        border: round $surface-lighten-2;
        border-title-align: left;
        border-title-color: $text-muted;
        padding: 0 1;
    }

    ReleaseScreen .release-section {
        width: 100%;
        height: auto;
        margin-top: 1;
        text-style: bold;
        color: $text-muted;
    }

    ReleaseScreen .release-issue,
    ReleaseScreen .release-change {
        width: 100%;
        height: auto;
        margin-top: 1;
    }

    ReleaseScreen .release-empty {
        width: 100%;
        height: auto;
        margin-top: 1;
        color: $text-muted;
    }
    #release-controls {
        width: 100%;
        height: auto;
    }

    ReleaseScreen .field-label {
        width: 100%;
        height: auto;
        margin-top: 1;
    }

    ReleaseScreen Checkbox {
        background: transparent;
        border: none;
        padding: 0;
        margin-top: 1;
    }

    #release-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #release-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("d", "show_changes", "What changed", show=False),
        Binding("s", "show_readiness", "Readiness", show=False),
    ]

    def __init__(
        self,
        report: ReadinessReport,
        *,
        previous: GradeRelease | None = None,
        comparison: ReleaseComparison | None = None,
        releases: list[GradeRelease] | None = None,
        teacher_mode: bool = False,
        on_release=None,
    ) -> None:
        super().__init__()
        self._report = report
        self._previous = previous
        self._comparison = comparison
        self._releases = releases or []
        self._teacher_mode = teacher_mode
        # Naming this _on_release would collide with the decorated handler Textual resolves by name.
        self._release_callback = on_release
        self._view = VIEW_READINESS
        self._released_id: str | None = None


    def compose(self) -> ComposeResult:
        with Vertical(id="release-dialog") as dialog:
            dialog.border_title = "▸ Release grades"
            yield Static(format_readiness_summary(self._report), id="release-summary")
            yield Static(self._previous_line(), id="release-previous")
            body = VerticalScroll(id="release-body")
            body.border_title = "Readiness"
            yield body
            with Vertical(id="release-controls"):
                yield Static(
                    "[b]Label[/b]  [dim]optional — what this hand-back was[/dim]",
                    classes="field-label",
                )
                yield Input(
                    placeholder="e.g. returned in class", id="release-label"
                )
                yield Checkbox(
                    "Release anyway, over the blocking issues",
                    value=False,
                    id="release-force",
                    classes="" if self._report.blocking else "hidden",
                )
            with Horizontal(id="release-buttons"):
                yield Button(
                    "What changed (d)", id="release-toggle-view", compact=True
                )
                yield Button(
                    "Release now",
                    variant="primary",
                    id="release-confirm",
                    compact=True,
                    disabled=not self._can_release(),
                )
                yield Button("Close", id="release-close", compact=True)

    def on_mount(self) -> None:
        self._render_body()

    def _previous_line(self) -> str:
        if self._previous is None:
            return (
                "[dim]Nothing has been released from this folder yet. Recording "
                "a release changes no grade — it only remembers what each "
                "student was handed.[/dim]"
            )
        label = f" — {self._previous.label}" if self._previous.label else ""
        return (
            f"[dim]Last released {self._previous.created_at[:16].replace('T', ' ')} "
            f"({self._previous.release_id}){label}[/dim]"
        )


    def _render_body(self) -> None:
        body = self.query_one("#release-body", VerticalScroll)
        body.remove_children()
        if self._view == VIEW_CHANGES:
            body.border_title = "What changed since the last release"
            self._render_changes(body)
        else:
            body.border_title = "Readiness"
            self._render_readiness(body)
        body.scroll_home(animate=False)

    def _render_readiness(self, body: VerticalScroll) -> None:
        report = self._report
        groups = (
            ("Blocking — fix before releasing", report.blocking),
            ("Worth a decision", report.warnings),
            ("For context", report.notes),
        )
        if not report.issues:
            body.mount(
                Static(
                    "Everything is graded and nothing needs attention.",
                    classes="release-empty",
                )
            )
            return
        for title, issues in groups:
            if not issues:
                continue
            body.mount(Static(title, classes="release-section"))
            for issue in issues:
                body.mount(
                    Static(format_readiness_issue(issue), classes="release-issue")
                )

    def _render_changes(self, body: VerticalScroll) -> None:
        if self._previous is None:
            body.mount(
                Static(
                    "Nothing has been released yet, so there is nothing to "
                    "compare against.",
                    classes="release-empty",
                )
            )
            return
        comparison = self._comparison
        if comparison is None:
            body.mount(
                Static("No comparison available.", classes="release-empty")
            )
            return

        body.mount(
            Static(
                format_release_comparison_summary(comparison),
                classes="release-issue",
            )
        )

        if comparison.missing_students:
            body.mount(
                Static(
                    f"{len(comparison.missing_students)} released student(s) have "
                    "no submission in this folder now.",
                    classes="release-empty",
                )
            )
        if comparison.new_students:
            body.mount(
                Static(
                    f"{len(comparison.new_students)} student(s) here were not in "
                    "the release.",
                    classes="release-empty",
                )
            )

        if comparison.changes:
            body.mount(Static("Question grades", classes="release-section"))
            for change in comparison.changes[:MAX_LISTED_CHANGES]:
                body.mount(
                    Static(
                        format_release_change(
                            change, teacher_mode=self._teacher_mode
                        ),
                        classes="release-change",
                    )
                )
            extra = len(comparison.changes) - MAX_LISTED_CHANGES
            if extra > 0:
                body.mount(
                    Static(
                        f"…and {extra} more. Run 'grade-release diff' for the "
                        "whole list.",
                        classes="release-empty",
                    )
                )

        if comparison.totals:
            body.mount(
                Static("Totals that differ from what was shown", classes="release-section")
            )
            for total in comparison.totals[:MAX_LISTED_CHANGES]:
                body.mount(
                    Static(
                        format_release_total_change(
                            total, teacher_mode=self._teacher_mode
                        ),
                        classes="release-change",
                    )
                )


    def _can_release(self) -> bool:
        if not self._report.blocking:
            return True
        if not self.is_mounted:
            return False
        return self.query_one("#release-force", Checkbox).value

    def _sync_release_button(self) -> None:
        self.query_one("#release-confirm", Button).disabled = not self._can_release()

    @on(Checkbox.Changed, "#release-force")
    def _on_force_toggled(self, event: Checkbox.Changed) -> None:
        event.stop()
        self._sync_release_button()

    @on(Button.Pressed, "#release-toggle-view")
    def _on_toggle_view(self, event: Button.Pressed) -> None:
        event.stop()
        self._set_view(
            VIEW_READINESS if self._view == VIEW_CHANGES else VIEW_CHANGES
        )

    def action_show_changes(self) -> None:
        self._set_view(VIEW_CHANGES)

    def action_show_readiness(self) -> None:
        self._set_view(VIEW_READINESS)

    def _set_view(self, view: str) -> None:
        if view == self._view:
            return
        self._view = view
        self.query_one("#release-toggle-view", Button).label = (
            "Readiness (s)" if view == VIEW_CHANGES else "What changed (d)"
        )
        self._render_body()

    @on(Button.Pressed, "#release-confirm")
    def _on_release_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if not self._can_release() or self._release_callback is None:
            return
        label = self.query_one("#release-label", Input).value.strip()
        try:
            release = self._release_callback(label)
        except Exception as exc:  # noqa: BLE001
            self.app.notify(f"Could not record the release: {exc}", severity="error")
            return
        self._released_id = getattr(release, "release_id", None)
        self.dismiss(self._released_id)

    @on(Button.Pressed, "#release-close")
    def _on_close(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
