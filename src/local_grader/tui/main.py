from rich.console import Console, RenderableType
from textual.app import App, ComposeResult
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, Static, Button, Label, ContentSwitcher, Input
from textual_image.widget import Image

from local_grader.core.pdf import pdf_splitter
from local_grader.core.questions import split_pdf_by_rectangles
from local_grader.core.roster import read_roster, display_roster, save_roster_with_grades

class Sidebar(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Label("MENU", id="menu-title")
        yield Button("My Notes", id="nav-notes", variant="primary")
        yield Button("Upload Documents", id="nav-upload")
        yield Button("View Documents", id="nav-view")
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

    Horizontal#split {
        height: auto;
        border: solid green;
    }

    Vertical.left-view {
        width: 50%;
        padding: 1 2;
        content-align: left middle;
    }

    Vertical.right-view {
        width: 50%;
        padding: 1 2;
        border-left: solid red;
        content-align: left top;
    }

    #about-image {
    width: 100%;
    height: auto;
    margin-top: 1;
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

                # UPLOAD PAGE
                with VerticalScroll(id="page-upload"):
                    yield Label("UPLOAD DOCUMENTS", classes="page-title")
                    yield Static("Upload documents script should go here...")
                    yield Button("Process Roster (Example)", id="btn-process-roster")
                    yield Static("", id="upload-status")

                # VIEW PAGE
                with VerticalScroll(id="page-view"):
                    yield Label("VIEW DOCUMENTS", classes="page-title")
                    with Horizontal(id = "split"):  
                            with VerticalScroll (classes = "left-view"):
                                yield Image("images/Grading.jpg", id="about-image")
                            with VerticalScroll (classes = "right-view"):
                                yield Input(placeholder= "First Name", id = "first-name")
                                yield Input(placeholder= "Last Name", id = "last-name")
                                yield Input(placeholder= "Grade", id = "grade")
                                yield Button("Submit", id="submit-view")
                                yield Static("", id = "view-submit-status")

                # SETTINGS PAGE
                with Vertical(id="page-settings"):
                    yield Label("SETTINGS", classes="page-title")
                    yield Static("Settings content goes here...")
                
                # ABOUT PAGE
                with VerticalScroll(id="page-about"):
                    yield Label("ABOUT", classes="page-title")
                    yield Static("Grading TUI Application v1.0", classes="welcome-text")

        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        switcher = self.query_one(ContentSwitcher)
        if event.button.id == "nav-notes":
            switcher.current = "page-notes"
        elif event.button.id == "nav-upload":
            switcher.current = "page-upload"
        elif event.button.id == "nav-view":
            switcher.current = "page-view"
        elif event.button.id == "nav-settings":
            switcher.current = "page-settings"
        elif event.button.id == "nav-about":
            switcher.current = "page-about"
        
        if event.button.id == "submit-view":
            first = self.query_one("#first-name" , Input).value
            last = self.query_one("#last-name" , Input).value
            grade = self.query_one("#grade" , Input).value
            status = f"Submitted: {first} {last} -> {grade}"
            self.query_one("#view-submit-status", Static).update(status)
        
        # testing
        if event.button.id == "btn-process-roster":
            if read_roster is None:
                self.query_one("#upload-status", Static).update("Error: Roster management module not available.")
                return
                
            try:
                students = read_roster("data/roster.csv")
                self.query_one("#upload-status", Static).update(f"Loaded {len(students)} students from roster.")
            except Exception as e:
                self.query_one("#upload-status", Static).update(f"Error: {str(e)}")

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
