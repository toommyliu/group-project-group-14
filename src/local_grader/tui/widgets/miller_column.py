from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Label, ListItem, ListView, Static


@dataclass(frozen=True)
class MillerItem:
    item_id: str
    label: str
    meta: str = ""


class MillerColumn(Vertical):
    class ItemSelected(Message):
        def __init__(self, column_id: str, item: MillerItem) -> None:
            self.column_id = column_id
            self.item = item
            super().__init__()

    def __init__(
        self,
        title: str,
        column_id: str,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        self.column_id = column_id
        self._title = title
        self._items: list[MillerItem] = []
        self._selected_item: MillerItem | None = None
        super().__init__(id=id, classes=classes)

    def compose(self) -> ComposeResult:
        yield Label(self._title, classes="miller-title")
        yield ListView(id=f"{self.column_id}-list")

    def set_items(
        self,
        items: list[MillerItem],
        *,
        highlight_first: bool = False,
    ) -> None:
        self._items = items
        self._selected_item = None
        list_view = self.list_view
        list_view.clear()
        for item in items:
            text = item.label
            if item.meta:
                text = f"{item.label} [{item.meta}]"
            list_view.append(ListItem(Static(text)))
        list_view.index = 0 if highlight_first and items else None

    def clear_items(self) -> None:
        self.set_items([])

    @property
    def list_view(self) -> ListView:
        return self.query_one(ListView)

    @property
    def items(self) -> list[MillerItem]:
        return list(self._items)

    @property
    def selected_item(self) -> MillerItem | None:
        return self._selected_item

    @property
    def highlighted_index(self) -> int | None:
        return self.list_view.index

    @property
    def highlighted_item(self) -> MillerItem | None:
        index = self.highlighted_index
        if index is None:
            return None
        if index < 0 or index >= len(self._items):
            return None
        return self._items[index]

    def has_items(self) -> bool:
        return bool(self._items)

    def focus_list(self) -> None:
        self.list_view.focus()

    def has_focus_within(self) -> bool:
        return self.has_focus or self.list_view.has_focus

    def set_highlighted_index(self, index: int | None) -> None:
        if index is None or not self._items:
            self.list_view.index = None
            return
        self.list_view.index = max(0, min(index, len(self._items) - 1))

    def highlight_first(self) -> None:
        if self._items:
            self.set_highlighted_index(0)

    def move_cursor_up(self) -> None:
        self.list_view.action_cursor_up()

    def move_cursor_down(self) -> None:
        self.list_view.action_cursor_down()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != f"{self.column_id}-list":
            return
        if event.index is None:
            return
        if event.index < 0 or event.index >= len(self._items):
            return
        self._selected_item = self._items[event.index]
        self.post_message(
            self.ItemSelected(column_id=self.column_id, item=self._selected_item)
        )
