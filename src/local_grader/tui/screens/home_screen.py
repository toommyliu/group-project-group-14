from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Static, Tree
from textual.widgets.tree import TreeNode

from local_grader.tui.widgets.nav_item import NavItem


@dataclass(frozen=True)
class HomeNodeData:
    kind: str
    course: NavItem | None = None
    assignment: NavItem | None = None
    student: NavItem | None = None


class HomeScreen(Vertical):
    def __init__(self, *, id: str | None = None) -> None:
        self._course_nodes: dict[str, TreeNode[HomeNodeData]] = {}
        self._course_assignments_nodes: dict[str, TreeNode[HomeNodeData]] = {}
        self._course_students_nodes: dict[str, TreeNode[HomeNodeData]] = {}
        self._assignment_nodes: dict[tuple[str, str], TreeNode[HomeNodeData]] = {}
        self._student_nodes: dict[tuple[str, str], TreeNode[HomeNodeData]] = {}
        self._student_submissions_nodes: dict[
            tuple[str, str], TreeNode[HomeNodeData]
        ] = {}
        super().__init__(id=id)

    def compose(self) -> ComposeResult:
        with Horizontal(id="home-workspace"):
            yield Tree[HomeNodeData]("Courses", id="home-tree")
            with VerticalScroll(id="home-details-pane"):
                yield Static("", id="home-details")
        yield Static(
            "Use arrows to navigate tree nodes and Enter to select.",
            id="home-status",
        )

    @property
    def tree(self) -> Tree[HomeNodeData]:
        return self.query_one("#home-tree", Tree)

    def on_mount(self) -> None:
        tree = self.tree
        tree.show_root = False
        tree.auto_expand = False
        tree.root.expand()

    def set_courses(
        self,
        courses: list[NavItem],
        *,
        highlight_first: bool = False,
    ) -> None:
        tree = self.tree
        tree.clear()
        self._course_nodes.clear()
        self._course_assignments_nodes.clear()
        self._course_students_nodes.clear()
        self._assignment_nodes.clear()
        self._student_nodes.clear()
        self._student_submissions_nodes.clear()

        first_course_node: TreeNode[HomeNodeData] | None = None
        for course in courses:
            course_node = tree.root.add(
                self._item_text(course),
                data=HomeNodeData(kind="course", course=course),
            )
            self._course_nodes[course.item_id] = course_node
            if first_course_node is None:
                first_course_node = course_node

            assignments_node = course_node.add(
                "Assignments",
                data=HomeNodeData(kind="course_assignments_section", course=course),
            )
            students_node = course_node.add(
                "Students",
                data=HomeNodeData(kind="course_students_section", course=course),
            )
            self._course_assignments_nodes[course.item_id] = assignments_node
            self._course_students_nodes[course.item_id] = students_node

        tree.root.expand()
        if highlight_first and first_course_node is not None:
            tree.move_cursor(first_course_node)

    def set_course_assignments(
        self,
        course_item_id: str,
        assignments: list[NavItem],
    ) -> None:
        parent = self._course_assignments_nodes.get(course_item_id)
        if parent is None:
            return
        course_item = parent.data.course if parent.data is not None else None
        if course_item is None:
            return

        self._clear_course_assignments(course_item_id)
        parent.remove_children()

        for assignment in assignments:
            assignment_node = parent.add_leaf(
                self._item_text(assignment),
                data=HomeNodeData(
                    kind="assignment",
                    course=course_item,
                    assignment=assignment,
                ),
            )
            key = (course_item_id, assignment.item_id)
            self._assignment_nodes[key] = assignment_node

        parent.expand()

    def set_course_assignments_loading(self, course_item_id: str) -> None:
        parent = self._course_assignments_nodes.get(course_item_id)
        if parent is None:
            return
        parent.remove_children()
        parent.add_leaf(
            "Loading assignments...",
            data=HomeNodeData(kind="loading"),
        )
        parent.expand()

    def set_course_students(
        self,
        course_item_id: str,
        students: list[NavItem],
    ) -> None:
        parent = self._course_students_nodes.get(course_item_id)
        if parent is None:
            return
        course_item = parent.data.course if parent.data is not None else None
        if course_item is None:
            return

        self._clear_course_students(course_item_id)
        parent.remove_children()

        for student in students:
            student_node = parent.add(
                self._item_text(student),
                data=HomeNodeData(
                    kind="student",
                    course=course_item,
                    student=student,
                ),
            )
            key = (course_item_id, student.item_id)
            self._student_nodes[key] = student_node
            self._student_submissions_nodes[key] = student_node.add(
                "Submissions",
                data=HomeNodeData(
                    kind="student_submissions_section",
                    course=course_item,
                    student=student,
                ),
            )

        parent.expand()

    def set_course_students_loading(self, course_item_id: str) -> None:
        parent = self._course_students_nodes.get(course_item_id)
        if parent is None:
            return
        parent.remove_children()
        parent.add_leaf(
            "Loading students...",
            data=HomeNodeData(kind="loading"),
        )
        parent.expand()

    def set_student_submissions(
        self,
        course_item_id: str,
        student_item_id: str,
        assignments: list[NavItem],
    ) -> None:
        key = (course_item_id, student_item_id)
        parent = self._student_submissions_nodes.get(key)
        if parent is None:
            return
        course_item = parent.data.course if parent.data is not None else None
        student_item = parent.data.student if parent.data is not None else None
        if course_item is None or student_item is None:
            return

        parent.remove_children()
        for assignment in assignments:
            parent.add_leaf(
                self._item_text(assignment),
                data=HomeNodeData(
                    kind="submission",
                    course=course_item,
                    assignment=assignment,
                    student=student_item,
                ),
            )
        parent.expand()

    def set_student_submissions_loading(
        self,
        course_item_id: str,
        student_item_id: str,
    ) -> None:
        key = (course_item_id, student_item_id)
        parent = self._student_submissions_nodes.get(key)
        if parent is None:
            return
        parent.remove_children()
        parent.add_leaf(
            "Loading submissions...",
            data=HomeNodeData(kind="loading"),
        )
        parent.expand()

    def highlighted_node_data(self) -> HomeNodeData | None:
        node = self.tree.cursor_node
        if node is None:
            return None
        data = node.data
        if not isinstance(data, HomeNodeData):
            return None
        return data

    def set_status(self, text: str) -> None:
        self.query_one("#home-status", Static).update(text)

    def set_details(self, text: str) -> None:
        self.query_one("#home-details", Static).update(text)

    def focus_tree(self) -> bool:
        tree = self.tree
        tree.focus()
        if tree.cursor_node is not None:
            return True
        if not tree.root.children:
            return False
        tree.move_cursor(tree.root.children[0])
        return True

    def focused(self) -> bool:
        return self.tree.has_focus

    def move_cursor_up(self) -> None:
        self.tree.action_cursor_up()

    def move_cursor_down(self) -> None:
        self.tree.action_cursor_down()

    def move_cursor_left(self) -> None:
        self.tree.action_cursor_parent()

    def move_cursor_right(self) -> None:
        node = self.tree.cursor_node
        if node is None:
            return
        if node.children:
            node.expand()
            self.tree.action_cursor_down()

    def focus_first_child(self) -> bool:
        node = self.tree.cursor_node
        if node is None or not node.children:
            return False
        node.expand()
        self.tree.action_cursor_down()
        return True

    @staticmethod
    def _item_text(item: NavItem) -> str:
        if item.meta:
            return f"{item.label} [{item.meta}]"
        return item.label

    def _clear_course_assignments(self, course_item_id: str) -> None:
        keys = [key for key in self._assignment_nodes if key[0] == course_item_id]
        for key in keys:
            self._assignment_nodes.pop(key, None)

    def _clear_course_students(self, course_item_id: str) -> None:
        keys = [key for key in self._student_nodes if key[0] == course_item_id]
        for key in keys:
            self._student_nodes.pop(key, None)
            self._student_submissions_nodes.pop(key, None)
