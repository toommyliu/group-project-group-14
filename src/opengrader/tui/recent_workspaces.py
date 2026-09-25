"""Choose a recent workspace when `opengrader tui` has none to open.

Shown only when a bare `tui` runs outside every workspace and the user has
opened at least one before (`core/recents.py`). It lists workspaces that
already exist — choosing one never creates anything. The rows, the *Missing*
marking and `d` to forget come from `RecentList`, the same widget
`opengrader classic` uses for recent class folders.
"""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Header, Label, Static

from opengrader.core.recents import RecentItem
from opengrader.tui.ui.widgets.recent_list import RecentList


class RecentWorkspaces(App[Path | None]):
    """Exits with the chosen workspace's root, or `None` to quit."""

    TITLE = "Opengrader"
    BINDINGS = [("q", "quit", "Quit")]
    DEFAULT_CSS = """
    #recent-home {
        width: 100%;
        height: 1fr;
        margin: 1 0 0 0;
        padding: 1 2;
    }

    #recent-title {
        height: 1;
        text-style: bold;
    }

    #recent-hint {
        height: auto;
        margin-bottom: 1;
        color: $text-muted;
    }

    #recent-workspaces {
        height: 1fr;
        border: round $surface-lighten-2;
        background: transparent;
    }

    #recent-empty {
        height: auto;
        margin-top: 1;
    }
    """

    def __init__(self, items: list[RecentItem], *, here: Path) -> None:
        super().__init__()
        self.items = list(items)
        self.here = here

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="recent-home"):
            yield Label("Recent workspaces", id="recent-title")
            yield Static(
                f"There is no workspace in {self.here}. Open one you used "
                "before, or quit and run `opengrader tui FOLDER` to open a "
                "folder as a workspace (`opengrader classic` grades plain "
                "folders).",
                id="recent-hint",
                markup=False,
            )
            yield RecentList(self.items, id="recent-workspaces")
            yield Static(
                "No recent workspaces left. Press q to quit.",
                id="recent-empty",
                markup=False,
            )
        yield Footer()

    def on_mount(self) -> None:
        self._sync_empty(len(self.items))
        self.query_one(RecentList).focus()

    def on_recent_list_chosen(self, event: RecentList.Chosen) -> None:
        self.exit(Path(event.item.path))

    def on_recent_list_forgotten(self, event: RecentList.Forgotten) -> None:
        self._sync_empty(event.remaining)

    def _sync_empty(self, remaining: int) -> None:
        self.query_one("#recent-empty").display = remaining == 0
        self.query_one(RecentList).display = remaining > 0
