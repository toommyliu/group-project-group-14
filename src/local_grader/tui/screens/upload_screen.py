from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Button, Label, Static


class UploadScreen(VerticalScroll):
    class ProcessRosterRequested(Message):
        pass

    def compose(self) -> ComposeResult:
        yield Label("UPLOAD DOCUMENTS", classes="page-title")
        yield Static("Upload documents script should go here...")
        yield Button("Process Roster (Example)", id="btn-process-roster")
        yield Static("", id="upload-status")

    def set_status(self, text: str) -> None:
        self.query_one("#upload-status", Static).update(text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-process-roster":
            self.post_message(self.ProcessRosterRequested())
