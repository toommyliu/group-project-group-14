"""Modal file-system browser used to fill path inputs from a click."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Literal

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Label, Static, Tree

PathPickMode = Literal["file", "directory", "any"]


class _FilteredDirectoryTree(DirectoryTree):
    """Directory tree that hides dotfiles."""

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [path for path in paths if not path.name.startswith(".")]


class PathPickerScreen(ModalScreen[Path | None]):
    """Browse the file system and dismiss with the chosen path, or None.

    ``mode`` controls what can be confirmed: a file, a directory, or any
    path. Selecting a file in the tree confirms immediately (file/any modes);
    directories are confirmed with the Select button so clicking them still
    expands the tree while navigating.
    """

    DEFAULT_CSS = """
    PathPickerScreen {
        align: center middle;
    }

    #path-picker-dialog {
        width: 80%;
        max-width: 100;
        height: 80%;
        background: $surface;
        border: thick $accent;
        padding: 1 2;
    }

    #path-picker-title {
        width: 100%;
        content-align: center middle;
        text-style: bold;
    }

    #path-picker-tree {
        height: 1fr;
        margin: 1 0;
        border: solid $primary;
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

    def compose(self) -> ComposeResult:
        with Vertical(id="path-picker-dialog"):
            yield Label(self._title, id="path-picker-title")
            yield _FilteredDirectoryTree(self._start_path, id="path-picker-tree")
            yield Static(str(self._start_path), id="path-picker-current")
            with Horizontal(id="path-picker-buttons"):
                yield Button("Select", variant="primary", id="path-picker-select")
                yield Button("Cancel", id="path-picker-cancel")

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
