from dataclasses import dataclass, field

from local_grader.tui.controllers.browse_controller import BrowseController
from local_grader.tui.state import TuiWorkflowState
from local_grader.tui.widgets.miller_column import MillerItem

from .fakes import FakeSampleDataService


@dataclass
class FakeBrowseScreen:
    courses: list[MillerItem] = field(default_factory=list)
    assignments: list[MillerItem] = field(default_factory=list)
    students: list[MillerItem] = field(default_factory=list)
    status: str = ""
    focused_column: str | None = None

    def set_courses(
        self,
        courses: list[MillerItem],
        *,
        highlight_first: bool = False,
    ) -> None:
        self.courses = courses

    def set_assignments(
        self,
        assignments: list[MillerItem],
        *,
        highlight_first: bool = False,
    ) -> None:
        self.assignments = assignments

    def set_students(
        self,
        students: list[MillerItem],
        *,
        highlight_first: bool = False,
    ) -> None:
        self.students = students

    def set_status(self, text: str) -> None:
        self.status = text

    def focus_column(self, column_id: str) -> bool:
        self.focused_column = column_id
        return True

    def focus_first_populated_column(self) -> bool:
        self.focused_column = "courses"
        return True

    def focus_next_populated_column(self, column_id: str) -> bool:
        self.focused_column = {
            "courses": "assignments",
            "assignments": "students",
            "students": "students",
        }[column_id]
        return True

    def focus_previous_populated_column(self, column_id: str) -> bool:
        self.focused_column = {
            "students": "assignments",
            "assignments": "courses",
            "courses": "courses",
        }[column_id]
        return True

    def focused_column_id(self) -> str | None:
        return self.focused_column

    def highlighted_item(self, column_id: str) -> MillerItem | None:
        collections = {
            "courses": self.courses,
            "assignments": self.assignments,
            "students": self.students,
        }
        items = collections[column_id]
        return items[0] if items else None

    def move_cursor_up(self) -> None:
        return None

    def move_cursor_down(self) -> None:
        return None


class FakeApp:
    def __init__(self) -> None:
        self.browse_screen = FakeBrowseScreen()

    def get_browse_screen(self) -> FakeBrowseScreen:
        return self.browse_screen


def test_course_selection_resets_downstream_state_and_focuses_assignments() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    opened_students: list[int] = []
    controller = BrowseController(
        app,
        state,
        FakeSampleDataService(),
        opened_students.append,
    )

    controller.initialize()
    controller.handle_item_selected("courses", MillerItem("c1", "CMPE 195"))

    assert state.selection.course_id == 1
    assert state.selection.assessment_id is None
    assert state.selection.student_id is None
    assert state.selection_labels["course"] == "CMPE 195"
    assert state.selection_labels["assignment"] == ""
    assert app.browse_screen.assignments
    assert app.browse_screen.students == []
    assert app.browse_screen.focused_column == "assignments"
    assert opened_students == []


def test_assignment_selection_populates_students_and_focuses_students() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    controller = BrowseController(
        app,
        state,
        FakeSampleDataService(),
        lambda index: None,
    )

    controller.handle_item_selected("courses", MillerItem("c1", "CMPE 195"))
    controller.handle_item_selected("assignments", MillerItem("a2", "HW 2"))

    assert state.selection.assessment_id == 2
    assert state.selection_labels["assignment"] == "HW 2"
    assert [item.item_id for item in state.student_items] == ["s1", "s2"]
    assert app.browse_screen.focused_column == "students"


def test_student_selection_opens_expected_student_index() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    opened_students: list[int] = []
    controller = BrowseController(
        app,
        state,
        FakeSampleDataService(),
        opened_students.append,
    )

    controller.handle_item_selected("courses", MillerItem("c1", "CMPE 195"))
    controller.handle_item_selected("assignments", MillerItem("a1", "HW 1"))
    controller.handle_item_selected("students", MillerItem("s2", "Andy Cortes"))

    assert state.selection.student_id == 2
    assert state.selection_labels["student"] == "Andy Cortes"
    assert opened_students == [1]
