from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Label, ListItem, ListView, Static

from opengrader.tui.ui.widgets import HomeSidebar, SelectionDetails
from opengrader.tui.ui.widgets.nav_item import NavItem


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
        self._active_content = "assignments"
        super().__init__(id=id)

    def compose(self) -> ComposeResult:
        with Horizontal(id="home-workspace"):
            yield HomeSidebar(id="home-sidebar")
            yield SelectionDetails(id="home-selection-details")
        yield Static(
            "Select a course to get started.",
            id="home-status",
        )

    def content_visible(self) -> bool:
        return self._sidebar().content_visible()

    def show_content_tabs(self) -> None:
        self._sidebar().show_content_sections()

    def set_courses(self, courses: list[NavItem]) -> None:
        self._course_items = list(courses)
        course_list = self._sidebar().course_list()
        course_list.clear()
        for item in courses:
            course_list.append(_NavListItem(item))
        self._sidebar().refresh_index_subtitles()

    def set_assignments(self, assignments: list[NavItem]) -> None:
        self._assignment_items = list(assignments)
        assignment_list = self._sidebar().assignment_list()
        assignment_list.clear()
        for item in assignments:
            assignment_list.append(_NavListItem(item))
        self._sidebar().refresh_index_subtitles()

    def set_students(self, students: list[NavItem]) -> None:
        self._student_items = list(students)
        self._expanded_student = None
        self._expanded_student_assignments = []
        student_list = self._sidebar().student_list()
        student_list.clear()
        for item in students:
            student_list.append(_NavListItem(item))
        self._sidebar().refresh_index_subtitles()

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

        self._sidebar().refresh_index_subtitles()

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
        if self._sidebar().student_list().has_focus:
            return "students"
        if self._sidebar().assignment_list().has_focus:
            return "assignments"
        return self._active_content

    def switch_tab(self, tab_id: str) -> None:
        if tab_id in ("assignments", "tab-assignments"):
            self._active_content = "assignments"
        elif tab_id in ("students", "tab-students"):
            self._active_content = "students"

    def focus_courses(self) -> None:
        self._sidebar().focus_courses()

    def focus_assignments(self) -> None:
        self._active_content = "assignments"
        self._sidebar().focus_assignments()

    def focus_students(self) -> None:
        self._active_content = "students"
        self._sidebar().focus_students()

    def focus_details(self) -> None:
        self.query_one("#home-selection-details", SelectionDetails).focus()

    def focused_pane(self) -> str | None:
        sidebar_focused = self._sidebar().focused_pane()
        if sidebar_focused is not None:
            return sidebar_focused
        if self.query_one("#home-selection-details", SelectionDetails).has_focus:
            return "details"
        return None

    def focus_content(self) -> None:
        if self._active_content == "students":
            self.focus_students()
            return
        self.focus_assignments()

    def show_course_details(
        self,
        item: NavItem,
        *,
        assignment_count: int,
        student_count: int,
    ) -> None:
        details = self.query_one("#home-selection-details", SelectionDetails)
        details.show_course(
            item,
            assignment_count=assignment_count,
            student_count=student_count,
        )

    def show_assignment_details(self, item: NavItem, *, template_hint: str) -> None:
        details = self.query_one("#home-selection-details", SelectionDetails)
        details.show_assignment(item, template_hint=template_hint)

    def show_student_details(self, item: NavItem, *, submission_count: int) -> None:
        details = self.query_one("#home-selection-details", SelectionDetails)
        details.show_student(item, submission_count=submission_count)

    def show_student_submission_details(
        self,
        student: NavItem,
        assignment: NavItem,
    ) -> None:
        details = self.query_one("#home-selection-details", SelectionDetails)
        details.show_student_submission(student, assignment)

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
            if self.content_visible():
                self.post_message(self.CourseHighlighted(nav))
        elif list_id == "home-assignment-list":
            self._active_content = "assignments"
            self.post_message(self.AssignmentHighlighted(nav))
        elif list_id == "home-student-list":
            self._active_content = "students"
            if not event.item.is_sub_item:
                self.post_message(self.StudentHighlighted(nav))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is None:
            return
        if not isinstance(event.item, _NavListItem):
            return
        list_id = event.list_view.id or ""
        nav = event.item.nav
        # if list_id == "home-course-list":
            # return
        # elif list_id == "home-assignment-list":
        if list_id == "home-assignment-list":
            self._active_content = "assignments"
            self.post_message(self.AssignmentSelected(nav))
        elif list_id == "home-student-list":
            self._active_content = "students"
            if event.item.is_sub_item and self._expanded_student is not None:
                self.post_message(
                    self.StudentSubmissionSelected(self._expanded_student, nav)
                )
            else:
                self.post_message(self.StudentSelected(nav))

    def _sidebar(self) -> HomeSidebar:
        return self.query_one("#home-sidebar", HomeSidebar)

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
        self._sidebar().refresh_index_subtitles()
