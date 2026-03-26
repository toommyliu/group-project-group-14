import os
import tempfile
from argparse import ArgumentParser
from pathlib import Path

import fitz
from rich.console import Console, RenderableType
from textual.app import App, ComposeResult
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Button, ContentSwitcher, Footer, Header, Input, Label, Static
from textual_image.widget import Image

from local_grader.core.pdf import pdf_splitter
from local_grader.core.questions import split_pdf_by_rectangles
from local_grader.core.roster import display_roster, read_roster, save_roster_with_grades

from local_grader.core.ocr.extractor import render_page_to_image

DEFAULT_QUESTION_PDF_DIR = Path("tests/question_split_output")
QUESTION_PDF_DIR_ENV = "LOCAL_GRADER_QUESTION_PDF_DIR"


class Sidebar(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Label("MENU", id="menu-title")
        yield Button("Documents", id="nav-documents", variant="primary")
        yield Button("Upload", id="nav-upload")
        yield Button("View/Grade", id="nav-view")
        yield Button("Settings", id="nav-settings")
        yield Button("About", id="nav-about")


class GradingTUIManager(App):
    CSS_PATH = "tui_interface_style.tcss"

    grade = reactive(100.0)
    history_counter = 0
    # Added to track unique widget IDs regardless of deletions
    total_entries_created = 0

    BINDINGS = [
        ("q", "quit", "Quit"), 
        ("n", "user_input", "Enter Note"), 
        ("c", "clear_all", "Clear Notes"),
        ("left", "previous_question", "Previous Question"),
        ("right", "next_question", "Next Question")
        ]
    def action_prev_question(self) -> None:
        self.change_question(-1)

    def action_next_question(self) -> None:
        self.change_question(1)

    document_name = "Assignment 1"
    document_image_path = "images/Grading.jpg"

    def __init__(self, question_pdf_dir: Path | None = None, **kwargs):
        super().__init__(**kwargs)
        self.question_pdf_dir = question_pdf_dir or DEFAULT_QUESTION_PDF_DIR
        self.question_pdfs: list[Path] = []
        self.current_question_index = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Sidebar()
            with ContentSwitcher(initial="page-documents"):

                # DOCUMENTS PAGE
                with VerticalScroll(id="page-documents"):
                    yield Label("MY DOCUMENTS", classes="page-title")
                    yield Input(placeholder="Type a note and press Enter...", id="document-input")
                    yield VerticalScroll(id="document-container")

                # UPLOAD PAGE
                with VerticalScroll(id="page-upload"):
                    yield Label("UPLOAD DOCUMENTS", classes="page-title")
                    yield Static("Upload documents script should go here...")
                    yield Button("Process Roster (Example)", id="btn-process-roster")
                    yield Static("", id="upload-status")
                    yield Label("Question Split PDF Directory")
                    yield Input(str(self.question_pdf_dir), id="question-dir-input")
                    yield Button("Load Question PDFs", id="btn-load-question-pdfs")
                    yield Static("", id="question-dir-status")

                # VIEW PAGE
                with VerticalScroll(id="page-view"):
                    yield Label("GRADING DOCUMENT INTERFACE", classes="page-title")
                    with Horizontal(id="split"):
                        with VerticalScroll(classes="left-view"):
                            yield Image(self.document_image_path, id="about-image")
                        with VerticalScroll(classes="right-view"):
                            yield Label(
                                self.document_name,
                                id="document-name-label",
                                classes="sidebar-header-text sidebar-highlight",
                            )
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

                            # Wrapped in Center container for styling
                            with Center(id="submit-container"):
                                with Horizontal():
                                    yield Button("Previous", id="btn-prev-doc", classes="nav-btn")
                                    yield Button("Submit to Database", id="submit-db", variant="success")
                                    yield Button("Next", id="btn-next-doc", classes="nav-btn")
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

    def on_mount(self) -> None:
        self._load_question_pdfs()
        self._show_current_question()
        self._update_question_dir_status()

    def watch_grade(self, new_grade: float) -> None:
        try:
            lbl = self.query_one("#total-grade-label", Label)
            lbl.update(f"Total Grade: {new_grade:.1f}")
            # Color red if below 70
            lbl.set_class(new_grade < 70.0, "low-grade")
        except:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        switcher = self.query_one(ContentSwitcher)
        nav_map = {
            "nav-documents": "page-documents",
            "nav-upload": "page-upload",
            "nav-view": "page-view",
            "nav-settings": "page-settings",
            "nav-about": "page-about",
        }

        if event.button.id in nav_map:
            switcher.current = nav_map[event.button.id]
        elif event.button.id == "btn-add-penalty":
            self.process_add_penalty()
        elif event.button.id == "btn-delete-entry":
            self.process_delete_entry()
        elif event.button.id == "submit-db":
            self.process_db_submit()
        elif event.button.id == "btn-prev-doc":
            self.change_question(-1)
        elif event.button.id == "btn-next-doc":
            self.change_question(1)
        elif event.button.id == "btn-load-question-pdfs":
            self.set_question_dir_from_input()

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
            self.total_entries_created += 1

            log_container = self.query_one("#history-log")
            # Store metadata in attributes for re-indexing later
            # Using total_entries_created for ID to ensure uniqueness
            new_entry = Static("", classes="history-entry", id=f"log-widget-{self.total_entries_created}")
            new_entry.penalty_amt = penalty_val
            new_entry.prob_name = prob
            new_entry.comment_text = comment
            new_entry.display_idx = self.history_counter

            # Use formatting helper
            new_entry.update(f"[{new_entry.display_idx}] Prob: {prob} | -{penalty_val:.1f} | {comment}")

            log_container.mount(new_entry)
            log_container.scroll_end()

            for i in ["#in-prob", "#in-penalty", "#in-comment"]:
                self.query_one(i, Input).value = ""
        except ValueError:
            pass

    def process_delete_entry(self) -> None:
        idx_input = self.query_one("#delete-idx-input", Input)
        try:
            target_display_idx = int(idx_input.value)
            entries = self.query("#history-log .history-entry")

            for entry in entries:
                if entry.display_idx == target_display_idx:
                    self.grade += entry.penalty_amt
                    entry.remove()
                    idx_input.value = ""
                    # Call re-indexing after removal
                    self.reindex_history()
                    break
        except:
            pass

    def reindex_history(self) -> None:
        """ Index sequence after a deletion. """
        entries = self.query("#history-log .history-entry")
        self.history_counter = 0
        for entry in entries:
            self.history_counter += 1
            # Update display metadata and text, NOT the widget ID
            entry.display_idx = self.history_counter
            entry.update(f"[{entry.display_idx}] Prob: {entry.prob_name} | -{entry.penalty_amt:.1f} | {entry.comment_text}")

    def process_db_submit(self) -> None:
        """Triggered when Submit to Database is clicked. Currently just simulates submission and updates status."""
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

    def _load_question_pdfs(self) -> None:
        if not self.question_pdf_dir.is_dir():
            self.question_pdfs = []
            self.current_question_index = 0
            return

        self.question_pdfs = sorted(path for path in self.question_pdf_dir.glob("*.pdf") if path.is_file())
        self.current_question_index = 0

    def _render_pdf_preview(self, pdf_path: Path, page_num: int = 0) -> Path:
        cache_dir = Path(tempfile.gettempdir()) / "local_grader_tui_preview"
        cache_dir.mkdir(parents=True, exist_ok=True)
        output_path = cache_dir / f"{pdf_path.stem}_page{page_num}.png"

        doc = fitz.open(str(pdf_path))
        try:
            page = doc[page_num]
            pix = page.get_pixmap(matrix=fitz.Matrix(5, 5), alpha=False)

            from PIL import Image, ImageChops

            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            bg = Image.new("RGB", img.size, (255, 255, 255))
            diff = ImageChops.difference(img, bg)
            bbox = diff.getbbox()

            if bbox:
                pad = 20
                x0, y0, x1, y1 = bbox
                x0 = max(0, x0 - pad)
                y0 = max(0, y0 - pad)
                x1 = min(img.width, x1 + pad)
                y1 = min(img.height, y1 + pad)
                img = img.crop((x0, y0, x1, y1))

            img.save(output_path)
        finally:
            doc.close()

        return output_path

    def _set_preview_image(self, image_path: Path) -> None:
        image_widget = self.query_one("#about-image", Image)
        image_widget.image = str(image_path)

    def _show_current_question(self) -> None:
        title = self.query_one("#document-name-label", Label)

        if not self.question_pdfs:
            title.update("No split question PDFs found")
            return

        current_pdf = self.question_pdfs[self.current_question_index]
        title.update(f"{current_pdf.stem} ({self.current_question_index + 1}/{len(self.question_pdfs)})")

        preview_path = self._render_pdf_preview(current_pdf)
        self._set_preview_image(preview_path)

    def _update_question_dir_status(self) -> None:
        status = self.query_one("#question-dir-status", Static)
        if self.question_pdfs:
            status.update(f"Loaded {len(self.question_pdfs)} PDF(s) from {self.question_pdf_dir}")
        elif self.question_pdf_dir.is_dir():
            status.update(f"No PDFs found in {self.question_pdf_dir}")
        else:
            status.update(f"Directory not found: {self.question_pdf_dir}")

    def set_question_dir_from_input(self) -> None:
        raw_path = self.query_one("#question-dir-input", Input).value.strip()
        if not raw_path:
            self.query_one("#question-dir-status", Static).update("Please enter a directory path.")
            return

        self.question_pdf_dir = Path(raw_path).expanduser()
        self._load_question_pdfs()
        self._show_current_question()
        self._update_question_dir_status()

    def change_question(self, step: int) -> None:
        if not self.question_pdfs:
            return
        self.current_question_index = (self.current_question_index + step) % len(self.question_pdfs)
        self._show_current_question()


def setup_args() -> Path:
    parser = ArgumentParser(description="Run Local Grader TUI")
    parser.add_argument(
        "--question-dir",
        type=str,
        default=None,
        help=f"Directory containing split question PDFs (default: {DEFAULT_QUESTION_PDF_DIR})",
    )
    args, _unknown = parser.parse_known_args()

    question_dir = args.question_dir or os.environ.get(QUESTION_PDF_DIR_ENV)
    if question_dir:
        return Path(question_dir).expanduser()
    return DEFAULT_QUESTION_PDF_DIR


def run_app():
    question_dir = setup_args()
    GradingTUIManager(question_pdf_dir=question_dir).run()


if __name__ == "__main__":
    run_app()
