from .tabbed_list_pane import IndexedListView, TabbedListPane


class StudentPane(TabbedListPane):
    def __init__(
        self,
        *,
        id: str | None = None,
        pane_id: str | None = None,
    ) -> None:
        super().__init__(
            title="[3]",
            tabs=[("Students", IndexedListView(id=id))],
            id=pane_id,
        )
