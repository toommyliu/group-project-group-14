"""A list of recently opened folders or workspaces (`core/recents.py`).

One widget for both places recents appear, so they behave the same:
`opengrader tui`'s recent-workspace chooser and `opengrader classic`'s Start
grading card. Each row is a title over a muted detail line. An entry whose
folder is gone (a drive that remounted under another name, a deleted course)
stays visible, struck through and marked *Missing*: choosing it only explains
itself, and `d` forgets it for good. Nothing here ever opens anything — a
choice is posted as `RecentList.Chosen` for the screen to act on.
"""

from collections.abc import Iterable

from textual.binding import Binding
from textual.message import Message
from textual.widgets import Label, ListItem, ListView

from opengrader.core.recents import RecentItem, forget_recent, short_path


class RecentList(ListView):
    BINDINGS = [
        Binding("d", "forget", "Forget"),
        Binding("delete", "forget", "Forget", show=False),
    ]
    DEFAULT_CSS = """
    RecentList > ListItem {
        padding: 0 1;
        margin-bottom: 1;
    }

    RecentList .recent-detail {
        color: $text-muted;
    }

    RecentList .recent-missing .recent-name {
        color: $text-muted;
        text-style: strike;
    }
    """

    class Chosen(Message):
        """An entry that still exists was chosen (enter or click)."""

        def __init__(self, recent_list: "RecentList", item: RecentItem) -> None:
            super().__init__()
            self.recent_list = recent_list
            self.item = item

        @property
        def control(self) -> "RecentList":
            return self.recent_list

    class Forgotten(Message):
        """An entry was forgotten with `d`; `remaining` is what is left."""

        def __init__(self, recent_list: "RecentList", remaining: int) -> None:
            super().__init__()
            self.recent_list = recent_list
            self.remaining = remaining

        @property
        def control(self) -> "RecentList":
            return self.recent_list

    def __init__(self, items: Iterable[RecentItem] = (), *, id: str | None = None) -> None:
        self.items = list(items)
        super().__init__(
            *(self._row(item) for item in self.items),
            initial_index=self._start_index(),
            id=id,
        )

    async def set_items(self, items: Iterable[RecentItem]) -> None:
        """Replace the rows, e.g. after something new was opened."""
        self.items = list(items)
        await self.clear()
        await self.extend([self._row(item) for item in self.items])
        self.index = self._start_index()

    def _start_index(self) -> int | None:
        # The newest entry that still exists; a missing one is never the default.
        return next(
            (i for i, item in enumerate(self.items) if item.exists()),
            0 if self.items else None,
        )

    @staticmethod
    def _row(item: RecentItem) -> ListItem:
        exists = item.exists()
        # Status first: a long path must not push it off the edge.
        where = short_path(item.path)
        if not exists:
            detail = f"Missing — press d to forget · {where}"
        elif item.kind == "workspace" and item.assignment_title:
            detail = f"Last opened: {item.assignment_title} · {where}"
        else:
            detail = where
        return ListItem(
            Label(item.title or item.path, classes="recent-name", markup=False),
            Label(detail, classes="recent-detail", markup=False),
            classes="" if exists else "recent-missing",
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        # Translate the generic selection into a recent entry, for the screen.
        event.stop()
        index = self.index
        if index is None or index >= len(self.items):
            return
        item = self.items[index]
        if not item.exists():
            self.notify(
                f"{item.path} is no longer there. Press d to forget it.",
                severity="warning",
            )
            return
        self.post_message(self.Chosen(self, item))

    async def action_forget(self) -> None:
        index = self.index
        if index is None or index >= len(self.items):
            return
        forget_recent(self.items.pop(index))
        await self.remove_items([index])
        if self.items:
            self.index = min(index, len(self.items) - 1)
        self.post_message(self.Forgotten(self, len(self.items)))
