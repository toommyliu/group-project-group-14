from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, Static


class SettingsScreen(Vertical):
    def compose(self) -> ComposeResult:
        yield Label("SETTINGS", classes="page-title")
        yield Static("Settings content goes here...")
