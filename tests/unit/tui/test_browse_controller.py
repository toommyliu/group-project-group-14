from opengrader.tui.controllers.home_controller import HomeController
from opengrader.tui.state import TuiWorkflowState
from opengrader.tui.widgets.nav_item import NavItem

from .fakes import FakeDataProvider


class FakeTabbedContent:
    def __init__(self) -> None:
        self.display = False


class FakeHomeScreen:
    def __init__(self) -> None:
        self.status: str = ""
        self.courses: list[NavItem] | None = None
        self.assignments: list[NavItem] | None = None
        self.students: list[NavItem] | None = None
        self.expanded_student: NavItem | None = None
        self.expanded_assignments: list[NavItem] | None = None
        self._active_tab: str = "assignments"
        self._focused_list: str = "home-course-list"
        self._tabs = FakeTabbedContent()

    def query_one(self, selector: str, widget_type=None) -> FakeTabbedContent:
        if "home-content-tabs" in selector:
            return self._tabs
        raise ValueError(f"Unknown selector: {selector}")

    def set_courses(self, courses: list[NavItem]) -> None:
        self.courses = courses

    def set_assignments(self, assignments: list[NavItem]) -> None:
        self.assignments = assignments

    def set_students(self, students: list[NavItem]) -> None:
        self.students = students

    def expand_student_assignments(
        self,
        student: NavItem,
        assignments: list[NavItem],
    ) -> None:
        self.expanded_student = student
        self.expanded_assignments = assignments

    def collapse_student_assignments(self) -> None:
        self.expanded_student = None
        self.expanded_assignments = None

    def set_status(self, text: str) -> None:
        self.status = text

    def focus_courses(self) -> None:
        self._focused_list = "home-course-list"

    def show_content_tabs(self) -> None:
        pass

    def focus_content(self) -> None:
        if self._active_tab == "assignments":
            self._focused_list = "home-assignment-list"
        else:
            self._focused_list = "home-student-list"

    def active_tab(self) -> str:
        return self._active_tab

    def switch_tab(self, tab_id: str) -> None:
        self._active_tab = tab_id

    def selected_course(self) -> NavItem | None:
        return None

    def selected_assignment(self) -> NavItem | None:
        return None

    def selected_student(self) -> NavItem | None:
        return None


class FakeApp:
    def __init__(self) -> None:
        self.home_screen = FakeHomeScreen()

    def get_home_screen(self) -> FakeHomeScreen:
        return self.home_screen


def test_initialize_populates_courses() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    controller = HomeController(app, state, FakeDataProvider(), lambda _: None)

    controller.initialize()

    assert app.home_screen.courses is not None
    assert len(app.home_screen.courses) == 2
    assert app.home_screen.courses[0].item_id == "c1"


def test_course_highlighted_loads_assignments_and_students() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    controller = HomeController(app, state, FakeDataProvider(), lambda _: None)
    controller.initialize()

    course = NavItem("c1", "CMPE 195", "Spring 2026")
    controller.on_course_highlighted(course)

    assert state.selection.course_id == 1
    assert state.selection_labels["course"] == "CMPE 195"
    assert app.home_screen.assignments is not None
    assert len(app.home_screen.assignments) == 2
    assert app.home_screen.students is not None
    assert len(app.home_screen.students) == 2


def test_assignment_selected_opens_grading() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    opened_students: list[int] = []
    controller = HomeController(app, state, FakeDataProvider(), opened_students.append)
    controller.initialize()

    controller.on_course_highlighted(NavItem("c1", "CMPE 195"))
    controller.on_assignment_selected(NavItem("a2", "HW 2"))

    assert state.selection.course_id == 1
    assert state.selection.assessment_id == 2
    assert state.selection_labels["assignment"] == "HW 2"
    assert [item.item_id for item in state.student_items] == ["s1", "s2"]
    assert opened_students == [0]


def test_student_selected_expands_assignments() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    controller = HomeController(app, state, FakeDataProvider(), lambda _: None)
    controller.initialize()

    controller.on_course_highlighted(NavItem("c1", "CMPE 195"))
    controller.on_student_selected(NavItem("s1", "Tommy Liu"))

    assert state.selection.student_id == 1
    assert state.selection_labels["student"] == "Tommy Liu"
    assert app.home_screen.expanded_student == NavItem("s1", "Tommy Liu")
    assert app.home_screen.expanded_assignments is not None
    assert len(app.home_screen.expanded_assignments) == 2


def test_student_submission_selected_opens_grading() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    opened_students: list[int] = []
    controller = HomeController(app, state, FakeDataProvider(), opened_students.append)
    controller.initialize()

    controller.on_course_highlighted(NavItem("c1", "CMPE 195"))
    controller.on_student_submission_selected(
        student=NavItem("s1", "Tommy Liu"),
        assignment=NavItem("a1", "HW 1"),
    )

    assert state.selection.course_id == 1
    assert state.selection.assessment_id == 1
    assert state.selection.student_id == 1
    assert state.selection_labels["assignment"] == "HW 1"
    assert state.selection_labels["student"] == "Tommy Liu"
    assert opened_students == [0]


def test_assignment_actions_update_status() -> None:
    app = FakeApp()
    state = TuiWorkflowState()
    controller = HomeController(app, state, FakeDataProvider(), lambda _: None)
    controller.initialize()

    controller.on_course_highlighted(NavItem("c1", "CMPE 195"))

    controller.show_assignment_details()
    assert "Highlight an assignment" in app.home_screen.status

    controller.edit_assignment_rubric()
    assert "Highlight an assignment" in app.home_screen.status

    controller.edit_assignment_template()
    assert "Highlight an assignment" in app.home_screen.status
