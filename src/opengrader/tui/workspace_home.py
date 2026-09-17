"""Choose an assignment from the workspace service."""

import shlex

from pydantic import TypeAdapter
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, Static

from opengrader.core.workspace.models import Assignment
from opengrader.server.workspace.client import WorkspaceClient

PROCESS_WORKSPACE = "process_workspace"


def process_workspace_argv(root: str) -> list[str]:
    return [root, "--workspace", root]


class WorkspaceHome(App[str | None]):
    TITLE = "Opengrader"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh assignments"),
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
