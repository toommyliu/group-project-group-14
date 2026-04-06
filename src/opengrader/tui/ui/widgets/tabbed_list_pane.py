from __future__ import annotations

from textual.widget import Widget
from textual.widgets import ListView

from .tabbed_pane import TabbedPane, TabbedPaneTab


class IndexedListView(ListView):
    DEFAULT_CSS = """
    IndexedListView {
        width: 1fr;
        height: 1fr;
        background: $background;
        border: round $accent;
        border-title-color: $foreground;
        border-title-align: left;
        border-subtitle-color: $text-muted;
        border-subtitle-align: right;
        scrollbar-size: 0 0;
    }
    IndexedListView:focus {
        background: transparent !important;
        border-title-color: $accent;
    }
    """

    def current_index_subtitle(self) -> str:
        total = len(self.children)
        if total == 0:
            return "0 of 0"

        current = 0
        if self.highlighted_child is not None:
            try:
                current = list(self.children).index(self.highlighted_child) + 1
            except ValueError:
                current = 0
        else:
            index = self.index if isinstance(self.index, int) else -1
            if 0 <= index < total:
                current = index + 1

        return f"{current} of {total}"

    def refresh_index_subtitle(self) -> None:
        self.border_subtitle = self.current_index_subtitle()


class TabbedListPane(TabbedPane):
    DEFAULT_CSS = """
    TabbedListPane > .pane-tab-content > IndexedListView {
        border: none;
        background: transparent;
    }
    """

    def __init__(
        self,
        title: str,
        *,
        tabs: list[tuple[str, IndexedListView]] | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(title, id=id)
        self._list_tabs: list[IndexedListView] = []

        tab_specs = tabs or [("Items", IndexedListView())]
        self.set_list_tabs(tab_specs)

    def set_list_tabs(self, tabs: list[tuple[str, IndexedListView]]) -> None:
        self._list_tabs = [list_view for _, list_view in tabs]
        pane_tabs = [
            TabbedPaneTab(label=label, build=self._make_builder(list_view))
            for label, list_view in tabs
        ]
        super().set_tabs(pane_tabs)
        self.refresh_index_subtitle()

    def next_tab(self) -> None:
        before = self.active_tab_index
        super().next_tab()
        if before != self.active_tab_index:
            self.refresh_index_subtitle()

    def prev_tab(self) -> None:
        before = self.active_tab_index
        super().prev_tab()
        if before != self.active_tab_index:
            self.refresh_index_subtitle()

    def action_pane_activate_tab(self, tab_index: int) -> None:
        before = self.active_tab_index
        super().action_pane_activate_tab(tab_index)
        if before != self.active_tab_index:
            self.refresh_index_subtitle()

    def primary_list(self) -> IndexedListView:
        return self._list_tabs[0]

    def active_list(self) -> IndexedListView | None:
        widget = self._active_widget()
        if isinstance(widget, IndexedListView):
            return widget
        return None

    def refresh_index_subtitle(self) -> None:
        active = self.active_list()
        subtitle = active.current_index_subtitle() if active is not None else "0 of 0"
        self.set_subtitle(subtitle)
        if active is not None:
            active.border_subtitle = subtitle

    @staticmethod
    def _make_builder(list_view: IndexedListView):
        def _build() -> Widget:
            return list_view

        return _build
