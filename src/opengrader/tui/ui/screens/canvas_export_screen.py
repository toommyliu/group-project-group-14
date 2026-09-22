from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, RichLog, Static

from opengrader.cli.canvas_export import run_cli as run_canvas_export_cli
from opengrader.tui.ui.screens.path_picker_screen import PathPickerScreen


class CanvasExportScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    CanvasExportScreen {
        align: center middle;
    }

    #canvas-export-dialog {
        width: 80%;
        max-width: 110;
        height: 80%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #canvas-export-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    CanvasExportScreen .field-label {
        width: 100%;
        height: auto;
        margin-top: 1;
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
        margin-top: 1;
    }

    CanvasExportScreen Checkbox {
        margin-top: 1;
    }

    #canvas-export-log {
        height: 1fr;
        margin-top: 1;
        border: round $surface-lighten-2;
        border-title-align: left;
        border-title-color: $text-muted;
        background: transparent;
        padding: 0 1;
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
        workspace_root: str = "",
        workspace_assignment: str = "",
    ) -> None:
        super().__init__()
        self._initial_submissions = submissions_path
        self._initial_course = course
        self._initial_assignment = assignment
        self._workspace_root = workspace_root
        self._workspace_assignment = workspace_assignment

    @staticmethod
    def _field_label(text: str, detail: str = "") -> Static:
        markup = f"[b]{text}[/b]"
        if detail:
            markup = f"{markup}  [dim]{detail}[/dim]"
        return Static(markup, classes="field-label")

    def compose(self) -> ComposeResult:
        with Vertical(id="canvas-export-dialog") as dialog:
            dialog.border_title = "▸ Export grades to Canvas"
            yield Static(
                "Posts scores and per-question comments via canvas-sak.",
                id="canvas-export-hint",
            )
            yield self._field_label(
                "Submissions",
                "leave blank to use saved workspace grades" if self._workspace_root
                else ".submission.md file or directory",
            )
            with Horizontal(classes="path-row"):
                yield Input(
                    value=self._initial_submissions,
                    placeholder="Path to submissions…",
                    id="canvas-export-submissions",
                )
                yield Button("Browse", id="canvas-export-browse", compact=True)
            yield self._field_label("Course", "Canvas course name, fuzzy matched")
            yield Input(
                value=self._initial_course,
                placeholder="e.g. CS146",
                id="canvas-export-course",
            )
            yield self._field_label(
                "Assignment", "Canvas assignment name, fuzzy matched"
            )
            yield Input(
                value=self._initial_assignment,
                placeholder="e.g. Quiz 1",
                id="canvas-export-assignment",
            )
            yield Checkbox(
                "Dry run (preview only, nothing is posted)",
                value=True,
                id="canvas-export-dry-run",
            )
            log = RichLog(id="canvas-export-log", wrap=True, auto_scroll=True)
            log.border_title = "Output"
            yield log
            with Horizontal(id="canvas-export-buttons"):
                yield Button(
                    "Export", variant="primary", id="canvas-export-run", compact=True
                )
                yield Button("Close", id="canvas-export-close", compact=True)

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

        if (not submissions and not self._workspace_root) or not course or not assignment:
            self.app.notify(
                "Enter a submissions path, course, and assignment.",
                severity="error",
            )
            return

        argv: list[str] = []
        if submissions:
            argv += ["--submissions", submissions]
        else:
            argv += [
                "--workspace", self._workspace_root,
                "--workspace-assignment", self._workspace_assignment,
            ]
        argv += ["--course", course, "--assignment", assignment]
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
        buffer = io.StringIO()
        succeeded = True
        try:
            with redirect_stdout(buffer), redirect_stderr(buffer):
                run_canvas_export_cli(argv)
        except SystemExit as exc:
            succeeded = exc.code in (0, None)
        except Exception as exc:
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
