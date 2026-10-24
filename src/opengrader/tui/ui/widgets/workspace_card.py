"""The Workspace card at the bottom of classic's Helper Tools.

It explains what a workspace is in a teacher's words, reads the chosen
folder with `make_workspace.plan_workspace` (files only: nothing is written
and no service starts until a yes), and asks before anything is made. The
card does not act itself: it posts `WorkspaceCard.Chosen`, and the setup
screen — which owns the browser launcher and knows whether a folder is being
prepared — makes the workspace and opens it.

The folder box is filled from the Start grading card's folder
(`suggest_folder`) until the teacher types their own, because the folder to
make a workspace of is often that one — or the folder holding it and the
other assignments of the course.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from rich.table import Table
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Input, Static

from opengrader.tui.make_workspace import WorkspacePlan, plan_workspace
from opengrader.tui.ui import button_roles

Where = Literal["browser", "terminal"]

# The confirmation dialog's choices (`PrepareFolderScreen` actions).
CHOICE_BROWSER = "browser"
CHOICE_TERMINAL = "terminal"


def comparison_table() -> Table:
    """Class folder and workspace side by side, for the card."""
    # A blank line under each row: several cells run to more than one line.
    table = Table(
        box=None, show_edge=False, pad_edge=False, expand=True, padding=(0, 1, 1, 0)
    )
    table.add_column("", style="bold", no_wrap=True)
    table.add_column("Class folder (what you use now)", ratio=1)
    table.add_column("Workspace", ratio=1)
    table.add_row(
        "Holds", "One assignment at a time", "A whole course: every assignment together"
    )
    table.add_row(
        "Grade in",
        "This window, or your web browser with fewer features (turn on "
        "Browser app above)",
        "Your web browser (or this window)",
    )
    table.add_row(
        "Grades kept",
        "In the students' files, straight away",
        "In a database file inside the folder (.opengrader), copied to the "
        "students' files when you choose",
    )
    table.add_row(
        "Extras",
        "—",
        "One list of the course's assignments; add scans and new assignments "
        "from the browser; share it with other accounts on this computer",
    )
    table.add_row(
        "Running",
        "Nothing in the background",
        "A small helper on this computer while you grade",
    )
    # Each true of how the two are built (core/workspace/folder_session.py,
    # core/workspace/store.py), so a teacher can choose with eyes open.
    table.add_row(
        "Downsides",
        "• Each assignment is opened on its own: no list of the whole course\n"
        "• In the browser you cannot add assignments or scans, or replace "
        "the whole answer key\n"
        "• No sharing with other accounts on this computer",
        "• The students' files fall behind until you write grades back\n"
        "• Takes a little more disk space\n"
        "• If the database file is damaged or lost, grades given since the "
        "last write-back go with it — more likely in a Dropbox, iCloud or "
        "OneDrive folder, or on a drive unplugged while grading\n"
        "• An older copy of Opengrader may refuse to open it\n"
        "• No one-click way back to a plain class folder",
    )
    return table


class WorkspaceCard(Vertical):
    DEFAULT_CSS = """
    WorkspaceCard {
        width: 100%;
        height: auto;
    }

    WorkspaceCard #workspace-compare {
        width: 100%;
        height: auto;
        margin: 1 0;
    }

    WorkspaceCard #workspace-plan {
        width: 100%;
        height: auto;
        margin-top: 1;
    }

    WorkspaceCard #workspace-plan.-problem {
        color: $warning;
    }

    WorkspaceCard #workspace-progress {
        width: 100%;
        height: auto;
        color: $text-muted;
        padding-left: 1;
        border-left: tall $warning;
    }

    WorkspaceCard #workspace-progress.-done {
        border-left: tall $success;
    }

    WorkspaceCard #workspace-progress.-failed {
        border-left: tall $error;
    }
    """

    class Chosen(Message):
        """Make (or open) the workspace `plan` describes, then open it `where`."""

        def __init__(self, card: "WorkspaceCard", plan: WorkspacePlan, where: Where) -> None:
            super().__init__()
            self.card = card
            self.plan = plan
            self.where = where

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        super().__init__(**kwargs)
        self._plan: WorkspacePlan | None = None
        # The last folder filled in from the Start grading card: replaced by
        # the next one, but a folder the teacher typed is left alone.
        self._suggested = ""
        self.busy = False

    def compose(self) -> ComposeResult:
        yield Static(
            "A workspace keeps a whole course in one place — every assignment, "
            "its answer key and its class list — and lets you grade in your web "
            "browser as well as here. Making one moves and deletes nothing: your "
            "files stay where they are.",
            classes="card-hint",
        )
        yield Static(comparison_table(), id="workspace-compare")
        yield Static(
            "Choose a class folder whose scans have been read, or the folder "
            "that holds all of a course's assignment folders.",
            classes="card-hint",
        )
        yield Static("[b]Folder[/b]  [dim]to turn into a workspace[/dim]", classes="field-label")
        yield Horizontal(
            Input(
                placeholder="Path to a class folder or a course folder…",
                id="workspace-folder",
                classes="path-input",
            ),
            Button(
                "Browse",
                id="workspace-browse",
                classes=f"browse-button {button_roles.OPEN}",
                compact=True,
            ),
            classes="path-row",
        )
        yield Static("", id="workspace-plan", markup=False)
        yield Static("", id="workspace-progress", markup=False)
        yield Horizontal(
            Button(
                "Make workspace…",
                id="workspace-make",
                classes=button_roles.BROWSER,
                compact=True,
                disabled=True,
            ),
            classes="card-actions",
        )

    def on_mount(self) -> None:
        self.query_one("#workspace-plan").display = False
        self.query_one("#workspace-progress").display = False

    # -- the folder -------------------------------------------------------

    @property
    def folder(self) -> str:
        return self.query_one("#workspace-folder", Input).value.strip().strip("'\"")

    def suggest_folder(self, folder: str | Path) -> None:
        """Fill the box with the Start grading card's folder, unless the
        teacher typed a different one."""
        if self.busy:
            return
        box = self.query_one("#workspace-folder", Input)
        if box.value.strip() and box.value.strip() != self._suggested:
            return
        self._suggested = str(folder)
        box.value = self._suggested
        self.refresh_plan()

    def refresh_plan(self) -> WorkspacePlan | None:
        """Read the folder again and say what the button would do."""
        label = self.query_one("#workspace-plan", Static)
        button = self.query_one("#workspace-make", Button)
        if not self.folder:
            self._plan = None
            label.display = False
            button.disabled = True
            button.label = "Make workspace…"
            return None
        plan = plan_workspace(Path(self.folder))
        self._plan = plan
        label.update("\n".join(plan.lines))
        label.set_class(plan.action == "none", "-problem")
        label.display = True
        button.label = "Open workspace…" if plan.action == "open" else "Make workspace…"
        button.disabled = plan.action == "none" or self.busy
        return plan

    @on(Input.Submitted, "#workspace-folder")
    def _on_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.refresh_plan()

    @on(Input.Blurred, "#workspace-folder")
    def _on_blurred(self, event: Input.Blurred) -> None:
        self.refresh_plan()

    @on(Button.Pressed, "#workspace-browse")
    def _on_browse(self, event: Button.Pressed) -> None:
        event.stop()
        from opengrader.tui.ui.screens.path_picker_screen import PathPickerScreen

        current = Path(self.folder).expanduser() if self.folder else None
        start = current if current is not None and current.is_dir() else None

        def chosen(path: Path | None) -> None:
            if path is not None:
                self.query_one("#workspace-folder", Input).value = str(path)
                self.refresh_plan()

        self.app.push_screen(
            PathPickerScreen(
                title="Choose the folder to make a workspace",
                mode="directory",
                start_path=start,
            ),
            chosen,
        )

    # -- asking, then handing over ----------------------------------------

    @on(Button.Pressed, "#workspace-make")
    def _on_make(self, event: Button.Pressed) -> None:
        event.stop()
        if self.busy:
            return
        # Read again: the folder may have changed since it was typed.
        plan = self.refresh_plan()
        if plan is not None and plan.action != "none":
            self.ask(plan)

    def ask(self, plan: WorkspacePlan) -> None:
        """The "are you sure?" dialog; a yes posts `Chosen`. Also used by
        the Start grading card's *Make this a workspace…*, so both ask the
        same question with the same choices."""
        from opengrader.tui.ui.screens.prepare_folder_screen import PrepareFolderScreen

        if plan.action == "make":
            title = "Make this folder a workspace?"
            note = (
                "Your files are not moved or deleted.\n\n"
                "From now on, grade this class in the workspace — not as a class "
                "folder here — or its grades end up in two places. Grades given "
                "in the workspace are copied back into the students' files when "
                "you choose Write grades back (W in the workspace's assignment "
                "list)."
            )
            actions = [
                (CHOICE_TERMINAL, "Make it, grade in this window"),
                (CHOICE_BROWSER, "Make it, open in browser"),
            ]
        else:
            title = "Open this workspace?"
            note = "It opens on its list of assignments."
            actions = [
                (CHOICE_TERMINAL, "Open in this window"),
                (CHOICE_BROWSER, "Open in browser"),
            ]
        found = "\n".join([f"Folder: {plan.root or plan.folder}", *plan.lines])

        def answered(choice: str | None) -> None:
            if choice in (CHOICE_BROWSER, CHOICE_TERMINAL):
                self.post_message(self.Chosen(self, plan, choice))  # type: ignore[arg-type]

        self.app.push_screen(PrepareFolderScreen(title, found, note, actions), answered)

    # -- progress, shown here because the launcher's line is far above ----

    def show_progress(self, message: str) -> None:
        self.busy = True
        self.query_one("#workspace-make", Button).disabled = True
        progress = self.query_one("#workspace-progress", Static)
        progress.remove_class("-done", "-failed")
        progress.update(message)
        progress.display = True

    def finished(self, root: Path, error: str | None) -> None:
        self.busy = False
        progress = self.query_one("#workspace-progress", Static)
        if error is None:
            progress.add_class("-done")
            progress.update(
                f"✓ {root} is a workspace, open in your browser. Open workspace… "
                "here takes you back to it any time."
            )
        else:
            progress.add_class("-failed")
            progress.update(f"✗ {error}\nYour files are as they were.")
        progress.display = True
        self.refresh_plan()
