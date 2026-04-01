from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from local_grader.tui.widgets.miller_column import MillerColumn, MillerItem


class BrowseScreen(Vertical):
    COLUMN_IDS = ("courses", "assignments", "students")

    def compose(self) -> ComposeResult:
        with Horizontal(id="miller-container"):
            yield MillerColumn("Courses", "courses", id="courses", classes="miller-col")
            yield MillerColumn(
                "Assignments", "assignments", id="assignments", classes="miller-col"
            )
            yield MillerColumn(
                "Students", "students", id="students", classes="miller-col"
            )
        yield Static(
            "Use arrows to move and Enter to select a course → assignment → student.",
            id="browse-status",
        )

    def column(self, column_id: str) -> MillerColumn:
        return self.query_one(f"#{column_id}", MillerColumn)

    def set_courses(
        self,
        courses: list[MillerItem],
        *,
        highlight_first: bool = False,
    ) -> None:
        self.column("courses").set_items(courses, highlight_first=highlight_first)

    def set_students(
        self,
        students: list[MillerItem],
        *,
        highlight_first: bool = False,
    ) -> None:
        self.column("students").set_items(students, highlight_first=highlight_first)

    def set_assignments(
        self,
        assignments: list[MillerItem],
        *,
        highlight_first: bool = False,
    ) -> None:
        self.column("assignments").set_items(
            assignments,
            highlight_first=highlight_first,
        )

    def set_status(self, text: str) -> None:
        self.query_one("#browse-status", Static).update(text)

    def selected_student(self) -> MillerItem | None:
        return self.column("students").selected_item

    def highlighted_item(self, column_id: str) -> MillerItem | None:
        return self.column(column_id).highlighted_item

    def focused_column_id(self) -> str | None:
        for column_id in self.COLUMN_IDS:
            if self.column(column_id).has_focus_within():
                return column_id
        return None

    def focus_column(self, column_id: str) -> bool:
        column = self.column(column_id)
        if not column.has_items():
            return False
        if column.highlighted_item is None:
            column.highlight_first()
        column.focus_list()
        return True

    def focus_first_populated_column(self) -> bool:
        for column_id in self.COLUMN_IDS:
            if self.focus_column(column_id):
                return True
        return False

    def focus_next_populated_column(self, column_id: str) -> bool:
        try:
            start_index = self.COLUMN_IDS.index(column_id)
        except ValueError:
            return self.focus_first_populated_column()
        for next_column_id in self.COLUMN_IDS[start_index + 1 :]:
            if self.focus_column(next_column_id):
                return True
        return False

    def focus_previous_populated_column(self, column_id: str) -> bool:
        try:
            start_index = self.COLUMN_IDS.index(column_id)
        except ValueError:
            return self.focus_first_populated_column()
        for previous_column_id in reversed(self.COLUMN_IDS[:start_index]):
            if self.focus_column(previous_column_id):
                return True
        return False

    def move_cursor_up(self) -> None:
        focused_column_id = self.focused_column_id()
        if focused_column_id is None:
            return
        self.column(focused_column_id).move_cursor_up()

    def move_cursor_down(self) -> None:
        focused_column_id = self.focused_column_id()
        if focused_column_id is None:
            return
        self.column(focused_column_id).move_cursor_down()
