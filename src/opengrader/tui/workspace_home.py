"""Choose an assignment from the workspace service."""

import shlex

from pydantic import TypeAdapter
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, Static

from opengrader.core.workspace.models import Assignment
from opengrader.server.workspace.client import WorkspaceClient

PROCESS_WORKSPACE = "process_workspace"
# Leave the chooser for the start page: every pipeline step and helper tool.
OPEN_TOOLS = "open_tools"
# Back to the terminal app's Home screen, to open another folder or workspace.
GO_HOME = "go_home"


def process_workspace_argv(root: str) -> list[str]:
    return [root, "--workspace", root]


class WorkspaceHome(App[str | None]):
    TITLE = "Opengrader"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh assignments"),
        ("s", "tools", "Tools & settings"),
        ("w", "write_back", "Write grades to files"),
        ("h", "home", "Home"),
        ("escape", "home", "Home"),
    ]
    DEFAULT_CSS = """
    #workspace-home {
        width: 100%;
        height: 1fr;
        margin: 1 0 0 0;
        padding: 1 2;
    }

    #workspace-name {
        height: 1;
        text-style: bold;
    }

    #workspace-path {
        height: auto;
        margin-bottom: 1;
        color: $text-muted;
    }

    #assignment-picker {
        height: 1fr;
    }

    #open-tools, #go-home, #write-back {
        margin-top: 1;
    }

    #picker-title,
    #empty-title,
    #cli-title {
        height: 1;
        text-style: bold;
    }

    #assignments {
        height: 1fr;
        margin: 1 0;
        border: round $surface-lighten-2;
        background: transparent;
    }

    #assignments > ListItem {
        background: transparent;
    }

    #empty-workspace {
        height: auto;
        margin: 1 0;
        padding: 2 3;
        border: round $surface-lighten-2;
    }

    #empty-description,
    #cli-description {
        height: auto;
        color: $text-muted;
    }

    #cli-title {
        margin-top: 2;
    }

    #cli-command {
        height: auto;
        margin-top: 1;
        padding: 1 2;
        background: $boost;
    }

    #process-folder {
        width: auto;
        margin-top: 1;
    }
    """

    def __init__(self, client: WorkspaceClient):
        super().__init__()
        self.client = client
        self.assignments: list[Assignment] = []

    def compose(self) -> ComposeResult:
        root = self.client.connection.root
        command = shlex.join(["opengrader", "run", *process_workspace_argv(root)])
        yield Header()
        with Vertical(id="workspace-home"):
            yield Label("Workspace", id="workspace-name")
            yield Static(root, id="workspace-path", markup=False)
            with Vertical(id="assignment-picker"):
                yield Label("Choose an assignment to grade", id="picker-title")
                yield ListView(id="assignments")
                yield Button(
                    "Write grades to files (W)",
                    id="write-back",
                    tooltip=(
                        "Copy this assignment's workspace grades into its "
                        ".submission.md files, so exports and backups see them. "
                        "Shows a preview first."
                    ),
                )
            with Vertical(id="empty-workspace"):
                yield Label("Set up your first assignment", id="empty-title")
                yield Static(
                    "Put your QA answer key and scans in this folder. Opengrader "
                    "will find them and prepare the submissions for grading.",
                    id="empty-description",
                )
                yield Button(
                    "Process this folder",
                    id="process-folder",
                    variant="primary",
                )
                yield Label("Or run the command yourself", id="cli-title")
                yield Static(
                    "Process this folder from another terminal, then press R to "
                    "refresh.",
                    id="cli-description",
                )
                yield Static(command, id="cli-command", markup=False)
            yield Button("← Home (H)", id="go-home")
            yield Button(
                "Tools & settings (S)",
                id="open-tools",
                tooltip=(
                    "The start page: check this computer, AI setup and "
                    "providers, sharing, backup, and every pipeline step."
                ),
            )
        yield Footer()

    async def on_mount(self) -> None:
        await self.action_refresh()

    async def action_refresh(self) -> None:
        try:
            self.assignments = TypeAdapter(list[Assignment]).validate_json(
                self.client.request("GET", "/assignments")
            )
            listing = self.query_one(ListView)
            await listing.clear()
            await listing.extend(
                [ListItem(Label(item.title)) for item in self.assignments]
            )
            listing.index = 0 if self.assignments else None
            has_assignments = bool(self.assignments)
            self.query_one("#assignment-picker").display = has_assignments
            self.query_one("#empty-workspace").display = not has_assignments
            (
                listing
                if has_assignments
                else self.query_one("#process-folder", Button)
            ).focus()
        except RuntimeError as exc:
            self.notify(str(exc), severity="error")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is not None:
            self.exit(self.assignments[index].id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "process-folder":
            self.exit(PROCESS_WORKSPACE)
        elif event.button.id == "open-tools":
            self.exit(OPEN_TOOLS)
        elif event.button.id == "go-home":
            self.exit(GO_HOME)
        elif event.button.id == "write-back":
            self.action_write_back()

    def action_home(self) -> None:
        self.exit(GO_HOME)

    def action_write_back(self) -> None:
        from opengrader.tui.ui.screens.write_back_screen import WriteBackScreen

        listing = self.query_one("#assignments", ListView)
        if not self.assignments or listing.index is None:
            self.notify("Choose an assignment first.")
            return
        chosen = self.assignments[listing.index]
        self.push_screen(
            WriteBackScreen(
                workspace_root=self.client.connection.root,
                assignment_id=chosen.id,
                title=chosen.title,
            )
        )

    def action_tools(self) -> None:
        self.exit(OPEN_TOOLS)
