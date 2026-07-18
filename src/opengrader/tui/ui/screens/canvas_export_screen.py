"""Modal dialog that runs the canvas-export CLI from inside the TUI."""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, RichLog

from opengrader.cli.canvas_export import run_cli as run_canvas_export_cli
from opengrader.tui.ui.screens.path_picker_screen import PathPickerScreen


class CanvasExportScreen(ModalScreen[None]):
    """Collect canvas-export flags, run the export, and show its output.

    The export shells out to canvas-sak (network) per student, so it runs in
    a thread worker; the CLI's stdout/stderr are captured and shown in the
    dialog's log. Dry run is on by default because a real run posts grades
    to Canvas.
    """

    DEFAULT_CSS = """
    CanvasExportScreen {
        align: center middle;
    }

    #canvas-export-dialog {
        width: 80%;
        max-width: 110;
        height: 80%;
        background: $surface;
        border: thick $accent;
        padding: 1 2;
    }

    #canvas-export-title {
        width: 100%;
        content-align: center middle;
        text-style: bold;
        margin-bottom: 1;
    }

    CanvasExportScreen .path-row {
        height: auto;
    }

    CanvasExportScreen .path-row Input {
        width: 1fr;
    }

    CanvasExportScreen .path-row Button {
        min-width: 10;
        width: auto;
        margin-left: 1;
    }

    #canvas-export-log {
        height: 1fr;
        margin-top: 1;
        border: solid $primary;
    }

    #canvas-export-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #canvas-export-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Close")]

    def __init__(
        self,
        *,
        submissions_path: str = "",
        course: str = "",
        assignment: str = "",
    ) -> None:
        super().__init__()
        self._initial_submissions = submissions_path
        self._initial_course = course
        self._initial_assignment = assignment

    def compose(self) -> ComposeResult:
        with Vertical(id="canvas-export-dialog"):
            yield Label("Export Grades to Canvas", id="canvas-export-title")
            with Horizontal(classes="path-row"):
                yield Input(
                    value=self._initial_submissions,
                    placeholder="Submissions file or directory",
                    id="canvas-export-submissions",
                )
                yield Button("Browse", id="canvas-export-browse")
            yield Input(
                value=self._initial_course,
                placeholder="Canvas course name (fuzzy matched)",
                id="canvas-export-course",
            )
            yield Input(
                value=self._initial_assignment,
                placeholder="Canvas assignment name (fuzzy matched)",
                id="canvas-export-assignment",
            )
            yield Checkbox(
                "Dry run (preview only, nothing is posted)",
                value=True,
                id="canvas-export-dry-run",
            )
            yield RichLog(id="canvas-export-log", wrap=True, auto_scroll=True)
            with Horizontal(id="canvas-export-buttons"):
                yield Button("Export", variant="primary", id="canvas-export-run")
                yield Button("Close", id="canvas-export-close")

    def on_mount(self) -> None:
        self.query_one("#canvas-export-submissions", Input).focus()

    @on(Button.Pressed, "#canvas-export-browse")
    def _on_browse_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        submissions_input = self.query_one("#canvas-export-submissions", Input)

        start_path: Path | None = None
        current = submissions_input.value.strip().strip("'\"")
        if current:
            candidate = Path(current).expanduser()
            if candidate.is_dir():
                start_path = candidate
            elif candidate.parent.is_dir():
                start_path = candidate.parent

        def _apply(path: Path | None) -> None:
            if path is None:
                return
            submissions_input.value = str(path)
            submissions_input.cursor_position = len(submissions_input.value)

        self.app.push_screen(
            PathPickerScreen(
                title="Choose the submissions file or directory",
                mode="any",
                start_path=start_path,
            ),
            _apply,
        )

    @on(Button.Pressed, "#canvas-export-run")
    def _on_run_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        submissions = self.query_one("#canvas-export-submissions", Input).value.strip()
        course = self.query_one("#canvas-export-course", Input).value.strip()
        assignment = self.query_one("#canvas-export-assignment", Input).value.strip()

        if not submissions or not course or not assignment:
            self.app.notify(
                "Submissions path, course, and assignment are all required.",
                severity="error",
            )
            return

        argv = [
            "--submissions", submissions,
            "--course", course,
            "--assignment", assignment,
        ]
        if self.query_one("#canvas-export-dry-run", Checkbox).value:
            argv.append("--dry-run")

        self.query_one("#canvas-export-run", Button).disabled = True
        log = self.query_one("#canvas-export-log", RichLog)
        log.clear()
        log.write("Running canvas-export…")
        self.run_worker(
            lambda: self._run_export(argv), thread=True, exclusive=True
        )

    def _run_export(self, argv: list[str]) -> None:
        """Worker thread: run the CLI with its output captured."""
        buffer = io.StringIO()
        succeeded = True
        try:
            with redirect_stdout(buffer), redirect_stderr(buffer):
                run_canvas_export_cli(argv)
        except SystemExit as exc:
            succeeded = exc.code in (0, None)
        except Exception as exc:  # surface unexpected failures in the log
            succeeded = False
            buffer.write(f"\nerror: {exc}")
        self.app.call_from_thread(self._show_result, buffer.getvalue(), succeeded)

    def _show_result(self, output: str, succeeded: bool) -> None:
        if not self.is_attached:
            return
        self.query_one("#canvas-export-run", Button).disabled = False
        log = self.query_one("#canvas-export-log", RichLog)
        log.clear()
        log.write(output.strip() or "(no output)")
        if succeeded:
            self.app.notify("Canvas export finished.")
        else:
            self.app.notify("Canvas export failed — see the log.", severity="error")

    @on(Button.Pressed, "#canvas-export-close")
    def _on_close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
