from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Label, Static

from local_grader.tui.widgets.miller_column import MillerColumn, MillerItem


class BrowseScreen(Vertical):
    class OpenSelectedStudent(Message):
        pass

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
            "Select a course → assignment → student to begin grading.",
            id="browse-status",
        )

    def on_mount(self) -> None:
        self.populate_demo_data()

    def populate_demo_data(self) -> None:
        self.query_one("#courses", MillerColumn).set_items(
            [
                MillerItem("c1", "CMPE 195", "Spring 2026"),
                MillerItem("c2", "CMPE 102", "Spring 2026"),
            ]
        )
        self.query_one("#assignments", MillerColumn).set_items([])
        self.query_one("#students", MillerColumn).set_items([])

    def set_students(self, students: list[MillerItem]) -> None:
        self.query_one("#students", MillerColumn).set_items(students)

    def set_assignments(self, assignments: list[MillerItem]) -> None:
        self.query_one("#assignments", MillerColumn).set_items(assignments)

    def set_status(self, text: str) -> None:
        self.query_one("#browse-status", Static).update(text)

    def selected_student(self) -> MillerItem | None:
        return self.query_one("#students", MillerColumn).selected_item
