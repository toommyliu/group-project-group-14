from __future__ import annotations

from pathlib import Path
from typing import Iterable, Literal

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Static, Tree
from opengrader.tui.ui.button_roles import OutlineButtons

PathPickMode = Literal["file", "directory", "any"]


class _FilteredDirectoryTree(DirectoryTree):
    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [path for path in paths if not path.name.startswith(".")]


class PathPickerScreen(OutlineButtons, ModalScreen[Path | None]):
    DEFAULT_CSS = """
    PathPickerScreen {
        align: center middle;
    }

    #path-picker-dialog {
        width: 80%;
        max-width: 100;
        height: 80%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #path-picker-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #path-picker-tree {
        height: 1fr;
        margin: 1 0;
        border: round $surface-lighten-2;
        background: transparent;
        padding: 0 1;
    }

    #path-picker-tree:focus {
        border: round $accent;
    }

    #path-picker-current {
        height: 1;
        color: $text-muted;
    }

    #path-picker-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #path-picker-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        *,
        title: str,
        mode: PathPickMode,
        start_path: Path | None = None,
    ) -> None:
        super().__init__()
        self._title = title
        self._mode = mode
        start = (start_path or Path.home()).expanduser()
        if not start.is_dir():
            start = Path.home()
        self._start_path = start

    _MODE_HINTS: dict[PathPickMode, str] = {
        "file": "Click a file to choose it.",
        "directory": "Highlight a directory, then press Select.",
        "any": "Click a file to choose it, or highlight a directory and press Select.",
    }

    def compose(self) -> ComposeResult:
        with Vertical(id="path-picker-dialog") as dialog:
            dialog.border_title = f"▸ {self._title}"
            yield Static(self._MODE_HINTS[self._mode], id="path-picker-hint")
            yield _FilteredDirectoryTree(self._start_path, id="path-picker-tree")
            yield Static(str(self._start_path), id="path-picker-current")
            with Horizontal(id="path-picker-buttons"):
                yield Button(
                    "Select", variant="primary", id="path-picker-select", compact=True
                )
                yield Button("Cancel", id="path-picker-cancel", compact=True)

    def on_mount(self) -> None:
        self.query_one("#path-picker-tree", _FilteredDirectoryTree).focus()

    def _cursor_path(self) -> Path | None:
        tree = self.query_one("#path-picker-tree", _FilteredDirectoryTree)
        node = tree.cursor_node
        if node is None or node.data is None:
            return None
        return Path(node.data.path)

    @on(DirectoryTree.FileSelected)
    def _on_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        if self._mode in ("file", "any"):
            event.stop()
            self.dismiss(event.path)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        if event.node.data is not None:
            self.query_one("#path-picker-current", Static).update(
                str(event.node.data.path)
            )

    @on(Button.Pressed, "#path-picker-select")
    def _on_select_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        path = self._cursor_path()
        if path is None:
            return
        if self._mode == "file" and not path.is_file():
            self.app.notify("Select a file, not a directory.", severity="warning")
            return
        if self._mode == "directory" and not path.is_dir():
            self.app.notify("Select a directory, not a file.", severity="warning")
            return
        self.dismiss(path)

    @on(Button.Pressed, "#path-picker-cancel")
    def _on_cancel_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
