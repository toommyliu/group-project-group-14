from dataclasses import dataclass

from opengrader.tui.controllers.home_controller import HomeController
from opengrader.tui.screens.home_screen import HomeNodeData
from opengrader.tui.state import TuiWorkflowState
from opengrader.tui.widgets.nav_item import NavItem

from .fakes import FakeDataProvider


@dataclass
class FakeHomeScreen:
    status: str = ""
    details: str = ""
    has_focus: bool = False
    courses: list[NavItem] | None = None
    course_assignments: dict[str, list[NavItem]] | None = None
    course_students: dict[str, list[NavItem]] | None = None
    student_submissions: dict[tuple[str, str], list[NavItem]] | None = None
    _focused_first_child: bool = False
    highlighted_data: HomeNodeData | None = None

    def __post_init__(self) -> None:
        self.course_assignments = {}
        self.course_students = {}
        self.student_submissions = {}

    def set_courses(
        self, courses: list[NavItem], *, highlight_first: bool = False
    ) -> None:
        self.courses = courses

    def set_course_assignments(
        self,
        course_item_id: str,
        assignments: list[NavItem],
    ) -> None:
        self.course_assignments[course_item_id] = assignments

    def set_course_assignments_loading(self, course_item_id: str) -> None:
        self.course_assignments[course_item_id] = []

    def set_course_students(
        self,
        course_item_id: str,
        students: list[NavItem],
    ) -> None:
        self.course_students[course_item_id] = students

    def set_course_students_loading(self, course_item_id: str) -> None:
        self.course_students[course_item_id] = []

    def set_student_submissions(
        self,
        course_item_id: str,
        student_item_id: str,
        assignments: list[NavItem],
    ) -> None:
        self.student_submissions[(course_item_id, student_item_id)] = assignments

    def set_student_submissions_loading(
        self,
        course_item_id: str,
        student_item_id: str,
    ) -> None:
        self.student_submissions[(course_item_id, student_item_id)] = []

    def set_status(self, text: str) -> None:
        self.status = text

    def set_details(self, text: str) -> None:
        self.details = text

    def focus_tree(self) -> bool:
        self.has_focus = True
        return True

    def focused(self) -> bool:
        return self.has_focus

    def focus_first_child(self) -> bool:
        self._focused_first_child = True
        return True

    def highlighted_node_data(self) -> HomeNodeData | None:
        return self.highlighted_data

    def move_cursor_up(self) -> None:
        return None

    def move_cursor_down(self) -> None:
        return None

    def move_cursor_left(self) -> None:
        return None

    def move_cursor_right(self) -> None:
        return None


class FakeApp:
    def __init__(self) -> None:
        self.home_screen = FakeHomeScreen()

    def get_home_screen(self) -> FakeHomeScreen:
        return self.home_screen


def test_assignment_path_opens_student_for_grading() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    opened_students: list[int] = []
    controller = HomeController(
        app,
        state,
        FakeDataProvider(),
        opened_students.append,
    )

    controller.initialize()
    controller.handle_node_selected(
        HomeNodeData(kind="course", course=NavItem("c1", "CMPE 195"))
    )
    controller.handle_node_selected(
        HomeNodeData(
            kind="course_assignments_section",
            course=NavItem("c1", "CMPE 195"),
        )
    )
    controller.handle_node_selected(
        HomeNodeData(
            kind="assignment",
            course=NavItem("c1", "CMPE 195"),
            assignment=NavItem("a2", "HW 2"),
        )
    )
    app.home_screen.highlighted_data = HomeNodeData(
        kind="assignment",
        course=NavItem("c1", "CMPE 195"),
        assignment=NavItem("a2", "HW 2"),
    )
    controller.start_grading_selected_assignment()

    assert state.selection.course_id == 1
    assert state.selection.assessment_id == 2
    assert [item.item_id for item in state.student_items] == ["s1", "s2"]
    assert state.selection_labels["assignment"] == "HW 2"
    assert opened_students == [0]


def test_student_submissions_path_opens_selected_submission() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    opened_students: list[int] = []
    controller = HomeController(
        app,
        state,
        FakeDataProvider(),
        opened_students.append,
    )

    controller.initialize()
    controller.handle_node_selected(
        HomeNodeData(kind="course", course=NavItem("c1", "CMPE 195"))
    )
    controller.handle_node_selected(
        HomeNodeData(
            kind="course_students_section",
            course=NavItem("c1", "CMPE 195"),
        )
    )
    controller.handle_node_selected(
        HomeNodeData(
            kind="student",
            course=NavItem("c1", "CMPE 195"),
            student=NavItem("s1", "Tommy Liu"),
        )
    )
    controller.handle_node_selected(
        HomeNodeData(
            kind="student_submissions_section",
            course=NavItem("c1", "CMPE 195"),
            student=NavItem("s1", "Tommy Liu"),
        )
    )
    controller.handle_node_selected(
        HomeNodeData(
            kind="submission",
            course=NavItem("c1", "CMPE 195"),
            assignment=NavItem("a1", "HW 1"),
            student=NavItem("s1", "Tommy Liu"),
        )
    )

    assert state.selection.course_id == 1
    assert state.selection.assessment_id == 1
    assert state.selection.student_id == 1
    assert state.selection_labels["assignment"] == "HW 1"
    assert state.selection_labels["student"] == "Tommy Liu"
    assert opened_students == [0]


def test_assignment_actions_write_to_details_pane() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    controller = HomeController(
        app,
        state,
        FakeDataProvider(),
        lambda _index: None,
    )

    controller.initialize()
    app.home_screen.highlighted_data = HomeNodeData(
        kind="assignment",
        course=NavItem("c1", "CMPE 195"),
        assignment=NavItem("a1", "HW 1"),
    )

    controller.show_assignment_details()
    assert app.home_screen.status == "Assignment details: HW 1"

    controller.edit_assignment_rubric()
    assert app.home_screen.status == "Edit rubric: HW 1"

    controller.edit_assignment_template()
    assert app.home_screen.status == "Edit template: HW 1"
