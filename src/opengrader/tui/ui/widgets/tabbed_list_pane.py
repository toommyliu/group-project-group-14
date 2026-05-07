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
        scrollbar-size: 1 1;
    }

    IndexedListView > ListItem.-highlight {
        color: $foreground;
        background: transparent;
        text-style: none;
    }

    IndexedListView > ListItem.-hovered {
        color: $foreground;
        background: transparent !important;
        text-style: none;
    }

    IndexedListView:focus > ListItem.-hovered {
        color: $foreground;
        background: transparent !important;
        text-style: none;
    }

    IndexedListView:focus {
        background: transparent !important;
        border-title-color: $accent;
    }

    IndexedListView:focus > ListItem.-highlight {
        color: $block-cursor-foreground;
        background: $block-cursor-background;
        text-style: $block-cursor-text-style;
    }
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._remembered_index: int | None = None

    def remember_highlighted_index(self) -> None:
        if self.highlighted_child is not None:
            try:
                self._remembered_index = list(self.children).index(
                    self.highlighted_child
                )
                return
            except ValueError:
                pass

        if isinstance(self.index, int) and self.index >= 0:
            self._remembered_index = self.index

    def ensure_focus_index(self, *, fallback_first: bool = True) -> None:
        if len(self.children) == 0:
            return

        if self.highlighted_child is not None:
            self.remember_highlighted_index()
            self.normalize_highlight_classes()
            return

        if self._remembered_index is not None:
            self.index = min(self._remembered_index, len(self.children) - 1)
            self.normalize_highlight_classes()
            return

        if fallback_first:
            self.index = 0
            self.normalize_highlight_classes()

    def normalize_highlight_classes(self) -> None:
        # TODO: don't know how to repro this
        # but the current child maintains highlight so
        # we just force it to be correct based on the index or highlighted_child
        if len(self.children) == 0:
            return

        target = self.highlighted_child
        if target is None and isinstance(self.index, int):
            if 0 <= self.index < len(self.children):
                target = list(self.children)[self.index]

        for child in self.children:
            child.set_class(child is target, "-highlight")

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
        index = self._active_row_index()
        before = self.active_tab_index
        super().next_tab()
        if before != self.active_tab_index:
            self._restore_active_row_index(index)
            self.refresh_index_subtitle()

    def prev_tab(self) -> None:
        index = self._active_row_index()
        before = self.active_tab_index
        super().prev_tab()
        if before != self.active_tab_index:
            self._restore_active_row_index(index)
            self.refresh_index_subtitle()

    def action_pane_activate_tab(self, tab_index: int) -> None:
        index = self._active_row_index()
        before = self.active_tab_index
        super().action_pane_activate_tab(tab_index)
        if before != self.active_tab_index:
            self._restore_active_row_index(index)
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

    def _active_row_index(self) -> int | None:
        active = self.active_list()
        if active is None:
            return None

        if active.highlighted_child is not None:
            try:
                return list(active.children).index(active.highlighted_child)
            except ValueError:
                return None

        if isinstance(active.index, int) and active.index >= 0:
            return active.index
        return None

    def _restore_active_row_index(self, index: int | None) -> None:
        active = self.active_list()
        if active is None or index is None or len(active.children) == 0:
            return

        active.index = min(index, len(active.children) - 1)
        active.normalize_highlight_classes()

    @staticmethod
    def _make_builder(list_view: IndexedListView):
        def _build() -> Widget:
            return list_view

        return _build
