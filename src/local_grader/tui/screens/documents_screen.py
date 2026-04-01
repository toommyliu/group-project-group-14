from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Input, Label, Static


class DocumentsScreen(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Label("MY DOCUMENTS", classes="page-title")
        yield Input(placeholder="Type a note and press Enter...", id="document-input")
        yield VerticalScroll(id="document-container")

    def add_note(self, note: str) -> None:
        if not note.strip():
            return
        container = self.query_one("#document-container", VerticalScroll)
        container.mount(Static(note, classes="document_note"))
        container.scroll_end(animate=False)

    def clear_notes(self) -> None:
        self.query_one("#document-container", VerticalScroll).remove_children()

    def focus_input(self) -> None:
        self.query_one("#document-input", Input).focus()
