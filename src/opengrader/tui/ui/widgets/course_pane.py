from .tabbed_list_pane import IndexedListView, TabbedListPane


class CoursePane(TabbedListPane):
    def __init__(self, *, id: str | None = None, pane_id: str | None = None) -> None:
        super().__init__(
            title="[1]",
            tabs=[("Courses", IndexedListView(id=id))],
            id=pane_id,
        )
