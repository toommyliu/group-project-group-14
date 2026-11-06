"""Ask once, on opening, whether to match papers to the class roster now.

Shown when the class folder (or workspace) has a roster and at least one
paper has no student ID or one not on it (`core/submissions/roster_check`).
Matching can wait — grading is anonymous anyway — but a grade cannot go back
to a student it is not matched to, so the release check stops until then.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from opengrader.tui.ui import button_roles

MATCH_IN_BROWSER = "browser"
MATCH_HERE = "here"


class MatchRosterPrompt(ModalScreen["str | None"]):
    """Returns `MATCH_IN_BROWSER`, `MATCH_HERE`, or None for later."""

    DEFAULT_CSS = """
    MatchRosterPrompt {
        align: center middle;
    }

    #match-roster-dialog {
        width: 70%;
        max-width: 84;
        height: auto;
        background: $surface;
        border: round $warning;
        border-title-align: left;
        border-title-color: $warning;
        border-title-style: bold;
        padding: 1 2;
    }

    #match-roster-summary {
        width: 100%;
        height: auto;
    }

    #match-roster-hint {
        width: 100%;
        height: auto;
        margin-top: 1;
        color: $text-muted;
    }

    #match-roster-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #match-roster-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "later", "Later")]

    def __init__(
        self,
        summary: str,
        *,
        browser_hint: str | None,
        here_hint: str | None,
    ) -> None:
        """`browser_hint` / `here_hint` say what each choice does; None
        leaves that choice out."""
        self._summary = summary
        self._browser_hint = browser_hint
        self._here_hint = here_hint
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="match-roster-dialog") as dialog:
            dialog.border_title = "▸ Match papers to the roster?"
            yield Static(
                f"{self._summary} Grading can start without it, but a grade "
                "can only be returned to a student it is matched to.",
                id="match-roster-summary",
            )
            hints = [hint for hint in (self._browser_hint, self._here_hint) if hint]
            if hints:
                yield Static("\n".join(hints), id="match-roster-hint", markup=False)
            with Horizontal(id="match-roster-buttons"):
                if self._browser_hint is not None:
                    yield Button(
                        "Match in browser",
                        id="match-roster-browser",
                        classes=button_roles.BROWSER,
                        compact=True,
                    )
                if self._here_hint is not None:
                    yield Button(
                        "Match here",
                        id="match-roster-here",
                        variant="success",
                        compact=True,
                    )
                yield Button("Later", id="match-roster-later", compact=True)

    def on_mount(self) -> None:
        first = "#match-roster-here" if self._here_hint is not None else "#match-roster-browser"
        if self._here_hint is None and self._browser_hint is None:
            first = "#match-roster-later"
        self.query_one(first, Button).focus()

    @on(Button.Pressed, "#match-roster-browser")
    def _browser(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(MATCH_IN_BROWSER)

    @on(Button.Pressed, "#match-roster-here")
    def _here(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(MATCH_HERE)

    @on(Button.Pressed, "#match-roster-later")
    def _later(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_later(self) -> None:
        self.dismiss(None)
