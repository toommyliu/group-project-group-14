from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView
from textual.worker import get_current_worker


class _DirEntry(NamedTuple):
    path: Path
    is_directory: bool
    display: str


class _DirListItem(ListItem):
    def __init__(self, entry: _DirEntry) -> None:
        self.entry = entry
        super().__init__(Label(f"{entry.display}{'/' if entry.is_directory else ''}"))


class FilePicker(ModalScreen[Path | None]):
    CSS_PATH = "file_picker.tcss"

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("full_stop", "toggle_hidden", "Toggle hidden", show=False),
    ]

    ESCAPE_TO_MINIMIZE = True

    def __init__(
        self,
        *,
        title: str = "Select File",
        select_label: str = "Select",
        file_filter: Callable[[Path], bool] | None = None,
        location: str | Path = ".",
        must_exist: bool = True,
    ) -> None:
        super().__init__()
        self._title = title
        self._select_label = select_label
        self._file_filter = file_filter
        self._location = Path(location).expanduser().resolve()
        self._must_exist = must_exist
        self._entries: list[_DirEntry] = []
        self._show_hidden = False
        self._path_bar_visible = False

    def compose(self) -> ComposeResult:
        with Vertical(id="file-picker-panel") as panel:
            panel.border_title = self._title
            yield Input(
                placeholder="Paste or type a path…",
                id="file-picker-path-input",
            )
            yield ListView(id="file-picker-dir-list")
            with Horizontal(id="file-picker-actions"):
                yield Label(
                    "Esc · Cancel",
                    id="file-picker-cancel",
                    classes="file-picker-action",
                )
                yield Label(
                    f"Enter · {self._select_label}",
                    id="file-picker-select",
                    classes="file-picker-action",
                )

    def on_mount(self) -> None:
        lv = self.query_one("#file-picker-dir-list", ListView)
        lv.border_title = "Files"

        path_input = self.query_one("#file-picker-path-input", Input)
        path_input.display = False
        self._path_bar_visible = False

        lv.focus()
        self._load_directory()

    def _show_path_bar(self, *, prefill: str = "") -> None:
        path_input = self.query_one("#file-picker-path-input", Input)
        path_input.value = prefill or str(self._location)
        path_input.display = True
        self._path_bar_visible = True
        path_input.focus()

    def _hide_path_bar(self) -> None:
        path_input = self.query_one("#file-picker-path-input", Input)
        path_input.display = False
        self._path_bar_visible = False
        self.query_one("#file-picker-dir-list", ListView).focus()

    @work(exclusive=True, thread=True)
    def _load_directory(self) -> None:
        worker = get_current_worker()
        entries: list[_DirEntry] = []
        try:
            for child in self._location.iterdir():
                if worker.is_cancelled:
                    return
                try:
                    is_dir = child.is_dir()
                except PermissionError:
                    continue

                if not self._show_hidden and child.name.startswith("."):
                    continue

                if is_dir:
                    entries.append(_DirEntry(child, True, child.name))
                else:
                    if self._file_filter is not None and not self._file_filter(child):
                        continue
                    entries.append(_DirEntry(child, False, child.name))
        except PermissionError:
            self.app.call_from_thread(self._set_error, "Permission denied")
            return

        entries.sort(key=lambda e: (not e.is_directory, e.display.lower()))
        self.app.call_from_thread(self._populate_list, entries)

    async def _populate_list(self, entries: list[_DirEntry]) -> None:
        self._entries = entries
        lv = self.query_one("#file-picker-dir-list", ListView)

        await lv.clear()

        items = []

        # Always show ".." unless at filesystem root
        parent = self._location.parent.resolve()
        if parent != self._location:
            items.append(_DirListItem(_DirEntry(parent, True, "..")))

        for entry in entries:
            items.append(_DirListItem(entry))

        await lv.extend(items)

        lv.border_title = str(self._location)

        total = len(lv.children)
        lv.border_subtitle = f"0 of {total}" if total == 0 else f"1 of {total}"

        if len(lv.children) > 0:
            self.call_after_refresh(self._settle_highlight)

        self._set_error("")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not isinstance(event.item, _DirListItem):
            return
        entry = event.item.entry
        if entry.is_directory:
            self._navigate_to(entry.path)
        else:
            self._select_file(entry.path)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        lv = self.query_one("#file-picker-dir-list", ListView)
        total = len(lv.children)
        if total == 0:
            lv.border_subtitle = "0 of 0"
            return
        current = 0
        if lv.highlighted_child is not None:
            try:
                current = list(lv.children).index(lv.highlighted_child) + 1
            except ValueError:
                current = 0
        lv.border_subtitle = f"{current} of {total}"

    def _navigate_to(self, directory: Path) -> None:
        try:
            resolved = directory.resolve()
            if not resolved.is_dir():
                self._set_error("Not a directory")
                return
        except PermissionError:
            self._set_error("Permission denied")
            return

        self._location = resolved
        self._load_directory()

    def _select_file(self, path: Path) -> None:
        resolved = path.resolve()
        if self._must_exist and not resolved.exists():
            self._set_error("File does not exist")
            return
        self.dismiss(resolved)

    @on(Input.Submitted, "#file-picker-path-input")
    def _on_path_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        raw = event.value.strip()
        if not raw:
            self._hide_path_bar()
            return

        # Try to expand ~ or ~user
        try:
            path = Path(raw).expanduser().resolve()
        except Exception:
            self._set_error("Invalid path")
            return

        if path.is_dir():
            self._navigate_to(path)
            self._hide_path_bar()
        elif path.is_file():
            self._select_file(path)
        else:
            self._set_error("Path does not exist")

    @on(events.Key)
    def _on_key(self, event: events.Key) -> None:
        focused = self.focused

        # Escape inside path bar hides it and doesn't dismiss picker
        if (
            event.key == "escape"
            and self._path_bar_visible
            and isinstance(focused, Input)
        ):
            event.stop()
            event.prevent_default()
            self._hide_path_bar()
            return

        # / in the list view reveals the path bar
        if event.key == "slash" and isinstance(focused, ListView):
            event.stop()
            event.prevent_default()
            self._show_path_bar(prefill=str(self._location))
            return

        if event.key in ("tab", "shift+tab"):
            event.stop()
            event.prevent_default()
            if isinstance(focused, Input) and self._path_bar_visible:
                self._hide_path_bar()
            elif not self._path_bar_visible:
                self._show_path_bar(prefill=str(self._location))
            else:
                self.query_one("#file-picker-dir-list", ListView).focus()
            return

        if event.key == "backspace" and isinstance(focused, ListView):
            event.stop()
            event.prevent_default()
            parent = self._location.parent.resolve()
            if parent != self._location:
                self._navigate_to(parent)

    def on_paste(self, event: events.Paste) -> None:
        if not self._path_bar_visible and isinstance(self.focused, ListView):
            event.stop()
            event.prevent_default()
            self._show_path_bar(prefill=event.text.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_toggle_hidden(self) -> None:
        self._show_hidden = not self._show_hidden
        self._load_directory()

    def _set_error(self, message: str) -> None:
        panel = self.query_one("#file-picker-panel", Vertical)
        panel.border_subtitle = message

    def _settle_highlight(self) -> None:
        lv = self.query_one("#file-picker-dir-list", ListView)
        if len(lv.children) > 0:
            lv.index = 0
            lv.scroll_home(animate=False)
