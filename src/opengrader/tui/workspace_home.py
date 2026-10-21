"""Choose an assignment from the workspace service.

This is where `opengrader tui` starts: the workspace's assignments and nothing
else. Tools, settings and folder grading live in `opengrader classic`.
"""

import shlex
from pathlib import Path

from pydantic import TypeAdapter
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, Static

from opengrader.core.workspace.models import Assignment
from opengrader.server.workspace.client import WorkspaceClient

PROCESS_WORKSPACE = "process_workspace"


def process_workspace_argv(folder: str, root: str) -> list[str]:
    return [folder, "--workspace", root]


class WorkspaceHome(App[str | None]):
    TITLE = "Opengrader"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh assignments"),
        ("w", "write_back", "Write grades to files"),
        ("a", "assistant", "AI assistant"),
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

    #assignment-actions {
        height: auto;
        margin-top: 1;
    }

    #assistant {
        margin-left: 1;
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

    def __init__(
        self,
        client: WorkspaceClient,
        folder: Path | None = None,
        *,
        highlight: str | None = None,
    ):
        super().__init__()
        self.client = client
        self.folder = folder or Path(client.connection.root)
        self.assignments: list[Assignment] = []
        # The assignment to start on: the one last opened here (core/recents),
        # or the one just left with F2. Only highlighted, never opened.
        self.highlight = highlight

    def compose(self) -> ComposeResult:
        root = self.client.connection.root
        command = shlex.join(
            ["opengrader", "run", *process_workspace_argv(str(self.folder), root)]
        )
        yield Header()
        with Vertical(id="workspace-home"):
            yield Label("Workspace", id="workspace-name")
            yield Static(root, id="workspace-path", markup=False)
            with Vertical(id="assignment-picker"):
                yield Label("Choose an assignment to grade", id="picker-title")
                yield ListView(id="assignments")
                with Horizontal(id="assignment-actions"):
                    yield Button(
                        "Write grades to files (W)",
                        id="write-back",
                        tooltip=(
                            "Copy this assignment's workspace grades into its "
                            ".submission.md files, so exports and backups see them. "
                            "Shows a preview first."
                        ),
                    )
                    yield Button(
                        "AI assistant (A)",
                        id="assistant",
                        tooltip=(
                            "Your own AI assistant writes or checks the answer key, "
                            "drafts rubrics or grades, in a copy with no names or IDs. "
                            "Its grades come back for you to confirm."
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
        yield Footer()

    async def on_mount(self) -> None:
        await self.action_refresh()

    async def action_refresh(self) -> None:
        try:
            listing = self.query_one(ListView)
            # A refresh keeps the cursor on the same assignment, not the same row.
            if self.assignments and listing.index is not None:
                self.highlight = self.assignments[listing.index].id
            self.assignments = TypeAdapter(list[Assignment]).validate_json(
                self.client.request("GET", "/api/assignments")
            )
            await listing.clear()
            await listing.extend(
                [ListItem(Label(item.title)) for item in self.assignments]
            )
            listing.index = self._start_index()
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

    def _start_index(self) -> int | None:
        if not self.assignments:
            return None
        return next(
            (i for i, item in enumerate(self.assignments) if item.id == self.highlight),
            0,
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is not None:
            self.exit(self.assignments[index].id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "process-folder":
            self.exit(PROCESS_WORKSPACE)
        elif event.button.id == "write-back":
            self.action_write_back()
        elif event.button.id == "assistant":
            self.action_assistant()

    def action_assistant(self) -> None:
        from opengrader.tui.ui.screens.assistant_screen import WorkspaceAssistantMenu

        if self.client.connection.account:
            self.notify(
                f"Only {self.client.connection.owner}, who owns this workspace, can give "
                "its work to an AI assistant."
            )
            return
        listing = self.query_one("#assignments", ListView)
        if not self.assignments or listing.index is None:
            self.notify("Choose an assignment first.")
            return
        self.push_screen(
            WorkspaceAssistantMenu(
                root=Path(self.client.connection.root),
                assignment_id=self.assignments[listing.index].id,
                request=self.client.request,
            )
        )

    def action_write_back(self) -> None:
        from opengrader.tui.ui.screens.write_back_screen import WriteBackScreen

        if self.client.connection.account:
            self.notify(
                f"Only {self.client.connection.owner}, who owns this workspace, can "
                "write its grades to the class folder's files."
            )
            return

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
