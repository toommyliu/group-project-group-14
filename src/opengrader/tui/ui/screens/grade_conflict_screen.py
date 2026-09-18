"""Compare saved and recovered work before replacing either version."""

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from opengrader.core.workspace.models import Answer
from opengrader.server.workspace.drafts import Draft


class GradeConflictScreen(ModalScreen[bool | None]):
    BINDINGS = [("escape", "cancel", "Keep editing")]
    DEFAULT_CSS = """
    GradeConflictScreen { align: center middle; }
    GradeConflictScreen > VerticalScroll {
        width: 80%; max-width: 100; height: auto; max-height: 85%;
        background: $surface; border: round $accent; padding: 1 2;
    }
    GradeConflictScreen Static { height: auto; margin-bottom: 1; }
    GradeConflictScreen Horizontal { height: auto; }
    """

    def __init__(self, draft: Draft, current: Answer):
        super().__init__()
        self.draft, self.current = draft, current

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("This answer changed while you were grading.")
            yield Static(
                f"Saved score: {self.current.points_awarded}\nSaved rubric: {', '.join(self.current.rubric_labels)}\nSaved feedback:\n{self.current.analysis}",
                markup=False,
            )
            yield Static(
                f"Your score: {self.draft.edit.points_awarded}\nYour rubric: {', '.join(self.draft.edit.rubric_labels)}\nYour feedback:\n{self.draft.edit.analysis}",
                markup=False,
            )
            with Horizontal():
                yield Button("Keep editing", id="cancel")
                yield Button("Use saved answer", id="saved")
                yield Button("Save my changes", id="mine", variant="primary")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(
            {"cancel": None, "saved": False, "mine": True}[event.button.id or "cancel"]
        )
