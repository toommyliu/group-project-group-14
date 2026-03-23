from rich.console import Console, RenderableType
from textual.app import App, ComposeResult
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, Static, Button, Label, ContentSwitcher, Input
from textual.reactive import reactive
from textual_image.widget import Image

from local_grader.core.pdf import pdf_splitter
from local_grader.core.questions import split_pdf_by_rectangles
from local_grader.core.roster import read_roster, display_roster, save_roster_with_grades

class Sidebar(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Label("MENU", id="menu-title")
        yield Button("Documents", id="nav-documents", variant="primary")
        yield Button("Upload", id="nav-upload")
        yield Button("View/Grade", id="nav-view")
        yield Button("Settings", id="nav-settings")
        yield Button("About", id="nav-about")

class GradingTUIManager(App):
    
    grade = reactive(100.0)
    history_counter = 0

    CSS = """
    Screen { background: #1a1a1a; }
    Sidebar {
        width: 25;
        background: #242424;
        border-right: solid $accent;
        padding: 1;
    }
    #menu-title { text-style: bold; margin-bottom: 1; text-align: center; }

    #document-input {
        margin-bottom: 1;
        border: tall $accent;
    }
    
    .document_note { 
        background: $panel; 
        border: solid $accent; 
        margin-bottom: 1; 
        padding: 1; 
    }


    Sidebar Button { width: 100%; margin-bottom: 1; }

    .sidebar-header-text {
        color: cyan;
        text-style: bold;
        padding: 1 2;
        width: 100%;
        text-align: center;
    }

    .sidebar-highlight { border: double #00ffff; }

    ContentSwitcher { padding: 1; }
    .page-title { text-style: bold underline; margin-bottom: 1; color: $accent; }
    
    #total-grade-label {
        text-style: bold;
        margin: 1 0;
        color: $success;
    }
    /* Requirement: Red score if below 70 */
    .low-grade { color: red !important; text-style: bold italic; }

    .input-row { height: auto; margin-bottom: 1; }
    .input-row Input { width: 1fr; margin-right: 1; }
    
    #history-log {
        height: 12;
        border: tall $accent;
        background: $surface;
        margin: 1 0;
        padding: 0 1;
    }
    
    .history-entry {
        border-bottom: double;
        padding: 0 1;
        margin-bottom: 0;
    }

    .delete-row { height: auto; margin-top: 1; }
    #delete-idx-input { width: 15; margin-right: 1; }

    Horizontal#split { height: 100%; border: solid green; }
    VerticalScroll.left-view { width: 50%; padding: 1 2; }
    VerticalScroll.right-view { width: 50%; padding: 1 2; border-left: solid red; }

    #about-image { width: 100%; height: auto; margin-top: 1; }

    /* Requirement: Centered and Wider Submit Button */
    #submit-container {
        align: center middle;
        width: 100%;
        margin-top: 1;
    }
    #submit-db {
        width: 60%;
        min-width: 30;
    }
    #db-status {
        text-align: center;
        width: 100%;
    }
    """

    BINDINGS = [("q", "quit", "Quit"), ("n", "user_input", "Enter Note"), ("c", "clear_all", "Clear Notes")]

    document_name = "Assignment 1"
    document_image_path = "images/Grading.jpg"

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Sidebar()
            with ContentSwitcher(initial="page-documents"):

                # DOCUMENTS PAGE
                with VerticalScroll(id="page-documents"):
                    yield Label("MY DOCUMENTS", classes="page-title")
                    yield Input(placeholder="Type a note and press Enter...", id="document-input")
                    yield Vertical(id="document-container")

                # UPLOAD PAGE
                with VerticalScroll(id="page-upload"):
                    yield Label("UPLOAD DOCUMENTS", classes="page-title")
                    yield Static("Upload documents script should go here...")
                    yield Button("Process Roster (Example)", id="btn-process-roster")
                    yield Static("", id="upload-status")

                # VIEW PAGE
                with VerticalScroll(id="page-view"):
                    yield Label("GRADING DOCUMENT INTERFACE", classes="page-title")
                    with Horizontal(id="split"):  
                        with VerticalScroll(classes="left-view"):
                            yield Image(self.document_image_path, id="about-image")
                        with VerticalScroll(classes="right-view"):
                            yield Label(self.document_name, classes="sidebar-header-text sidebar-highlight")
                            yield Label(f"Total Grade: {self.grade}", id="total-grade-label")
                            
                            with Horizontal(classes="input-row"):
                                yield Input(placeholder="Problem (3i)", id="in-prob")
                                yield Input(placeholder="Penalty", id="in-penalty")
                                yield Input(placeholder="Comment", id="in-comment")
                                yield Button("Add", id="btn-add-penalty", variant="primary")

                            yield Label("Grading History Log:")
                            yield VerticalScroll(id="history-log")

                            with Horizontal(classes="delete-row"):
                                yield Input(placeholder="[ # ]", id="delete-idx-input")
                                yield Button("Delete Line", id="btn-delete-entry", variant="error")

                            # UI Update: Wrapped in Center container for styling
                            with Center(id="submit-container"):
                                yield Button("Submit to Database", id="submit-db", variant="success")
                            yield Static("", id="db-status")

                # SETTINGS PAGE
                with Vertical(id="page-settings"):
                    yield Label("SETTINGS", classes="page-title")
                    yield Static("Settings content goes here...")
                
                # ABOUT PAGE
                with VerticalScroll(id="page-about"):
                    yield Label("ABOUT", classes="page-title")
                    yield Static("Grading TUI Application v1.0", classes="welcome-text")
        yield Footer()

    def watch_grade(self, new_grade: float) -> None:
        try:
            lbl = self.query_one("#total-grade-label", Label)
            lbl.update(f"Total Grade: {new_grade:.1f}")
            # Logic: color red if below 70
            lbl.set_class(new_grade < 70.0, "low-grade")
        except:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        switcher = self.query_one(ContentSwitcher)
        nav_map = {"nav-documents": "page-documents", "nav-upload": "page-upload", "nav-view": "page-view", "nav-settings": "page-settings", "nav-about": "page-about"}
        
        if event.button.id in nav_map:
            switcher.current = nav_map[event.button.id]
        elif event.button.id == "btn-add-penalty":
            self.process_add_penalty()
        elif event.button.id == "btn-delete-entry":
            self.process_delete_entry()
        elif event.button.id == "submit-db":
            self.process_db_submit()

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

    def process_add_penalty(self) -> None:
        prob = self.query_one("#in-prob", Input).value or "N/A"
        penalty_raw = self.query_one("#in-penalty", Input).value or "0"
        comment = self.query_one("#in-comment", Input).value or "No comment"

        try:
            penalty_val = float(penalty_raw)
            self.grade -= penalty_val
            self.history_counter += 1
            
            log_container = self.query_one("#history-log")
            # Store metadata in attributes for re-indexing later
            new_entry = Static("", classes="history-entry", id=f"log-idx-{self.history_counter}")
            new_entry.penalty_amt = penalty_val
            new_entry.prob_name = prob
            new_entry.comment_text = comment
            
            # Use formatting helper
            new_entry.update(f"[{self.history_counter}] Prob: {prob} | -{penalty_val:.1f} | {comment}")
            
            log_container.mount(new_entry)
            log_container.scroll_end()

            for i in ["#in-prob", "#in-penalty", "#in-comment"]:
                self.query_one(i, Input).value = ""
        except ValueError:
            pass

    def process_delete_entry(self) -> None:
        idx_input = self.query_one("#delete-idx-input", Input)
        try:
            target_id = f"#log-idx-{idx_input.value}"
            entry = self.query_one(target_id, Static)
            self.grade += entry.penalty_amt 
            entry.remove()
            idx_input.value = ""
            
            # Call re-indexing after removal
            self.reindex_history()
        except:
            pass 

    def reindex_history(self) -> None:
        """Requirement: Fixes index sequence after a deletion."""
        entries = self.query("#history-log .history-entry")
        self.history_counter = 0
        for entry in entries:
            self.history_counter += 1
            # Update ID for future deletions
            entry.id = f"log-idx-{self.history_counter}"
            # Update displayed text with new index
            entry.update(f"[{self.history_counter}] Prob: {entry.prob_name} | -{entry.penalty_amt:.1f} | {entry.comment_text}")

    def process_db_submit(self) -> None:
        history_items = self.query("#history-log Static")
        status = f"Success: Sent {len(history_items)} entries. Final Grade: {self.grade:.1f}"
        self.query_one("#db-status", Static).update(status)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Triggered when you press Enter in the Input field."""
        if event.value.strip():  # Only add if the note isn't empty
            container = self.query_one("#document-container")
            container.mount(Static(event.value, classes="document_note"))
            
            # Clear the input for the next note
            event.input.value = ""
            # Auto-scroll to the new note
            container.scroll_visible()

    def action_clear_all(self):
        self.query("#document-container Static").remove()

    def action_user_input(self) -> None:
        self.query_one("#document-input").focus()

def run_app():
    GradingTUIManager().run()

if __name__ == "__main__":
    run_app()
