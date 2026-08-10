"""Modal multi-grader work claims, opened with Shift+D.

Who is grading what. The anonymity lock makes a TA team fair; claims
(`core/claims.py`) make it *divided*, so nobody marks a paper someone else
already did. This pop-up is the in-app view of that division:

* your own slices at the top, then everyone else's, then the coverage line;
* **claim** a run of questions (``c``) by typing it into the field — the same
  ``q1-q3`` syntax the ``claim`` CLI takes;
* **release** (``r``) a slice back to the pool, or mark it **done** (``d``);
* toggle the navigation filter (``a``) between "only my slice" and the whole
  cohort.

Every action dismisses with a :class:`ClaimAction`; ``TuiApp`` performs it
against the append-only log and reopens the refreshed modal, mirroring
`AssignIdentityScreen` and the regrade queue. Nothing here can block a save — a
claim is coordination, not permission, so the worst an overlap does is warn.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from opengrader.core.claims import WorkClaim, WorkSlice, coverage_report

ClaimActionKind = Literal["claim", "release", "complete", "toggle_filter"]


@dataclass(frozen=True)
class ClaimAction:
    """What the grader asked for, returned on dismiss."""

    kind: ClaimActionKind
    claim_id: str = ""
    work: WorkSlice | None = None
    note: str = ""


class _ClaimRow(Static):
    """A focusable claim row: r releases it, d marks it done."""

    can_focus = True

    def __init__(self, claim: WorkClaim, label: str, *, mine: bool) -> None:
        self._claim = claim
        self._mine = mine
        super().__init__(label, classes="claim-row")

    def _post(self, kind: ClaimActionKind) -> None:
        # Releasing or completing someone else's slice is not this grader's
        # call — the row is still listed, just not actionable.
        if not self._mine:
            return
        self.post_message(
            ClaimScreen.ActionChosen(ClaimAction(kind=kind, claim_id=self._claim.claim_id))
        )

    def on_key(self, event: events.Key) -> None:
        # The focused row is first in the bubble path, so intercepting the nav
        # keys here keeps the enclosing VerticalScroll from hijacking them.
        if event.key == "r":
            event.stop()
            self._post("release")
        elif event.key == "d":
            event.stop()
            self._post("complete")
        elif event.key in ("down", "j"):
            event.stop()
            self.screen.focus_relative_row(self, 1)
        elif event.key in ("up", "k"):
            event.stop()
            self.screen.focus_relative_row(self, -1)


class ClaimScreen(ModalScreen["ClaimAction | None"]):
    """Who holds which slice of the grading, and how to change it."""

    DEFAULT_CSS = """
    ClaimScreen {
        align: center middle;
    }

    #claim-dialog {
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

    #claim-summary {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #claim-body {
        height: 1fr;
        margin-top: 1;
        border: round $surface-lighten-2;
        padding: 0 1;
        scrollbar-size: 1 1;
    }

    ClaimScreen .claim-section {
        width: 100%;
        height: auto;
        color: $text-muted;
        text-style: bold;
        margin-top: 1;
    }

    ClaimScreen .claim-row {
        width: 100%;
        height: auto;
        color: $accent;
    }

    ClaimScreen .claim-row:hover {
        background: $boost;
    }

    ClaimScreen .claim-row:focus {
        background: $accent 20%;
        text-style: bold;
    }

    ClaimScreen .claim-theirs {
        color: $text-muted;
    }

    ClaimScreen .claim-empty {
        color: $text-muted;
        padding: 1 2;
    }

    ClaimScreen .claim-warning {
        color: $warning;
        width: 100%;
        height: auto;
    }

    #claim-new-row {
        height: auto;
        margin-top: 1;
    }

    #claim-new-questions {
        width: 1fr;
    }

    #claim-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("D", "close", "Close"),
        Binding("c", "focus_new", "Claim", show=False),
        Binding("a", "toggle_filter", "All work", show=False),
    ]

    class ActionChosen(Message):
        def __init__(self, action: ClaimAction) -> None:
            self.action = action
            super().__init__()

    def __init__(
        self,
        *,
        claims: list[WorkClaim],
        actor: str,
        question_ids: list[str],
        student_ids: list[str],
        filter_enabled: bool,
    ) -> None:
        self._claims = claims
        self._actor = actor
        self._question_ids = question_ids
        self._student_ids = student_ids
        self._filter_enabled = filter_enabled
        super().__init__()

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #

    def compose(self) -> ComposeResult:
        mine = [claim for claim in self._claims if claim.actor == self._actor]
        theirs = [claim for claim in self._claims if claim.actor != self._actor]

        with Vertical(id="claim-dialog") as dialog:
            dialog.border_title = "▸ Work Claims"
            yield Static(self._summary_text(), id="claim-summary")
            with VerticalScroll(id="claim-body"):
                if not self._claims:
                    yield Label(
                        "Nobody has claimed anything — every grader sees the "
                        "whole assignment.",
                        classes="claim-empty",
                    )
                if mine:
                    yield Static("YOURS", classes="claim-section")
                    for claim in mine:
                        yield _ClaimRow(claim, self._row_label(claim), mine=True)
                if theirs:
                    yield Static("OTHER GRADERS", classes="claim-section")
                    for claim in theirs:
                        row = _ClaimRow(claim, self._row_label(claim), mine=False)
                        row.add_class("claim-theirs")
                        yield row
                for warning in self._overlap_warnings():
                    yield Label(warning, classes="claim-warning")
            with Horizontal(id="claim-new-row"):
                yield Input(
                    placeholder="Claim questions, e.g. q1-q3 or q1,q2,q7",
                    id="claim-new-questions",
                )
                yield Button("Claim", id="claim-add", variant="primary", compact=True)
            with Horizontal(id="claim-buttons"):
                yield Button(self._filter_label(), id="claim-filter", compact=True)
                yield Button("Close", id="claim-close", compact=True)

    def on_mount(self) -> None:
        rows = list(self.query(_ClaimRow))
        if rows:
            rows[0].focus()
        else:
            self.query_one("#claim-new-questions", Input).focus()

    def focus_relative_row(self, current: _ClaimRow, delta: int) -> None:
        rows = list(self.query(_ClaimRow))
        if current not in rows:
            return
        target = rows.index(current) + delta
        if 0 <= target < len(rows):
            rows[target].focus()
            rows[target].scroll_visible()

    # ------------------------------------------------------------------ #
    # Text
    # ------------------------------------------------------------------ #

    def _row_label(self, claim: WorkClaim) -> str:
        who = "you" if claim.actor == self._actor else claim.actor
        note = f"  — {claim.note}" if claim.note else ""
        return f"  {who:<14} {claim.work.describe()}{note}"

    def _filter_label(self) -> str:
        return "Showing: my slice" if self._filter_enabled else "Showing: all work"

    def _summary_text(self) -> str:
        report = coverage_report(
            self._claims,
            question_ids=self._question_ids,
            student_ids=self._student_ids,
        )
        if report.total_cells:
            percent = 100.0 * report.claimed_cells / report.total_cells
            coverage = (
                f"{report.claimed_cells}/{report.total_cells} answers claimed "
                f"({percent:.0f}%)"
            )
        else:
            coverage = "no class loaded"
        return (
            f"{coverage}.  ↑↓/jk move · c claims · r releases · d marks done · "
            "a toggles my-slice/all · esc closes."
        )

    def _overlap_warnings(self) -> list[str]:
        """One line per double-booked pair — the state claims exist to prevent."""
        report = coverage_report(
            self._claims,
            question_ids=self._question_ids,
            student_ids=self._student_ids,
        )
        return [
            f"! {overlap.first.actor} and {overlap.second.actor} both hold "
            f"{overlap.shared_cells} answer(s)"
            for overlap in report.overlaps
        ]

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    def action_focus_new(self) -> None:
        self.query_one("#claim-new-questions", Input).focus()

    def action_toggle_filter(self) -> None:
        self.dismiss(ClaimAction(kind="toggle_filter"))

    def _submit_new_claim(self) -> None:
        raw = self.query_one("#claim-new-questions", Input).value.strip()
        if not raw:
            self.app.notify(
                "Type the questions to claim, e.g. q1-q3.", severity="warning"
            )
            return
        questions = self._expand(raw)
        if not questions:
            self.app.notify(
                f"No questions in this assignment match {raw!r}.", severity="warning"
            )
            return
        self.dismiss(
            ClaimAction(kind="claim", work=WorkSlice(question_ids=tuple(questions)))
        )

    def _expand(self, raw: str) -> list[str]:
        """Expand ``q1-q3``/``q1,q2`` against the cohort's real question ids.

        Deliberately the same rule as the CLI's ``_expand_ids``: a range is
        resolved by *position in the real id list*, never by arithmetic, so it
        can't invent a question the assignment doesn't have.
        """
        position = {value: index for index, value in enumerate(self._question_ids)}
        wanted: list[str] = []
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if chunk.count("-") == 1:
                start, end = (part.strip() for part in chunk.split("-"))
                if start in position and end in position:
                    low, high = sorted((position[start], position[end]))
                    wanted.extend(self._question_ids[low : high + 1])
                    continue
            if chunk in position:
                wanted.append(chunk)
        chosen = set(wanted)
        return [value for value in self._question_ids if value in chosen]

    @on(ActionChosen)
    def _on_action_chosen(self, event: ActionChosen) -> None:
        event.stop()
        self.dismiss(event.action)

    @on(Input.Submitted, "#claim-new-questions")
    def _on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._submit_new_claim()

    @on(Button.Pressed, "#claim-add")
    def _on_add_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self._submit_new_claim()

    @on(Button.Pressed, "#claim-filter")
    def _on_filter_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_toggle_filter()

    @on(Button.Pressed, "#claim-close")
    def _on_close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
