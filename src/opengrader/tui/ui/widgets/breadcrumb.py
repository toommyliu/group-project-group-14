from rich.markup import escape

from textual.widgets import Label


class Breadcrumb(Label):
    """Muted location trail; the current (last) segment renders bold."""

    _SEPARATOR = " › "

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("Home", id=id)
        self.set_path([])

    def set_path(self, labels: list[str]) -> None:
        if not labels:
            self.update(f"Home{self._SEPARATOR}[b]Select a submission[/b]")
            return
        parts = [escape(label) for label in labels[:-1]]
        parts.append(f"[b]{escape(labels[-1])}[/b]")
        self.update(self._SEPARATOR.join(parts))
