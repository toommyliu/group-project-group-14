from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, RichLog, Select, Static

from opengrader.cli.return_bundle import run_cli as run_return_bundle_cli
from opengrader.tui.ui.screens.path_picker_screen import PathPickerScreen

_CONTENT_OPTIONS: list[tuple[str, str]] = [
    ("Everything (gradebook + PDFs + analysis)", "all"),
    ("Gradebook CSV only", "gradebook"),
    ("Student PDFs only", "student-pdfs"),
    ("Gradebook + student PDFs", "gradebook-student"),
    ("Analysis report only", "analysis"),
]


class ReturnBundleScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    ReturnBundleScreen {
        align: center middle;
    }

    #return-bundle-dialog {
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

    #return-bundle-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    ReturnBundleScreen .field-label {
        width: 100%;
        height: auto;
        margin-top: 1;
    }

    ReturnBundleScreen .path-row {
        height: auto;
    }

    ReturnBundleScreen .path-row Input {
        width: 1fr;
    }

    ReturnBundleScreen .path-row Button {
        min-width: 10;
        width: auto;
        margin-left: 1;
        margin-top: 1;
    }

    #return-bundle-log {
        height: 1fr;
        margin-top: 1;
        border: round $surface-lighten-2;
        border-title-align: left;
        border-title-color: $text-muted;
        background: transparent;
        padding: 0 1;
    }

    #return-bundle-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #return-bundle-content {
        width: 46;
        margin-right: 1;
    }

    #return-bundle-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Close")]

    def __init__(
        self,
        *,
        submissions_path: str = "",
        output_path: str = "",
        default_name: str = "",
        workspace_root: str = "",
        workspace_assignment: str = "",
    ) -> None:
        super().__init__()
        self._submissions_path = submissions_path
        self._initial_output = output_path
        self._default_name = default_name
        self._workspace_root = workspace_root
        self._workspace_assignment = workspace_assignment

    @staticmethod
    def _field_label(text: str, detail: str = "") -> Static:
        markup = f"[b]{text}[/b]"
        if detail:
            markup = f"{markup}  [dim]{detail}[/dim]"
        return Static(markup, classes="field-label")

    def compose(self) -> ComposeResult:
        with Vertical(id="return-bundle-dialog") as dialog:
            dialog.border_title = "▸ Export return bundle"
            yield Static(
                "Package this class's gradebook, annotated student PDFs, and "
                "analysis report into one folder to hand back.",
                id="return-bundle-hint",
            )
            yield self._field_label(
                "Destination", "where the bundle folder is created"
            )
            with Horizontal(classes="path-row"):
                yield Input(
                    value=self._initial_output,
                    placeholder="Folder to create the bundle in…",
                    id="return-bundle-output",
                )
                yield Button("Browse", id="return-bundle-browse", compact=True)
            yield self._field_label("Bundle folder name", "the folder's name")
            yield Input(
                value=self._default_name,
                placeholder="e.g. quiz-1-return-2026-07-28",
                id="return-bundle-name",
            )
            log = RichLog(id="return-bundle-log", wrap=True, auto_scroll=True)
            log.border_title = "Output"
            yield log
            with Horizontal(id="return-bundle-buttons"):
                yield Select(
                    _CONTENT_OPTIONS,
                    value="all",
                    allow_blank=False,
                    id="return-bundle-content",
                )
                yield Button(
                    "Export", variant="primary", id="return-bundle-run", compact=True
                )
                yield Button("Close", id="return-bundle-close", compact=True)

    def on_mount(self) -> None:
        self.query_one("#return-bundle-output", Input).focus()

    @on(Button.Pressed, "#return-bundle-browse")
    def _on_browse_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        output_input = self.query_one("#return-bundle-output", Input)

        start_path: Path | None = None
        current = output_input.value.strip().strip("'\"")
        if current:
            candidate = Path(current).expanduser()
            if candidate.is_dir():
                start_path = candidate
            elif candidate.parent.is_dir():
                start_path = candidate.parent

        def _apply(path: Path | None) -> None:
            if path is None:
                return
            output_input.value = str(path)
            output_input.cursor_position = len(output_input.value)

        self.app.push_screen(
            PathPickerScreen(
                title="Choose the destination folder",
                mode="directory",
                start_path=start_path,
            ),
            _apply,
        )

    @on(Button.Pressed, "#return-bundle-run")
    def _on_run_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if not self._submissions_path and not self._workspace_root:
            self.app.notify("No submissions loaded to export.", severity="error")
            return

        output = self.query_one("#return-bundle-output", Input).value.strip()
        name = self.query_one("#return-bundle-name", Input).value.strip()
        content = self.query_one("#return-bundle-content", Select).value

        argv: list[str]
        if self._workspace_root:
            argv = [
                "--workspace", self._workspace_root,
                "--assignment", self._workspace_assignment,
            ]
        else:
            argv = ["--submissions", self._submissions_path]
        argv += ["--content", str(content)]
        if output:
            argv += ["--output", output]
        if name:
            argv += ["--name", name]

        self.query_one("#return-bundle-run", Button).disabled = True
        log = self.query_one("#return-bundle-log", RichLog)
        log.clear()
        log.write("Building return bundle…")
        self.run_worker(lambda: self._run_export(argv), thread=True, exclusive=True)

    def _run_export(self, argv: list[str]) -> None:
        buffer = io.StringIO()
        succeeded = True
        try:
            with redirect_stdout(buffer), redirect_stderr(buffer):
                run_return_bundle_cli(argv)
        except SystemExit as exc:
            succeeded = exc.code in (0, None)
        except Exception as exc:
            succeeded = False
            buffer.write(f"\nerror: {exc}")
        self.app.call_from_thread(self._show_result, buffer.getvalue(), succeeded)

    def _show_result(self, output: str, succeeded: bool) -> None:
        if not self.is_attached:
            return
        self.query_one("#return-bundle-run", Button).disabled = False
        log = self.query_one("#return-bundle-log", RichLog)
        log.clear()
        log.write(output.strip() or "(no output)")
        if succeeded:
            self.app.notify("Return bundle exported.")
        else:
            self.app.notify("Return bundle export failed — see the log.", severity="error")

    @on(Button.Pressed, "#return-bundle-close")
    def _on_close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
