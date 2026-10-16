"""Join a submission with the one scanned just before or after it (`X`).

For a paper the splitter cut in two that was noticed only after it was read,
often while grading: pages 1–5 in one submission and page 6 as a "student"
of its own. Joining makes one PDF of the two, reads it again, and keeps the
grades given while the answer was on screen. The pieces are named by their
files, never by student: grading is anonymous by default.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from opengrader.tui.data.models import SplitNeighbours, SplitPiece
from opengrader.tui.ui.button_roles import OutlineButtons


def describe_piece(piece: SplitPiece) -> str:
    pages = "page" if piece.pages == 1 else "pages"
    shown = f"{piece.pages} {pages}" if piece.pages else "its PDF already joined"
    return f"{piece.stem} · {shown} · {piece.graded} graded"


class JoinSubmissionsScreen(OutlineButtons, ModalScreen[str | None]):
    """Dismisses with "before" or "after" (which neighbour to join), or None."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    JoinSubmissionsScreen {
        align: center middle;
    }

    #join-dialog {
        width: 84%;
        max-width: 96;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: round $warning;
        border-title-align: left;
        border-title-color: $warning;
        border-title-style: bold;
        padding: 1 2;
    }

    #join-intro, #join-here {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }

    #join-here {
        text-style: bold;
    }

    .join-choice {
        width: 100%;
        margin-bottom: 1;
    }

    #join-buttons {
        height: auto;
        align-horizontal: right;
    }
    """

    def __init__(self, neighbours: SplitNeighbours) -> None:
        super().__init__()
        self._neighbours = neighbours

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="join-dialog") as dialog:
            dialog.border_title = "▸ Join a paper the splitter cut in two"
            yield Static(
                "If this student's paper was split into two submissions, join "
                "them: the two PDFs become one, the paper is read again, and "
                "every grade given while its answer was on screen is kept. A "
                "question marked wrong because its page was in the other piece "
                "starts again. Undo history is cleared, and in a workspace the "
                "joined paper becomes a new submission.",
                id="join-intro",
                markup=False,
            )
            yield Static(
                f"This submission: {describe_piece(self._neighbours.here)}",
                id="join-here",
                markup=False,
            )
            if self._neighbours.before is not None:
                yield Button(
                    f"Join with the one before: {describe_piece(self._neighbours.before)}",
                    id="join-before",
                    classes="join-choice",
                )
            if self._neighbours.after is not None:
                yield Button(
                    f"Join with the one after: {describe_piece(self._neighbours.after)}",
                    id="join-after",
                    classes="join-choice",
                )
            with Horizontal(id="join-buttons"):
                yield Button("Cancel", id="join-cancel")

    def on_mount(self) -> None:
        self.query_one("#join-cancel", Button).focus()

    @on(Button.Pressed, "#join-before")
    def _on_before(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss("before")

    @on(Button.Pressed, "#join-after")
    def _on_after(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss("after")

    @on(Button.Pressed, "#join-cancel")
    def _on_cancel(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
