from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rich.markup import escape
from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.widget import Widget


@dataclass
class TabbedPaneTab:
    label: str
    build: Callable[[], Widget]
    supports_paging: bool = False


class TabbedPane(Vertical):
    DEFAULT_CSS = """
    TabbedPane {
        width: 1fr;
        height: 1fr;
        layout: vertical;
        background: $background;
        border: round $accent;
        border-title-color: $foreground;
        border-title-align: left;
        border-subtitle-color: $text-muted;
        border-subtitle-align: right;
    }

    TabbedPane.-pane-focused {
        border-title-color: $accent;
    }

    TabbedPane > .pane-tab-content {
        width: 1fr;
        height: 1fr;
    }

    TabbedPane > .pane-tab-content > * {
        width: 1fr;
        height: 1fr;
    }
    """

    def __init__(
        self,
        title: str,
        *,
        show_single_tab: bool = False,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._title = title
        self._subtitle = ""
        self._show_single_tab = show_single_tab
        self._tabs: list[TabbedPaneTab] = []
        self._tab_widgets: dict[int, Widget] = {}
        self._active_tab_index: int = 0
        self._size_units: int = 1

    def compose(self) -> ComposeResult:
        with Container(classes="pane-tab-content"):
            widget = self._active_widget()
            if widget is not None:
                yield widget

    def on_mount(self) -> None:
        self._render_active_tab()

    def on_focus(self, _event: events.Focus) -> None:
        self._sync_focus_class()

    def on_blur(self, _event: events.Blur) -> None:
        self._sync_focus_class()

    def on_descendant_focus(self, _event: events.DescendantFocus) -> None:
        self._sync_focus_class()

    def on_descendant_blur(self, _event: events.DescendantBlur) -> None:
        self.call_after_refresh(self._sync_focus_class)

    def set_title(self, title: str) -> None:
        self._title = title
        self._sync_chrome()

    def set_subtitle(self, subtitle: str) -> None:
        self._subtitle = subtitle
        self._sync_chrome()

    def set_tabs(self, tabs: list[TabbedPaneTab]) -> None:
        self._tabs = list(tabs)
        self._tab_widgets.clear()
        self._active_tab_index = 0
        self._render_active_tab()

    def next_tab(self) -> None:
        if len(self._tabs) <= 1:
            return
        self._active_tab_index = (self._active_tab_index + 1) % len(self._tabs)
        self._render_active_tab()

    def prev_tab(self) -> None:
        if len(self._tabs) <= 1:
            return
        self._active_tab_index = (self._active_tab_index - 1) % len(self._tabs)
        self._render_active_tab()

    def next_page(self) -> None:
        if not self._active_tab_supports_paging():
            return
        widget = self._active_widget()
        if widget is None:
            return
        callback = getattr(widget, "next_page", None)
        if callable(callback):
            callback()

    def prev_page(self) -> None:
        if not self._active_tab_supports_paging():
            return
        widget = self._active_widget()
        if widget is None:
            return
        callback = getattr(widget, "prev_page", None)
        if callable(callback):
            callback()

    @property
    def tab_count(self) -> int:
        return len(self._tabs)

    @property
    def active_tab_index(self) -> int:
        return self._active_tab_index

    @property
    def size_units(self) -> int:
        return self._size_units

    def set_size_units(self, units: int) -> None:
        self._size_units = max(1, units)
        self.styles.height = f"{self._size_units}fr"

    def _active_tab(self) -> TabbedPaneTab | None:
        if not self._tabs:
            return None
        return self._tabs[self._active_tab_index]

    def _active_widget(self) -> Widget | None:
        active_tab = self._active_tab()
        if active_tab is None:
            return None

        widget = self._tab_widgets.get(self._active_tab_index)
        if widget is None:
            widget = active_tab.build()
            self._tab_widgets[self._active_tab_index] = widget
        return widget

    def _active_tab_supports_paging(self) -> bool:
        active_tab = self._active_tab()
        if active_tab is None:
            return False
        return active_tab.supports_paging

    def _render_active_tab(self) -> None:
        if not self.is_mounted:
            return

        content = self.query_one(".pane-tab-content", Container)
        widget = self._active_widget()
        children = list(content.children)

        if widget is not None and len(children) == 1 and children[0] is widget:
            self._sync_chrome()
            self._sync_focus_class()
            return

        for child in children:
            child.remove()

        if widget is not None and widget.parent is not content:
            content.mount(widget)

        self._sync_chrome()
        self._sync_focus_class()

    def _sync_chrome(self) -> None:
        self.border_title = self._build_border_title()
        subtitle_parts: list[str] = []
        if self._subtitle:
            subtitle_parts.append(self._subtitle)
        self.border_subtitle = " · ".join(subtitle_parts)

    def _build_border_title(self) -> str:
        if not self._tabs:
            return self._title
        if len(self._tabs) == 1 and not self._show_single_tab:
            return self._title + f" {escape(self._tabs[0].label)}"

        tab_markup: list[str] = []
        for index, tab in enumerate(self._tabs):
            if index > 0:
                tab_markup.append("[$text-muted] - [/]")
            tab_label = escape(tab.label)
            if index == self._active_tab_index:
                tab_markup.append(f"{tab_label}")
            else:
                tab_markup.append(f"[$text-muted]{tab_label}[/]")

        return self._title + " " + ''.join(tab_markup)

    def sync_focus_class(self) -> None:
        self._sync_focus_class()

    def _sync_focus_class(self) -> None:
        self.set_class(self.has_focus or self.has_focus_within, "-pane-focused")
