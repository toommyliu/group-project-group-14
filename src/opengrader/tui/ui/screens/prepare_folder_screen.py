"""Ask before Launch Grading prepares a class folder that is not ready.

Shown by the Start grading card when the chosen folder has scans but nothing
to grade yet, when its answer key is missing, or when its submissions were
read before there was an answer key to auto-grade them with. The card decides
what to say and which choices to offer; this dialog only shows them and
reports the one that was picked.

Nothing is run from here. Preparing writes `split/` and `submissions/` into
the folder and can take minutes, so it is always a choice — and a mistyped
folder costs one Escape, not a run.
"""

from __future__ import annotations

from typing import Sequence

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

# What a dialog can be dismissed with.
ACTION_PREPARE = "prepare"
ACTION_SETTINGS = "settings"
ACTION_CREATE_KEY = "create-key"
ACTION_AUTO_GRADE = "auto-grade"
ACTION_OPEN = "open"
ACTION_SKIP = "skip"

_BUTTON_PREFIX = "prepare-folder-"


class PrepareFolderScreen(ModalScreen[str | None]):
    """Dismisses with the chosen action, or None for Cancel / Escape."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    PrepareFolderScreen {
        align: center middle;
    }

    #prepare-folder-dialog {
        width: 84%;
        max-width: 96;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #prepare-folder-found, #prepare-folder-note, #prepare-folder-ai {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }

    #prepare-folder-ai {
        color: $text-muted;
    }

    #prepare-folder-buttons {
        height: auto;
        align-horizontal: right;
    }

    #prepare-folder-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        title: str,
        found: str,
        note: str,
        actions: Sequence[tuple[str, str]],
        *,
        ai_status: str = "",
    ) -> None:
        """`actions` are (action, button label) pairs, left to right; the last
        one is the suggested choice and starts focused."""
        super().__init__()
        self._title = title
        self._found = found
        self._note = note
        self._actions = tuple(actions)
        self._ai_status = ai_status

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="prepare-folder-dialog") as dialog:
            dialog.border_title = f"▸ {self._title}"
            # File names are shown as they are: a "[" in one is not markup.
            yield Static(self._found, id="prepare-folder-found", markup=False)
            yield Static(self._note, id="prepare-folder-note", markup=False)
            if self._ai_status:
                yield Static(self._ai_status, id="prepare-folder-ai")
            with Horizontal(id="prepare-folder-buttons"):
                yield Button("Cancel", id=f"{_BUTTON_PREFIX}cancel")
                for index, (action, label) in enumerate(self._actions):
                    last = index == len(self._actions) - 1
                    yield Button(
                        label,
                        id=f"{_BUTTON_PREFIX}{action}",
                        variant="primary" if last else "default",
                    )

    def on_mount(self) -> None:
        self.query(Button).last().focus()

    @on(Button.Pressed)
    def _on_button(self, event: Button.Pressed) -> None:
        event.stop()
        action = (event.button.id or "").removeprefix(_BUTTON_PREFIX)
        self.dismiss(None if action == "cancel" else action)

    def action_cancel(self) -> None:
        self.dismiss(None)
