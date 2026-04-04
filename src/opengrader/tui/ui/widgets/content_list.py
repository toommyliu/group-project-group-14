from textual.widgets import ListView


class ContentList(ListView):
    DEFAULT_CSS = """
    ContentList {
        width: 1fr;
        height: 1fr;
        background: $background;
        border: round $accent;
        border-title-color: $foreground;
        border-title-align: left;
        border-subtitle-color: $text-muted;
        border-subtitle-align: right;
    }
    ContentList:focus {
        background: transparent !important;
        border-title-color: $accent;
    }
    """

    def __init__(
        self,
        title: str,
        *,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.border_title = title
        self.border_subtitle = "0 of 0"

    def refresh_index_subtitle(self) -> None:
        total = len(self.children)
        if total == 0:
            self.border_subtitle = "0 of 0"
            return

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

        self.border_subtitle = f"{current} of {total}"
