from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Label, ListItem, ListView, Static

from opengrader.tui.ui.widgets import SelectionDetails
from opengrader.tui.ui.widgets.nav_item import NavItem


class _NavListItem(ListItem):
    def __init__(self, nav: NavItem) -> None:
        self.nav = nav
        label_text = self._format(nav)
        super().__init__(Label(label_text))

    @staticmethod
    def _format(item: NavItem) -> str:
        if item.meta:
            return f"{item.label} [{item.meta}]"
        return item.label


class CoursePickerScreen(Vertical):
    class CourseHighlighted(Message):
        def __init__(self, item: NavItem) -> None:
            self.item = item
            super().__init__()

    class CourseSelected(Message):
        def __init__(self, item: NavItem) -> None:
            self.item = item
            super().__init__()

    def compose(self) -> ComposeResult:
        yield Static("Choose a course", id="course-picker-title")
        yield Static(
            "Pick the class you want to grade, then enter the workspace.",
            id="course-picker-subtitle",
        )
        with Horizontal(id="course-picker-workspace"):
            with Vertical(id="course-picker-sidebar"):
                yield ListView(id="course-picker-list")
            yield SelectionDetails(id="course-picker-details")
        yield Static("Select a course to get started.", id="course-picker-status")

    def set_courses(self, courses: list[NavItem]) -> None:
        course_list = self.query_one("#course-picker-list", ListView)
        course_list.clear()
        for item in courses:
            course_list.append(_NavListItem(item))

    def selected_course(self) -> NavItem | None:
        lv = self.query_one("#course-picker-list", ListView)
        child = lv.highlighted_child
        if isinstance(child, _NavListItem):
            return child.nav
        return None

    def focus_courses(self, item_id: str | None = None) -> None:
        lv = self.query_one("#course-picker-list", ListView)
        if item_id is not None:
            for index, child in enumerate(lv.children):
                nav = getattr(child, "nav", None)
                if nav is not None and getattr(nav, "item_id", None) == item_id:
                    lv.index = index
                    break
        elif lv.children:
            lv.index = 0
        lv.focus()

    def show_course_details(
        self,
        item: NavItem,
        *,
        assignment_count: int,
        student_count: int,
    ) -> None:
        details = self.query_one("#course-picker-details", SelectionDetails)
        details.show_course(
            item,
            assignment_count=assignment_count,
            student_count=student_count,
        )

    def set_status(self, text: str) -> None:
        self.query_one("#course-picker-status", Static).update(text)

    def on_mount(self) -> None:
        self.query_one("#course-picker-list", ListView).focus()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        if not isinstance(event.widget, ListView):
            return
        item = self.selected_course()
        if item is not None:
            self.post_message(self.CourseHighlighted(item))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None or not isinstance(event.item, _NavListItem):
            return
        self.post_message(self.CourseHighlighted(event.item.nav))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is None or not isinstance(event.item, _NavListItem):
            return
        self.post_message(self.CourseSelected(event.item.nav))
