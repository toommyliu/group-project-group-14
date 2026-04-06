from .tabbed_list_pane import IndexedListView, TabbedListPane


class AssignmentPane(TabbedListPane):
    def __init__(
        self,
        *,
        id: str | None = None,
        pane_id: str | None = None,
    ) -> None:
        super().__init__(
            title="[2]",
            tabs=[("Assignments", IndexedListView(id=id))],
            id=pane_id,
        )
