from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Static

from opengrader.tui.ui.widgets.shortcut_input import ShortcutInput


@dataclass(frozen=True)
class StatusBarBinding:
    key: str
    description: str


class StatusBar(Horizontal):
    class SearchChanged(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    class SearchSubmitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    class SearchCancelled(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def __init__(self, *, id: str | None = None) -> None:
        self._search_active = False
        super().__init__(id=id)

    def compose(self) -> ComposeResult:
        yield Static("", id="status-bar-bindings")
        yield Static("", id="status-bar-search-prompt", classes="status-bar-search")
        yield ShortcutInput(id="status-bar-search-input", classes="status-bar-search")

    def on_mount(self) -> None:
        self._set_search_display(False)

    def show_bindings(self, bindings: list[StatusBarBinding]) -> None:
        if self._search_active:
            return

        content = Text()
        for binding in bindings:
            if content:
                content.append("   ")
            content.append(binding.key, style="bold #f0a000")
            content.append(f" {binding.description}")
        if content:
            content.append("   ")
        content.append("^\\", style="bold #f0a000")
        content.append(" palette")
        self.query_one("#status-bar-bindings", Static).update(content)

    def enter_search(self, *, prompt: str, value: str, status: str) -> None:
        self._search_active = True
        self.query_one("#status-bar-bindings", Static).display = False
        self.query_one("#status-bar-search-prompt", Static).update(prompt)
        search_input = self.query_one("#status-bar-search-input", ShortcutInput)
        search_input.value = value
        search_input.cursor_position = len(value)
        self._set_search_display(True)
        search_input.focus()

    def update_search_status(self, status: str) -> None:
        _ = status

    def exit_search(self) -> None:
        self._search_active = False
        self._set_search_display(False)
        self.query_one("#status-bar-bindings", Static).display = True

    def search_active(self) -> bool:
        return self._search_active

    @on(ShortcutInput.Changed, "#status-bar-search-input")
    def _on_search_changed(self, event: ShortcutInput.Changed) -> None:
        if self._search_active:
            self.post_message(self.SearchChanged(event.value))

    @on(ShortcutInput.Submitted, "#status-bar-search-input")
    def _on_search_submitted(self, event: ShortcutInput.Submitted) -> None:
        if self._search_active:
            self.post_message(self.SearchSubmitted(event.value))
            event.stop()

    def on_key(self, event: events.Key) -> None:
        if not self._search_active:
            return
        if event.key == "escape":
            search_input = self.query_one("#status-bar-search-input", ShortcutInput)
            self.post_message(self.SearchCancelled(search_input.value))
            event.prevent_default()
            event.stop()

    def _set_search_display(self, display: bool) -> None:
        self.query_one("#status-bar-search-prompt", Static).display = display
        self.query_one("#status-bar-search-input", ShortcutInput).display = display
