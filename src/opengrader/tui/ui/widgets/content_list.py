from textual.widgets import ListView


class ContentList(ListView):
    DEFAULT_CSS = """
    ContentList {
        width: 1fr;
        height: 1fr;
        border: round $accent;
        border-title-color: #ffffff;
        border-title-align: left;
        border-subtitle-color: $text-muted;
        border-subtitle-align: right;
    }
    ContentList:focus {
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
