from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import (
    Label,
    ListItem,
    ListView,
    Static,
    TabbedContent,
    TabPane,
)

from opengrader.tui.widgets import tabs  # type: ignore -- applies monkeypatch to textual's Tab widget
from opengrader.tui.widgets.nav_item import NavItem


class _NavListItem(ListItem):
    def __init__(self, nav: NavItem, *, is_sub_item: bool = False) -> None:
        self.nav = nav
        self.is_sub_item = is_sub_item
        prefix = "  └ " if is_sub_item else ""
        label_text = prefix + self._format(nav)
        super().__init__(Label(label_text))

    @staticmethod
    def _format(item: NavItem) -> str:
        if item.meta:
            return f"{item.label} [{item.meta}]"
        return item.label


class HomeScreen(Vertical):
    class CourseHighlighted(Message):
        def __init__(self, item: NavItem) -> None:
            self.item = item
            super().__init__()

    class AssignmentHighlighted(Message):
        def __init__(self, item: NavItem) -> None:
            self.item = item
            super().__init__()

    class StudentHighlighted(Message):
        def __init__(self, item: NavItem) -> None:
            self.item = item
            super().__init__()

    class AssignmentSelected(Message):
        def __init__(self, item: NavItem) -> None:
            self.item = item
            super().__init__()

    class StudentSelected(Message):
        def __init__(self, item: NavItem) -> None:
            self.item = item
            super().__init__()

    class StudentSubmissionSelected(Message):
        def __init__(self, student: NavItem, assignment: NavItem) -> None:
            self.student = student
            self.assignment = assignment
            super().__init__()

    def __init__(self, *, id: str | None = None) -> None:
        self._course_items: list[NavItem] = []
        self._assignment_items: list[NavItem] = []
        self._student_items: list[NavItem] = []
        self._expanded_student: NavItem | None = None
        self._expanded_student_assignments: list[NavItem] = []
        super().__init__(id=id)

    def compose(self) -> ComposeResult:
        with Horizontal(id="home-workspace"):
            yield ListView(id="home-course-list")
            with TabbedContent(id="home-content-tabs"):
                with TabPane("Assignments", id="tab-assignments"):
                    yield ListView(id="home-assignment-list")
                with TabPane("Students", id="tab-students"):
                    yield ListView(id="home-student-list")
        yield Static(
            "Select a course to get started.",
            id="home-status",
        )

    def on_mount(self) -> None:
        self.query_one("#home-content-tabs", TabbedContent).display = False

    def show_content_tabs(self) -> None:
        self.query_one("#home-content-tabs", TabbedContent).display = True

    def set_courses(self, courses: list[NavItem]) -> None:
        self._course_items = list(courses)
        course_list = self.query_one("#home-course-list", ListView)
        course_list.clear()
        for item in courses:
            course_list.append(_NavListItem(item))

    def set_assignments(self, assignments: list[NavItem]) -> None:
        self._assignment_items = list(assignments)
        assignment_list = self.query_one("#home-assignment-list", ListView)
        assignment_list.clear()
        for item in assignments:
            assignment_list.append(_NavListItem(item))

    def set_students(self, students: list[NavItem]) -> None:
        self._student_items = list(students)
        self._expanded_student = None
        self._expanded_student_assignments = []
        student_list = self.query_one("#home-student-list", ListView)
        student_list.clear()
        for item in students:
            student_list.append(_NavListItem(item))

    def expand_student_assignments(
        self,
        student: NavItem,
        assignments: list[NavItem],
    ) -> None:
        self._collapse_expanded_student()
        self._expanded_student = student
        self._expanded_student_assignments = list(assignments)

        student_list = self.query_one("#home-student-list", ListView)
        insert_after = self._student_list_index(student)
        if insert_after is None:
            return

        for offset, assignment in enumerate(assignments):
            sub_item = _NavListItem(assignment, is_sub_item=True)
            sub_item.add_class("student-sub-item")
            student_list.insert(insert_after + 1 + offset, sub_item)

    def collapse_student_assignments(self) -> None:
        self._collapse_expanded_student()

    def selected_course(self) -> NavItem | None:
        return self._item_from_list("#home-course-list")

    def selected_assignment(self) -> NavItem | None:
        return self._item_from_list("#home-assignment-list")

    def selected_student(self) -> NavItem | None:
        item = self._item_from_list("#home-student-list")
        if item is not None and self._is_sub_item("#home-student-list"):
            return None
        return item

    def active_tab(self) -> str:
        tabs = self.query_one("#home-content-tabs", TabbedContent)
        active = tabs.active
        if active == "tab-students":
            return "students"
        return "assignments"

    def switch_tab(self, tab_id: str) -> None:
        tabs = self.query_one("#home-content-tabs", TabbedContent)
        if tab_id in ("assignments", "tab-assignments"):
            tabs.active = "tab-assignments"
        elif tab_id in ("students", "tab-students"):
            tabs.active = "tab-students"

    def focus_courses(self) -> None:
        lv = self.query_one("#home-course-list", ListView)
        lv.focus()
        if lv.highlighted_child is None and len(lv.children) > 0:
            lv.index = 0

    def focus_content(self) -> None:
        tab = self.active_tab()
        if tab == "assignments":
            lv = self.query_one("#home-assignment-list", ListView)
            lv.focus()
            if lv.highlighted_child is None and len(lv.children) > 0:
                lv.index = 0
        else:
            lv = self.query_one("#home-student-list", ListView)
            lv.focus()
            if lv.highlighted_child is None and len(lv.children) > 0:
                lv.index = 0

    def set_status(self, text: str) -> None:
        self.query_one("#home-status", Static).update(text)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        if not isinstance(event.item, _NavListItem):
            return
        list_id = event.list_view.id or ""
        nav = event.item.nav
        if list_id == "home-course-list":
            self.post_message(self.CourseHighlighted(nav))
        elif list_id == "home-assignment-list":
            self.post_message(self.AssignmentHighlighted(nav))
        elif list_id == "home-student-list":
            if not event.item.is_sub_item:
                self.post_message(self.StudentHighlighted(nav))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is None:
            return
        if not isinstance(event.item, _NavListItem):
            return
        list_id = event.list_view.id or ""
        nav = event.item.nav
        if list_id == "home-course-list":
            self.post_message(self.CourseHighlighted(nav))
        elif list_id == "home-assignment-list":
            self.post_message(self.AssignmentSelected(nav))
        elif list_id == "home-student-list":
            if event.item.is_sub_item and self._expanded_student is not None:
                self.post_message(
                    self.StudentSubmissionSelected(self._expanded_student, nav)
                )
            else:
                self.post_message(self.StudentSelected(nav))

    def _item_from_list(self, list_selector: str) -> NavItem | None:
        lv = self.query_one(list_selector, ListView)
        if lv.highlighted_child is None:
            return None
        child = lv.highlighted_child
        if isinstance(child, _NavListItem):
            return child.nav
        return None

    def _is_sub_item(self, list_selector: str) -> bool:
        lv = self.query_one(list_selector, ListView)
        if lv.highlighted_child is None:
            return False
        child = lv.highlighted_child
        return isinstance(child, _NavListItem) and child.is_sub_item

    def _student_list_index(self, student: NavItem) -> int | None:
        lv = self.query_one("#home-student-list", ListView)
        for idx, child in enumerate(lv.children):
            if (
                isinstance(child, _NavListItem)
                and child.nav == student
                and not child.is_sub_item
            ):
                return idx
        return None

    def _collapse_expanded_student(self) -> None:
        if self._expanded_student is None:
            return
        student_list = self.query_one("#home-student-list", ListView)
        to_remove = [
            child
            for child in student_list.children
            if isinstance(child, _NavListItem) and child.is_sub_item
        ]
        for child in to_remove:
            child.remove()
        self._expanded_student = None
        self._expanded_student_assignments = []
