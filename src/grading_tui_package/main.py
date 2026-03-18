from PIL import Image as PILImage
from rich.console import Console
from textual.app import App, ComposeResult
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, Static, Button, Label, ContentSwitcher, Input
from textual.widget import Widget           # or: from textual.widgets import Widget
from textual_image.renderable.halfcell import Image as TIImage
from rich.console import RenderableType

class ImageWidget(Widget):
    def __init__(self, path: str) -> None:
        super().__init__()
        self._img = TIImage(path)

    def render(self) -> RenderableType:
        return self._img

class Sidebar(Vertical):
    def compose(self) -> ComposeResult:
        yield Label("MENU", id="menu-title")
        yield Button("My Notes", id="nav-notes", variant="primary")
        yield Button("Settings", id="nav-settings")
        yield Button("About", id="nav-about")

class GradingTUIManager(App):
    CSS = """
    Screen { background: #1a1a1a; }
    Sidebar {
        width: 25;
        background: #242424;
        border-right: solid $accent;
        padding: 1;
    }
    #menu-title { text-style: bold; margin-bottom: 1; text-align: center; }
    Sidebar Button { width: 100%; margin-bottom: 1; }
    
    ContentSwitcher { padding: 1; }
    .page-title { text-style: bold underline; margin-bottom: 1; color: $accent; }
    .welcome-text { margin-bottom: 1; color: $text-muted; }
    
    #note-input {
        margin-bottom: 1;
        border: tall $accent;
    }

    .note { 
        background: $panel; 
        border: solid $accent; 
        margin-bottom: 1; 
        padding: 1; 
    }

    """

    BINDINGS = [("q", "quit", "Quit"), ("n", "user_input", "Enter Input"), ("c", "clear_all", "Clear All")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Sidebar()
            with ContentSwitcher(initial="page-notes"):
                # NOTES PAGE
                with VerticalScroll(id="page-notes"):
                    yield Label("MY NOTES", classes="page-title")
                    yield Label("Welcome to our Grading TUI Application. Press 'N' to add a note.", classes="welcome-text")
                    
                    yield Input(placeholder="Type a note and press Enter...", id="note-input")
                    yield Vertical(id="notes-container")
                
                # SETTINGS PAGE
                with Vertical(id="page-settings"):
                    yield Label("SETTINGS", classes="page-title")
                    yield Static("Settings content goes here...")
                
                # ABOUT PAGE
                with Vertical(id="page-about"):
                    yield Label("ABOUT", classes="page-title")
                    yield Static("Grading TUI Application v1.1")
                    img = PILImage.open("images/Grading.png").convert("RGBA")
                    img = img.resize((360, 360), PILImage.LANCZOS)
                    img.save("images/Grading_360.png")
                    yield Static(TIImage("images/Grading_360.png"))

        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        switcher = self.query_one(ContentSwitcher)
        if event.button.id == "nav-notes":
            switcher.current = "page-notes"
        elif event.button.id == "nav-settings":
            switcher.current = "page-settings"
        elif event.button.id == "nav-about":
            switcher.current = "page-about"

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Triggered when you press Enter in the Input field."""
        if event.value.strip():  # Only add if the note isn't empty
            container = self.query_one("#notes-container")
            container.mount(Static(event.value, classes="note"))
            
            # Clear the input for the next note
            event.input.value = ""
            # Auto-scroll to the new note
            container.scroll_visible()

    def action_clear_all(self):
        self.query("#notes-container Static").remove()

    def action_user_input(self) -> None:
        """Binding for 'N' to jump straight to the input box."""
        self.query_one("#note-input").focus()

def run_app():
    GradingTUIManager().run()

if __name__ == "__main__":
    run_app()