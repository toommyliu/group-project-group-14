from pathlib import Path

from opengrader.tui.controllers.home_controller import HomeController
from opengrader.tui.state import TuiWorkflowState
from opengrader.tui.ui.widgets.nav_item import NavItem
from opengrader.tui.data import AssignmentTemplateSummary

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


class TemplateFlowHomeScreen(FakeHomeScreen):
    def __init__(self) -> None:
        super().__init__()
        self._selected_assignment = NavItem("a1", "HW 1")

    def selected_assignment(self) -> NavItem | None:
        return self._selected_assignment


class TemplateFlowProvider(FakeDataProvider):
    def __init__(self) -> None:
        self.created_template_args: list[dict[str, object]] = []
        self.linked_assignment: tuple[int, int] | None = None
        self.replaced_assignment: tuple[int, list[int], int] | None = None

    def get_assignment_template(
        self,
        assignment_id: int | None,
    ) -> AssignmentTemplateSummary | None:
        _ = assignment_id
        return None

    def create_assignment_template(
        self,
        *,
        assignment_id: int,
        name: str,
        template_pdf_path: str,
        spec_json_path: str | None = None,
    ) -> AssignmentTemplateSummary:
        self.created_template_args.append(
            {
                "assignment_id": assignment_id,
                "name": name,
                "template_pdf_path": template_pdf_path,
                "spec_json_path": spec_json_path,
            }
        )
        template_id = 41 + len(self.created_template_args)
        return AssignmentTemplateSummary(
            id=template_id,
            course_id=1,
            name=name,
            template_pdf_relpath=(
                "courses/cmpe-195-spring-2026/"
                f"assignments/{assignment_id}-hw-1/templates/"
                f"{name.lower().replace(' ', '-')}-t{template_id}/template.pdf"
            ),
            spec_json_relpath=(
                "courses/cmpe-195-spring-2026/"
                f"assignments/{assignment_id}-hw-1/templates/"
                f"{name.lower().replace(' ', '-')}-t{template_id}/spec.json"
                if spec_json_path is not None
                else (
                    "courses/cmpe-195-spring-2026/"
                    f"assignments/{assignment_id}-hw-1/templates/"
                    f"{name.lower().replace(' ', '-')}-t{template_id}/spec.pending.json"
                )
            ),
            spec_pending=spec_json_path is None,
        )

    def set_assignment_template(
        self,
        *,
        assignment_id: int,
        template_id: int,
    ) -> None:
        self.linked_assignment = (assignment_id, template_id)

    def replace_assignment_templates(
        self,
        *,
        assignment_id: int,
        template_ids: list[int],
        current_template_id: int,
    ) -> None:
        self.replaced_assignment = (assignment_id, template_ids, current_template_id)

    def list_assignment_templates_for_assignment(
        self,
        assignment_id: int | None,
    ) -> list[AssignmentTemplateSummary]:
        _ = assignment_id
        return []


class TemplateFlowApp:
    def __init__(self, screen_results: list[object]) -> None:
        self.home_screen = TemplateFlowHomeScreen()
        self._screen_results = list(screen_results)

    def get_home_screen(self) -> TemplateFlowHomeScreen:
        return self.home_screen

    def push_screen(self, screen: object, callback=None) -> None:
        _ = screen
        if callback is None:
            return
        result = self._screen_results.pop(0)
        callback(result)


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
    assert state.selection.assignment_id == 2
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
    assert state.selection.assignment_id == 1
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


def test_edit_assignment_template_uploads_pdf_and_spec() -> None:
    app = TemplateFlowApp(
        [
            {"name": "HW 1 Template", "versions": "A", "strategy": "pdf+spec"},
            Path("/tmp/template.pdf"),
            Path("/tmp/spec.json"),
        ]
    )
    state = TuiWorkflowState()
    state.selection.course_id = 1
    provider = TemplateFlowProvider()
    controller = HomeController(app, state, provider, lambda _: None)

    controller.edit_assignment_template()

    assert len(provider.created_template_args) == 1
    assert provider.created_template_args[0]["name"] == "HW 1 Template [vA]"
    assert provider.created_template_args[0]["assignment_id"] == 1
    assert provider.created_template_args[0]["spec_json_path"] == "/tmp/spec.json"
    assert provider.replaced_assignment == (1, [42], 42)
    assert "Linked template 'HW 1 Template [vA]'" in app.home_screen.status


def test_edit_assignment_template_supports_pdf_only_strategy() -> None:
    app = TemplateFlowApp(
        [
            {"name": "HW 1 Template", "versions": "", "strategy": "pdf-only"},
            Path("/tmp/template.pdf"),
        ]
    )
    state = TuiWorkflowState()
    state.selection.course_id = 1
    provider = TemplateFlowProvider()
    controller = HomeController(app, state, provider, lambda _: None)

    controller.edit_assignment_template()

    assert len(provider.created_template_args) == 1
    assert provider.created_template_args[0]["name"] == "HW 1 Template"
    assert provider.created_template_args[0]["assignment_id"] == 1
    assert provider.created_template_args[0]["spec_json_path"] is None
    assert provider.replaced_assignment == (1, [42], 42)
    assert "spec pending" in app.home_screen.status


def test_edit_assignment_template_supports_multiple_versions() -> None:
    app = TemplateFlowApp(
        [
            {
                "name": "HW 1 Template",
                "versions": "A,B",
                "strategy": "pdf+spec",
            },
            Path("/tmp/template-a.pdf"),
            Path("/tmp/template-b.pdf"),
            Path("/tmp/spec-shared.json"),
        ]
    )
    state = TuiWorkflowState()
    state.selection.course_id = 1
    provider = TemplateFlowProvider()
    controller = HomeController(app, state, provider, lambda _: None)

    controller.edit_assignment_template()

    assert len(provider.created_template_args) == 2
    assert provider.created_template_args[0]["name"] == "HW 1 Template [vA]"
    assert provider.created_template_args[1]["name"] == "HW 1 Template [vB]"
    assert (
        provider.created_template_args[0]["spec_json_path"] == "/tmp/spec-shared.json"
    )
    assert (
        provider.created_template_args[1]["spec_json_path"] == "/tmp/spec-shared.json"
    )
    assert provider.replaced_assignment == (1, [42, 43], 42)
    assert "Linked 2 template versions" in app.home_screen.status
