from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Label, Static


class AboutScreen(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Label("ABOUT", classes="page-title")
        yield Static("Grading TUI Application v1.0", classes="welcome-text")
