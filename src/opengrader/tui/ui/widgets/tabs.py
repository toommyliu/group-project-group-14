from __future__ import annotations

import textual.widgets
from textual.widgets._tabs import Underline


def patch_tabs() -> None:
    """
    Monkeypatch textual's Tab-related widgets to use proportional highlighting for the active tab.
    """

    def proportional_highlight_active(
        self: textual.widgets.Tabs, animate: bool = False
    ) -> None:
        try:
            active_tab = self.query_one("#tabs-list > Tab.-active")
        except:
            return

        tabs_list = self.query_one("#tabs-list")
        underline = self.query_one(Underline)

        start = 0.0
        for tab in tabs_list.children:
            if tab == active_tab:
                break
            if tab.display:
                start += len(str(tab.label)) + 4

        tab_width = len(str(active_tab.label)) + 4
        end = start + tab_width

        total_width = sum(
            len(str(t.label)) + 4 for t in tabs_list.children if t.display
        )
        if total_width > 0:
            underline.styles.width = total_width

        def sync_scroll() -> None:
            try:
                tabs_scroll = self.query_one("#tabs-scroll")
                underline.scroll_x = tabs_scroll.scroll_x
            except:
                pass

        sync_scroll()

        underline.highlight_start = int(start)
        underline.highlight_end = int(end)

        self.call_after_refresh(sync_scroll)

    textual.widgets.Tabs._highlight_active = proportional_highlight_active


patch_tabs()
