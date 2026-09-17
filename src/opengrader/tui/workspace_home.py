"""Choose an assignment from the workspace service."""

import os
import shlex
import webbrowser

from pydantic import TypeAdapter
from textual.app import App, ComposeResult
from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, Static

from opengrader.core.workspace.models import Assignment
from opengrader.server.workspace.client import WorkspaceClient


class WorkspaceHome(App[str | None]):
    TITLE = "Opengrader"
    BINDINGS = [("q", "quit", "Quit"), ("r", "refresh", "Refresh assignments")]

    def __init__(self, client: WorkspaceClient):
        super().__init__()
        self.client = client
        self.assignments: list[Assignment] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Choose an assignment to grade.", id="instructions")
        command = shlex.join(
            [
                "uv",
                "run",
                "opengrader",
                "run",
                "--scan",
                "scans.pdf",
                "--qa",
                "qa.md",
                "--out",
                "class/",
                "--workspace",
                self.client.connection.root,
            ]
        )
        yield Static(
            f"To process local scans, run in another terminal:\n{command}\nThen press R to refresh assignments.",
            markup=False,
        )
        yield ListView(id="assignments")
        yield Button("Open assignment setup in browser", id="setup")
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
            self.query_one("#instructions", Static).update(
                "Choose an assignment to grade."
                if self.assignments
                else "No assignments yet. Process scans with the CLI below, or open setup in the browser."
            )
            listing.focus()
        except RuntimeError as exc:
            self.notify(str(exc), severity="error")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is not None:
            self.exit(self.assignments[index].id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "setup":
            if os.environ.get("SSH_CONNECTION"):
                self.notify(
                    f"Forward the service port and open {self.client.connection.url}",
                    timeout=30,
                )
            else:
                webbrowser.open(self.client.connection.url)
