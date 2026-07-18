from __future__ import annotations
from pathlib import Path
from typing import Literal

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.message import Message
from textual.widgets import (
    Button,
    Checkbox,
    Input,
    Label,
    Static,
    Collapsible,
)

from opengrader.core.pdf.splitter import pdf_splitter
from opengrader.cli.submission_extractor import run_cli as run_extractor_cli
from opengrader.cli.pdf_to_qa_solutions_template import run_cli as run_qa_gen_cli
from opengrader.cli.automated_grading import run_cli as run_grading_cli
from opengrader.tui.ui.screens.path_picker_screen import PathPickerScreen, PathPickMode

class SetupScreen(Vertical):
    """
    A setup screen allowing users to configure and run processing scripts
    before launching the grading TUI.
    """

    DEFAULT_CSS = """
    /* Containers inside the scroll view must size to their content;
       a Vertical's default 1fr height clips children and breaks the
       scroll range. */
    SetupScreen #launch-section {
        height: auto;
    }

    SetupScreen .path-row {
        height: auto;
    }

    SetupScreen .path-row Input {
        width: 1fr;
    }

    SetupScreen .path-row .browse-button {
        min-width: 10;
        width: auto;
        margin-left: 1;
    }

    SetupScreen .flag-input-group > .path-row {
        width: 1fr;
    }
    """

    # Path-taking inputs: input id -> (what the picker may confirm, dialog title).
    _PATH_FIELDS: dict[str, tuple[PathPickMode, str]] = {
        "split-input": ("file", "Choose the scanned submissions PDF"),
        "split-output": ("directory", "Choose the split output directory"),
        "split-template-path": ("file", "Choose the blank template PDF"),
        "ext-input": ("directory", "Choose the directory of split PDFs"),
        "ext-output": ("directory", "Choose the extraction output directory"),
        "ext-template-path": ("file", "Choose the blank template PDF"),
        "ext-id-path": ("file", "Choose the roster/ID file"),
        "qa-gen-input": ("file", "Choose the answer key PDF"),
        "qa-gen-output": ("any", "Choose the output file or directory"),
        "grade-solutions": ("file", "Choose the solutions markdown file"),
        "grade-input": ("any", "Choose the submissions file or directory"),
        "tui-qa-path": ("file", "Choose the QA markdown file"),
        "tui-sub-path": ("any", "Choose the submissions file or directory"),
    }

    class LaunchRequested(Message):
        """Emitted when the user wants to launch the TUI with specific paths."""
        def __init__(self, qa_path: str, submissions_path: str) -> None:
            super().__init__()
            self.qa_path = qa_path
            self.submissions_path = submissions_path

    def _path_row(
        self,
        placeholder: str,
        input_id: str,
        *,
        hidden: bool = False,
    ) -> Horizontal:
        """A path Input with a Browse button that opens the file picker."""
        classes = "path-row hidden" if hidden else "path-row"
        return Horizontal(
            Input(placeholder=placeholder, id=input_id, classes="path-input"),
            Button("Browse", id=f"browse-{input_id}", classes="browse-button"),
            classes=classes,
            id=f"{input_id}-row",
        )

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="setup-container"):
            yield Label("Opengrader Setup & Pipeline", id="setup-title")
            yield Static("Configure paths for processing scripts or skip to launch the TUI directly.", id="setup-subtitle")

            with Collapsible(title="Submission Splitter", id="section-splitter", collapsed=False):
                yield self._path_row("Input PDF Path", "split-input")
                yield self._path_row("Output Directory", "split-output")
                yield Input(placeholder="Pages per submission", id="split-pages")
                yield Checkbox("Reorder pages (requires OCR)", id="split-reorder")
                with Horizontal(classes="flag-input-group"):
                    yield Checkbox("Use Blank Template", id="split-use-template")
                    yield self._path_row("Template PDF Path", "split-template-path", hidden=True)
                yield Button("Run Splitter", id="run-splitter", variant="primary")

            with Collapsible(title="Submission Extractor", id="section-extractor", collapsed=False):
                yield self._path_row("Input Directory (Split PDFs)", "ext-input")
                yield self._path_row("Output Directory", "ext-output")
                with Horizontal(classes="flag-input-group"):
                    yield Checkbox("Use Blank Template", id="ext-use-template")
                    yield self._path_row("Template PDF Path", "ext-template-path", hidden=True)
                with Horizontal(classes="flag-input-group"):
                    yield Checkbox("Use Roster (ID File)", id="ext-use-id")
                    yield self._path_row("ID File Path (.tsv)", "ext-id-path", hidden=True)
                yield Button("Run Extractor", id="run-extractor", variant="primary")

            with Collapsible(title="PDF to QA Solution Draft", id="section-pdf-to-qa", collapsed=False):
                yield self._path_row("Answer Key PDF Path", "qa-gen-input")
                yield self._path_row("Output QA Markdown Path", "qa-gen-output")
                yield Input(placeholder="Assignment Title", id="qa-gen-title")
                yield Button("Run QA Generator", id="run-qa-gen", variant="primary")

            with Collapsible(title="Automated Grading", id="section-grading", collapsed=False):
                yield self._path_row("Solutions Markdown Path", "grade-solutions")
                yield self._path_row("Student Submissions Directory", "grade-input")
                yield Button("Run Auto-Grader", id="run-grading", variant="primary")

            yield Static("", id="setup-spacer")

            with Vertical(id="launch-section"):
                yield Label("TUI Launch Configuration")
                yield self._path_row("QA Markdown File Path (e.g., solutions.md)", "tui-qa-path")
                yield self._path_row("Student Submissions Directory (containing .submission.md files)", "tui-sub-path")
                yield Button("Launch Grading TUI", variant="success", id="launch-button")

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """Toggle visibility of related path inputs when checkboxes are toggled."""
        if event.checkbox.id == "split-use-template":
            self.query_one("#split-template-path-row").set_class(not event.value, "hidden")
        elif event.checkbox.id == "ext-use-template":
            self.query_one("#ext-template-path-row").set_class(not event.value, "hidden")
        elif event.checkbox.id == "ext-use-id":
            self.query_one("#ext-id-path-row").set_class(not event.value, "hidden")

    def _open_path_picker(self, input_id: str) -> None:
        """Open the modal file browser and fill the input with the result."""
        mode, title = self._PATH_FIELDS[input_id]
        input_widget = self.query_one(f"#{input_id}", Input)

        start_path: Path | None = None
        current = self._clean_path(input_widget.value)
        if current:
            candidate = Path(current)
            if candidate.is_dir():
                start_path = candidate
            elif candidate.parent.is_dir():
                start_path = candidate.parent

        def _apply(path: Path | None) -> None:
            if path is None:
                return
            input_widget.value = str(path)
            input_widget.cursor_position = len(input_widget.value)

        self.app.push_screen(
            PathPickerScreen(title=title, mode=mode, start_path=start_path),
            _apply,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("browse-"):
            input_id = button_id.removeprefix("browse-")
            if input_id in self._PATH_FIELDS:
                event.stop()
                self._open_path_picker(input_id)
                return
        if event.button.id == "launch-button":
            qa_path = self._clean_path(self.query_one("#tui-qa-path", Input).value)
            sub_path = self._clean_path(self.query_one("#tui-sub-path", Input).value)
            
            if not qa_path or not sub_path:
                self.app.notify("Both QA path and Submissions path are required to launch.", severity="error")
                return
            
            self.post_message(self.LaunchRequested(qa_path, sub_path))
        elif event.button.id == "run-splitter":
            self._run_splitter()
        elif event.button.id == "run-extractor":
            self._run_extractor()
        elif event.button.id == "run-qa-gen":
            self._run_qa_gen()
        elif event.button.id == "run-grading":
            self._run_grading()

    def _run_splitter(self) -> None:
        input_pdf = self._clean_path(self.query_one("#split-input", Input).value)
        output_dir = self._clean_path(self.query_one("#split-output", Input).value)
        pages = self.query_one("#split-pages", Input).value
        if not input_pdf:
            self.app.notify("Input PDF path is required.", severity="error")
            return
        try:
            pages_val = int(pages) if pages else 1
            out_path = Path(output_dir) if output_dir else None
            pdf_splitter(Path(input_pdf), pages_per_chunk=pages_val, output_dir=out_path)
            self.app.notify(f"Successfully split {input_pdf}")
        except Exception as e:
            self.app.notify(f"Splitter error: {e}", severity="error")

    def _run_extractor(self) -> None:
        input_dir = self._clean_path(self.query_one("#ext-input", Input).value)
        output_dir = self._clean_path(self.query_one("#ext-output", Input).value)
        if not input_dir or not output_dir:
            self.app.notify("Both input and output directories are required.", severity="error")
            return

        argv = ["--input", input_dir, "--output", output_dir]
        
        if self.query_one("#ext-use-template", Checkbox).value:
            template = self._clean_path(self.query_one("#ext-template-path", Input).value)
            if template:
                argv.extend(["--blank-template", template])

        if self.query_one("#ext-use-id", Checkbox).value:
            id_path = self._clean_path(self.query_one("#ext-id-path", Input).value)
            if id_path:
                argv.extend(["--id-file", id_path])

        try:
            # Auto-ollama is preferred for setup TUI
            argv.append("--auto-ollama")
            run_extractor_cli(argv)
            self.app.notify("Extraction complete.")
        except Exception as e:
            self.app.notify(f"Extractor error: {e}", severity="error")

    def _run_qa_gen(self) -> None:
        input_pdf = self._clean_path(self.query_one("#qa-gen-input", Input).value)
        output_md = self._clean_path(self.query_one("#qa-gen-output", Input).value)
        title = self.query_one("#qa-gen-title", Input).value
        
        if not input_pdf or not output_md:
            self.app.notify("Input PDF and Output Markdown paths are required.", severity="error")
            return

        argv = ["--input", input_pdf, "--output", output_md]
        if title:
            argv.extend(["--title", title])

        try:
            argv.append("--auto-ollama")
            run_qa_gen_cli(argv)
            self.app.notify(f"QA Solutions generated at {output_md}")
        except Exception as e:
            self.app.notify(f"QA Gen error: {e}", severity="error")

    def _run_grading(self) -> None:
        solutions = self._clean_path(self.query_one("#grade-solutions", Input).value)
        input_dir = self._clean_path(self.query_one("#grade-input", Input).value)
        
        if not solutions or not input_dir:
            self.app.notify("Solutions file and Student Submissions directory are required.", severity="error")
            return

        argv = ["--solutions", solutions, "--input", input_dir]
        try:
            run_grading_cli(argv)
            self.app.notify("Automated grading complete.")
        except Exception as e:
            self.app.notify(f"Grading error: {e}", severity="error")

    def _clean_path(self, path_str: str) -> str:
        """Normalize path strings by removing quotes and resolving shell-style escapes."""
        p = path_str.strip()
        if not p:
            return ""
        # Strip both single and double quotes often included when copying paths
        p = p.strip("'\"")
        # Handle shell-style escaped spaces and expand home directory
        return str(Path(p.replace("\\ ", " ")).expanduser())