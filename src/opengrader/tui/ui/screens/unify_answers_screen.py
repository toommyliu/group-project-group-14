"""Modal "unify answers" view, opened with U on the grade screen.

The exact answer-group tier (the ``g`` hotkey) can only batch students whose
answers are *identical* after cosmetic normalization, because merging on
anything looser would risk applying one grade to two different answers. This
screen is where that risk is handed to a human instead: it lists the merges
:mod:`opengrader.core.submissions.suggested_groups` proposes for the question in
front of the grader, shows the full text of every answer a merge would join and
the weakest similarity inside it, and merges nothing until the grader says so.

Every answer but the representative is shown **diffed against it** — added words
highlighted, omitted words struck through. That is not decoration: no similarity
metric can tell a rewording from a reversal (an inserted "not" scores 0.96), so
the grader's judgement is the only thing standing between a proposal and a wrong
grade for a dozen students, and it has to be a judgement about a highlighted word
rather than about two paragraphs that look identical.

Accepting a proposal does not grade anybody. It only widens what the ``g``
hotkey will cover for this question, so the grader still reads the answer,
picks a rubric option, and presses ``g`` exactly as before — one confirmation
step added, no new grading path. That is deliberate: a screen that both proposed
a merge *and* applied a grade would be one keypress away from marking a dozen
students on a proposal the grader had not finished reading.

Acceptance is session state, not a file. The confirmation authorizes one batch
action; once the grades are written they are the durable thing, and a persisted
"these answers are the same" claim would be a second source of truth about
grouping that nothing else in the app reads.
"""

from __future__ import annotations

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from rich.markup import escape

from opengrader.core.submissions.suggested_groups import (
    SuggestedGroup,
    diff_answer_spans,
)


def _diff_markup(reference: str, other: str) -> str:
    """Render one answer with its differences from the graded answer marked up.

    Added/changed words are highlighted and words this answer *omits* are shown
    struck through, so the grader is reading a difference rather than comparing
    two paragraphs by eye. Every span is escaped: a student's answer is
    arbitrary text and a stray ``[`` in it would otherwise be parsed as Rich
    markup and swallow the rest of the line.
    """
    parts: list[str] = []
    for span in diff_answer_spans(reference, other):
        text = escape(span.text)
        if span.kind == "added":
            parts.append(f"[b $warning]{text}[/]")
        elif span.kind == "removed":
            parts.append(f"[dim strike]{text}[/]")
        else:
            parts.append(text)
    return " ".join(parts)


def floor_percent(score: float) -> int:
    """Render a similarity as a whole percent, rounding **down**.

    Every label here reads "at least N% alike", so rounding up would print 100%
    for answers that are 99.5% alike — claiming the answers are identical when
    they are not. Understating is the only safe direction.
    """
    return int(score * 100)


class _SuggestionRow(Static):
    """A focusable proposal; selecting it accepts the merge."""

    can_focus = True

    def __init__(self, index: int, label: str, *, accepted: bool) -> None:
        self._index = index
        classes = "unify-suggestion" + (" unify-accepted" if accepted else "")
        super().__init__(label, classes=classes)

    def _select(self) -> None:
        self.post_message(UnifyAnswersScreen.SuggestionAccepted(self._index))

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self._select()

    def on_key(self, event: events.Key) -> None:
        # The focused row is first in the bubble path, so intercepting the nav
        # keys here keeps the enclosing VerticalScroll from hijacking up/down.
        if event.key in ("enter", "space"):
            event.stop()
            self._select()
        elif event.key in ("down", "j"):
            event.stop()
            self.screen.focus_relative_suggestion(self, 1)
        elif event.key in ("up", "k"):
            event.stop()
            self.screen.focus_relative_suggestion(self, -1)


class UnifyAnswersScreen(ModalScreen["int | None"]):
    """Proposed merges of near-identical answers for one question.

    Dismisses with the index of the accepted proposal, or ``None`` on close.
    """

    DEFAULT_CSS = """
    UnifyAnswersScreen {
        align: center middle;
    }

    #unify-dialog {
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

    #unify-summary {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #unify-body {
        height: 1fr;
        margin-top: 1;
        border: round $surface-lighten-2;
        padding: 0 1;
        scrollbar-size: 1 1;
    }

    UnifyAnswersScreen .unify-suggestion {
        width: 100%;
        height: auto;
        margin: 1 0 0 0;
        color: $accent;
    }

    UnifyAnswersScreen .unify-suggestion:hover {
        background: $boost;
    }

    UnifyAnswersScreen .unify-suggestion:focus {
        background: $accent 20%;
        text-style: bold;
    }

    UnifyAnswersScreen .unify-accepted {
        color: $success;
    }

    UnifyAnswersScreen .unify-answer {
        width: 100%;
        height: auto;
        margin-left: 4;
    }

    UnifyAnswersScreen .unify-answer-lead {
        width: 100%;
        height: auto;
        margin-left: 4;
        text-style: bold;
    }

    UnifyAnswersScreen .unify-empty {
        color: $text-muted;
        padding: 1 2;
    }

    #unify-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("U", "close", "Close"),
    ]

    class SuggestionAccepted(Message):
        """Posted when a proposal is chosen, to widen this question's group."""

        def __init__(self, index: int) -> None:
            self.index = index
            super().__init__()

    def __init__(
        self,
        suggestions: list[SuggestedGroup],
        *,
        question_label: str,
        accepted_indices: frozenset[int] = frozenset(),
    ) -> None:
        self._suggestions = suggestions
        self._question_label = question_label
        self._accepted = accepted_indices
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="unify-dialog") as dialog:
            dialog.border_title = f"▸ Unify Answers · {self._question_label}"
            yield Static(self._summary_text(), id="unify-summary")
            with VerticalScroll(id="unify-body"):
                if not self._suggestions:
                    yield Label(
                        "No near-identical answers to merge — every distinct "
                        "answer to this question is genuinely distinct.",
                        classes="unify-empty",
                    )
                for index, suggestion in enumerate(self._suggestions):
                    yield from self._suggestion_block(index, suggestion)
            with Horizontal(id="unify-buttons"):
                yield Button("Close", id="unify-close", compact=True)

    def on_mount(self) -> None:
        # Land on the first proposal so enter accepts immediately; fall back to
        # the close button when there is nothing to act on (Textual's `.first()`
        # raises on an empty query, so iterate instead).
        rows = list(self.query(_SuggestionRow))
        if rows:
            rows[0].focus()
        else:
            self.query_one("#unify-close", Button).focus()

    def focus_relative_suggestion(self, current: _SuggestionRow, delta: int) -> None:
        """Move focus to the next/previous proposal (arrow / j·k keys)."""
        rows = list(self.query(_SuggestionRow))
        if current not in rows:
            return
        target_index = rows.index(current) + delta
        if 0 <= target_index < len(rows):
            row = rows[target_index]
            row.focus()
            row.scroll_visible()

    def _suggestion_block(self, index: int, suggestion: SuggestedGroup):
        accepted = index in self._accepted
        mark = "✓ accepted · " if accepted else ""
        headline = (
            f"{mark}{suggestion.size} students across {suggestion.group_count} "
            f"answers — at least {floor_percent(suggestion.min_score)}% alike"
        )
        yield _SuggestionRow(index, headline, accepted=accepted)
        reference = suggestion.representative.sample_response_text
        for group in suggestion.groups:
            lead = group is suggestion.representative
            prefix = "grade this" if lead else f"+{group.size}"
            # The full answer, never a snippet: the grader is being asked
            # whether these are the same answer, and a truncated one cannot be
            # judged. Every answer but the representative is shown *diffed*
            # against it, because the similarity score cannot tell a rewording
            # from a reversal and an unhighlighted "not" is invisible inside a
            # paragraph that is otherwise word-for-word identical.
            body = (
                escape(" ".join(reference.split()))
                if lead
                else _diff_markup(reference, group.sample_response_text)
            )
            yield Label(
                f"\\[{prefix}] {body}",
                classes="unify-answer-lead" if lead else "unify-answer",
                markup=True,
            )

    def _summary_text(self) -> str:
        if not self._suggestions:
            return (
                "Nothing proposed. ↑↓/jk move · esc closes."
            )
        students = sum(suggestion.added_size for suggestion in self._suggestions)
        return (
            f"{len(self._suggestions)} proposed merge(s), covering {students} "
            "more student(s) than the exact groups.  Accepting one only widens "
            "what g will grade — it marks nobody.\n"
            "↑↓/jk move · enter accepts · esc closes."
        )

    @on(SuggestionAccepted)
    def _on_suggestion_accepted(self, event: SuggestionAccepted) -> None:
        event.stop()
        self.dismiss(event.index)

    @on(Button.Pressed, "#unify-close")
    def _on_close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
