from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from rich.markup import escape
from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.widget import Widget
from textual.widgets import ListView
from textual.reactive import reactive


@dataclass
class TabbedPaneTab:
    label: str
    build: Callable[[], Widget]
    supports_paging: bool = False


class TabbedPane(Vertical):
    auto_links = reactive(False)

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
            for index in range(len(self._tabs)):
                widget = self._widget_for_tab(index)
                widget.display = index == self._active_tab_index
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
        if self.is_mounted:
            content = self.query_one(".pane-tab-content", Container)
            for child in list(content.children):
                child.remove()

        self._tabs = list(tabs)
        self._tab_widgets.clear()
        self._active_tab_index = 0
        self._render_active_tab()

    def next_tab(self) -> None:
        if len(self._tabs) <= 1:
            return
        self._set_active_tab(
            (self._active_tab_index + 1) % len(self._tabs),
            focus=False,
        )

    def prev_tab(self) -> None:
        if len(self._tabs) <= 1:
            return
        self._set_active_tab(
            (self._active_tab_index - 1) % len(self._tabs),
            focus=False,
        )

    def action_pane_activate_tab(self, tab_index: int) -> None:
        self._set_active_tab(tab_index, focus=True)

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
        if not self._tabs:
            return None
        return self._widget_for_tab(self._active_tab_index)

    def _widget_for_tab(self, tab_index: int) -> Widget:
        widget = self._tab_widgets.get(tab_index)
        if widget is None:
            widget = self._tabs[tab_index].build()
            self._tab_widgets[tab_index] = widget
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
        tab_widgets = [
            self._widget_for_tab(index)
            for index in range(len(self._tabs))
        ]

        for widget in tab_widgets:
            if widget not in content.children:
                content.mount(widget)

        active_widget = self._active_widget()
        for child in content.children:
            child.display = child is active_widget

        self._sync_chrome()
        self._sync_focus_class()
        # TODO: might not be needed anymore?
        # self.refresh(layout=True)

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
            label = escape(self._tabs[0].label)
            if not label:
                return self._title
            return (
                self._title
                + " "
                + self._tab_label_markup(
                    self._active_tab_index,
                    label,
                    active=True,
                )
            )

        title_as_first_tab = len(self._tabs) > 1 and not self._tabs[0].label
        visible_tabs = [
            (index, tab) for index, tab in enumerate(self._tabs) if tab.label
        ]

        if title_as_first_tab:
            title_markup = self._title_tab_markup(active=self._active_tab_index == 0)
            if not visible_tabs:
                return title_markup
            tab_markup = self._join_tab_markup(visible_tabs)
            return title_markup + "[$text-muted] - [/]" + tab_markup

        if not visible_tabs:
            return self._title

        return self._title + " " + self._join_tab_markup(visible_tabs)

    def _join_tab_markup(self, visible_tabs: list[tuple[int, TabbedPaneTab]]) -> str:
        tab_markup: list[str] = []
        for display_index, (index, tab) in enumerate(visible_tabs):
            if display_index > 0:
                tab_markup.append("[$text-muted] - [/]")
            tab_label = escape(tab.label)
            tab_markup.append(
                self._tab_label_markup(
                    index,
                    tab_label,
                    active=index == self._active_tab_index,
                )
            )
        return "".join(tab_markup)

    def _title_tab_markup(self, *, active: bool) -> str:
        clickable = f"[@click=pane_activate_tab(0)]{self._title}[/]"
        if active:
            return f"[$accent bold]{clickable}[/]"
        return f"[$text-muted]{clickable}[/]"

    def _set_active_tab(self, tab_index: int, *, focus: bool) -> bool:
        if tab_index < 0 or tab_index >= len(self._tabs):
            return False

        changed = tab_index != self._active_tab_index
        if changed:
            self._active_tab_index = tab_index
            self._render_active_tab()

        if focus:
            self._focus_active_widget()

        return changed

    def _focus_active_widget(self) -> None:
        active_widget = self._active_widget()

        if isinstance(active_widget, ListView):
            ensure_focus_index = getattr(active_widget, "ensure_focus_index", None)
            if callable(ensure_focus_index):
                ensure_focus_index(fallback_first=True)
            elif (
                active_widget.highlighted_child is None
                and len(active_widget.children) > 0
            ):
                active_widget.index = 0

            normalize_highlight_classes = getattr(
                active_widget,
                "normalize_highlight_classes",
                None,
            )
            if callable(normalize_highlight_classes):
                normalize_highlight_classes()

        if (
            active_widget is not None
            and active_widget.can_focus
            and not active_widget.disabled
        ):
            active_widget.focus()
            return

        if self.can_focus and not self.disabled:
            self.focus()

    @staticmethod
    def _tab_label_markup(tab_index: int, tab_label: str, *, active: bool) -> str:
        clickable = f"[@click=pane_activate_tab({tab_index})]{tab_label}[/]"
        if active:
            return f"[$accent bold]{clickable}[/]"
        return f"[$text-muted]{clickable}[/]"

    def sync_focus_class(self) -> None:
        self._sync_focus_class()

    def _sync_focus_class(self) -> None:
        self.set_class(self.has_focus or self.has_focus_within, "-pane-focused")
