from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Protocol

from opengrader.core.courses import ALLOWED_ASSESSMENT_TYPES, LocalGraderDomainError
from textual.widgets.tree import TreeNode

from opengrader.tui.data import (
    AssignmentSummary,
    CourseSummary,
    StudentSummary,
    Provider,
)
from opengrader.tui.screens import Dialog, DialogField, HomeNodeData, HomeScreen
from opengrader.tui.state import TuiWorkflowState
from opengrader.tui.widgets.nav_item import NavItem


class HomeApp(Protocol):
    def get_home_screen(self) -> HomeScreen: ...

    def push_screen(self, screen: Dialog, callback=None) -> None: ...


class HomeController:
    def __init__(
        self,
        app: HomeApp,
        state: TuiWorkflowState,
        data_provider: Provider,
        open_student: Callable[[int], None],
    ) -> None:
        self._app = app
        self._state = state
        self._data_provider = data_provider
        self._open_student = open_student
        self._loaded_course_assignments: set[str] = set()
        self._loaded_course_students: set[str] = set()
        self._loaded_student_submissions: set[tuple[str, str]] = set()

    def initialize(self) -> None:
        home_screen = self._app.get_home_screen()
        home_screen.set_courses(
            self._course_items(self._data_provider.list_courses()),
            highlight_first=True,
        )
        home_screen.set_status("Use arrows to navigate tree and Enter to select.")
        home_screen.set_details(
            "Select a course to access assignment-centric and student-centric workflows."
        )
        home_screen.focus_tree()

    def handle_node_selected(self, data: HomeNodeData) -> None:
        kind = data.kind
        if kind == "courses_section":
            self._handle_courses_section(data)
            return
        if kind == "course":
            self._handle_course(data)
            return
        if kind == "course_assignments_section":
            self._handle_course_assignments_section(data)
            return
        if kind == "assignment":
            self._handle_assignment(data)
            return
        if kind == "course_students_section":
            self._handle_course_students_section(data)
            return
        if kind == "student":
            self._handle_student(data)
            return
        if kind == "student_submissions_section":
            self._handle_student_submissions_section(data)
            return
        if kind == "submission":
            self._handle_submission(data)

    def handle_node_expanded(self, data: HomeNodeData) -> None:
        kind = data.kind
        if kind == "course_assignments_section":
            self._ensure_course_assignments_loaded(data)
            return
        if kind == "course_students_section":
            self._ensure_course_students_loaded(data)
            return
        if kind == "student_submissions_section":
            self._ensure_student_submissions_loaded(data)

    def move_up(self) -> None:
        self._ensure_focus()
        self._app.get_home_screen().move_cursor_up()

    def move_down(self) -> None:
        self._ensure_focus()
        self._app.get_home_screen().move_cursor_down()

    def move_left(self) -> None:
        self._ensure_focus()
        self._app.get_home_screen().move_cursor_left()

    def move_right(self) -> None:
        self._ensure_focus()
        self._app.get_home_screen().move_cursor_right()

    def confirm(self) -> None:
        home_screen = self._app.get_home_screen()
        if not home_screen.focused():
            self._ensure_focus()
            return
        data = home_screen.highlighted_node_data()
        if data is None:
            return
        self.handle_node_selected(data)

    def refresh_students(self) -> None:
        selection = self._state.selection
        course_item_id = selection.course_item_id
        if course_item_id is None:
            return

        students = self._student_items(
            self._data_provider.list_students(selection.course_id)
        )

        home_screen = self._app.get_home_screen()
        home_screen.set_course_students(course_item_id, students)
        self._loaded_course_students.add(course_item_id)

        student_item_id = selection.student_item_id
        if student_item_id is not None:
            assignments = self._assignment_items(
                self._data_provider.list_assignments(selection.course_id)
            )
            home_screen.set_student_submissions(
                course_item_id,
                student_item_id,
                assignments,
            )
            self._loaded_student_submissions.add((course_item_id, student_item_id))

    def _ensure_focus(self) -> None:
        self._app.get_home_screen().focus_tree()

    def _handle_courses_section(self, _data: HomeNodeData) -> None:
        self._app.get_home_screen().set_status("Courses Section")
        self._app.get_home_screen().set_details(
            "Press n or right-click to add a new course."
        )

    def _handle_course(self, data: HomeNodeData) -> None:
        if data.course is None:
            return
        self._select_course(data)

        home_screen = self._app.get_home_screen()
        home_screen.set_status(f"Course: {data.course.label}")
        home_screen.set_details(
            "Choose Assignments for grading flow, or Students to inspect a student then open Submissions."
        )

    def _handle_course_assignments_section(self, data: HomeNodeData) -> None:
        if data.course is None:
            return
        self._select_course(data, clear_downstream=False)
        self._ensure_course_assignments_loaded(data)
        self._app.get_home_screen().focus_first_child()
        self._app.get_home_screen().set_status(f"Assignments for {data.course.label}")
        self._app.get_home_screen().set_details(
            "Select an assignment to start grading, inspect details, edit rubric, or edit template."
        )

    def _handle_assignment(self, data: HomeNodeData) -> None:
        if data.course is None or data.assignment is None:
            return
        self._select_course(data, clear_downstream=False)
        self._select_assignment(data)
        self._app.get_home_screen().set_status(f"Assignment: {data.assignment.label}")
        self._app.get_home_screen().set_details(
            "Actions\n"
            "- Enter: Start grading from first ungraded student\n"
            "- d: View assignment details (planned)\n"
            "- r: Edit rubric (planned)\n"
            "- t: Edit template (planned)"
        )

    def start_grading_selected_assignment(self) -> None:
        data = self._app.get_home_screen().highlighted_node_data()
        if data is None:
            return
        if data.kind != "assignment":
            self._app.get_home_screen().set_status(
                "Highlight an assignment before starting grading."
            )
            return
        self._start_grading_assignment(data)

    def show_assignment_details(self) -> None:
        data = self._app.get_home_screen().highlighted_node_data()
        if data is None or data.kind != "assignment" or data.assignment is None:
            self._app.get_home_screen().set_status(
                "Highlight an assignment to view details."
            )
            return
        self._app.get_home_screen().set_status(
            f"Assignment details: {data.assignment.label}"
        )
        self._app.get_home_screen().set_details(
            "Details view coming soon. This will show assignment metadata and grading summary."
        )

    def edit_assignment_rubric(self) -> None:
        data = self._app.get_home_screen().highlighted_node_data()
        if data is None or data.kind != "assignment" or data.assignment is None:
            self._app.get_home_screen().set_status(
                "Highlight an assignment to edit rubric."
            )
            return
        self._app.get_home_screen().set_status(f"Edit rubric: {data.assignment.label}")
        self._app.get_home_screen().set_details(
            "Rubric editor coming soon. This action is reserved for rubric configuration."
        )

    def edit_assignment_template(self) -> None:
        data = self._app.get_home_screen().highlighted_node_data()
        if data is None or data.kind != "assignment" or data.assignment is None:
            self._app.get_home_screen().set_status(
                "Highlight an assignment to edit template."
            )
            return
        self._app.get_home_screen().set_status(
            f"Edit template: {data.assignment.label}"
        )
        self._app.get_home_screen().set_details(
            "Template editor coming soon. This action is reserved for template setup."
        )

    def create(self) -> None:
        data = self._app.get_home_screen().highlighted_node_data()
        if data is None:
            self._app.get_home_screen().set_status(
                "Select a section (Courses, Assignments, or Students) and press n to create."
            )
            return

        kind = data.kind
        if kind == "courses_section":
            self._open_create_course_dialog()
        elif kind == "course_assignments_section" and data.course:
            self._open_create_assignment_dialog(data)
        elif kind == "course_students_section" and data.course:
            self._open_create_student_dialog(data)
        else:
            self._app.get_home_screen().set_status(
                "Select a section (Courses, Assignments, or Students) and press n to create."
            )

    def show_context_menu(
        self,
        _node: TreeNode[HomeNodeData],
        data: HomeNodeData,
    ) -> None:
        if data.kind == "courses_section":
            self._app.get_home_screen().set_status("Right-click: Add Course")
        elif data.kind == "course_assignments_section" and data.course:
            self._app.get_home_screen().set_status(
                f"Right-click: Add Assignment to {data.course.label}"
            )
        elif data.kind == "course_students_section" and data.course:
            self._app.get_home_screen().set_status(
                f"Right-click: Add Student to {data.course.label}"
            )

    def _start_grading_assignment(self, data: HomeNodeData) -> None:
        if data.course is None or data.assignment is None:
            return
        self._select_course(data, clear_downstream=False)
        self._select_assignment(data)

        students = self._student_items(
            self._data_provider.list_students(self._state.selection.course_id)
        )
        self._state.student_items = students
        if not students:
            self._app.get_home_screen().set_status("No students found for this course.")
            return
        student_index = self._first_ungraded_student_index() or 0
        if student_index is not None:
            self._open_student(student_index)

    def _handle_course_students_section(self, data: HomeNodeData) -> None:
        if data.course is None:
            return
        self._select_course(data, clear_downstream=False)
        self._ensure_course_students_loaded(data)
        self._app.get_home_screen().focus_first_child()
        self._app.get_home_screen().set_status(f"Students in {data.course.label}")
        self._app.get_home_screen().set_details(
            "Select a student, then open Submissions to inspect a specific submission."
        )

    def _handle_student(self, data: HomeNodeData) -> None:
        if data.course is None or data.student is None:
            return
        self._select_course(data, clear_downstream=False)
        selection = self._state.selection
        selection.student_item_id = data.student.item_id
        selection.student_id = self._numeric_id(data.student.item_id)
        self._state.selection_labels["student"] = data.student.label
        self._app.get_home_screen().set_status(f"Student: {data.student.label}")
        self._app.get_home_screen().set_details(
            "Open Submissions to choose an assignment for this student."
        )

    def _handle_student_submissions_section(self, data: HomeNodeData) -> None:
        if data.course is None or data.student is None:
            return
        self._select_course(data, clear_downstream=False)
        self._state.selection.student_item_id = data.student.item_id
        self._state.selection.student_id = self._numeric_id(data.student.item_id)
        self._state.selection_labels["student"] = data.student.label
        self._ensure_student_submissions_loaded(data)
        self._app.get_home_screen().focus_first_child()
        self._app.get_home_screen().set_status(f"Submissions for {data.student.label}")
        self._app.get_home_screen().set_details(
            "Select a submission to open grading for that assignment/student pair."
        )

    def _ensure_course_assignments_loaded(self, data: HomeNodeData) -> None:
        if data.course is None:
            return
        course_item_id = data.course.item_id
        if course_item_id in self._loaded_course_assignments:
            return
        home_screen = self._app.get_home_screen()
        home_screen.set_course_assignments_loading(course_item_id)
        home_screen.set_status(f"Loading assignments for {data.course.label}...")
        home_screen.set_details("Fetching assignments...")
        assignments = self._assignment_items(
            self._data_provider.list_assignments(self._numeric_id(course_item_id))
        )
        home_screen.set_course_assignments(course_item_id, assignments)
        self._loaded_course_assignments.add(course_item_id)

    def _ensure_course_students_loaded(self, data: HomeNodeData) -> None:
        if data.course is None:
            return
        course_item_id = data.course.item_id
        if course_item_id in self._loaded_course_students:
            return
        home_screen = self._app.get_home_screen()
        home_screen.set_course_students_loading(course_item_id)
        home_screen.set_status(f"Loading students for {data.course.label}...")
        home_screen.set_details("Fetching students...")
        students = self._student_items(
            self._data_provider.list_students(self._numeric_id(course_item_id))
        )
        home_screen.set_course_students(course_item_id, students)
        self._loaded_course_students.add(course_item_id)

    def _ensure_student_submissions_loaded(self, data: HomeNodeData) -> None:
        if data.course is None or data.student is None:
            return
        key = (data.course.item_id, data.student.item_id)
        if key in self._loaded_student_submissions:
            return
        home_screen = self._app.get_home_screen()
        home_screen.set_student_submissions_loading(
            data.course.item_id,
            data.student.item_id,
        )
        home_screen.set_status(f"Loading submissions for {data.student.label}...")
        home_screen.set_details("Fetching submissions...")
        assignments = self._assignment_items(
            self._data_provider.list_assignments(self._numeric_id(data.course.item_id))
        )
        home_screen.set_student_submissions(
            data.course.item_id,
            data.student.item_id,
            assignments,
        )
        self._loaded_student_submissions.add(key)

    def _handle_submission(self, data: HomeNodeData) -> None:
        if data.course is None or data.student is None or data.assignment is None:
            return
        self._select_course(data, clear_downstream=False)
        self._select_assignment(data, clear_student=False)
        selection = self._state.selection
        selection.student_item_id = data.student.item_id
        selection.student_id = self._numeric_id(data.student.item_id)
        self._state.selection_labels["student"] = data.student.label

        self._state.student_items = self._student_items(
            self._data_provider.list_students(self._state.selection.course_id)
        )
        student_index = self._student_index(data.student.item_id)
        if student_index is not None:
            self._open_student(student_index)

    def _select_course(
        self,
        data: HomeNodeData,
        *,
        clear_downstream: bool = True,
    ) -> None:
        if data.course is None:
            return
        selection = self._state.selection
        selection.course_item_id = data.course.item_id
        selection.course_id = self._numeric_id(data.course.item_id)
        self._state.selection_labels["course"] = data.course.label
        if not clear_downstream:
            return
        selection.assessment_item_id = None
        selection.assessment_id = None
        selection.student_item_id = None
        selection.student_id = None
        self._state.active_student_index = -1
        self._state.selection_labels["assignment"] = ""
        self._state.selection_labels["student"] = ""

    def _select_assignment(
        self,
        data: HomeNodeData,
        *,
        clear_student: bool = True,
    ) -> None:
        if data.assignment is None:
            return
        selection = self._state.selection
        selection.assessment_item_id = data.assignment.item_id
        selection.assessment_id = self._numeric_id(data.assignment.item_id)
        self._state.selection_labels["assignment"] = data.assignment.label
        if not clear_student:
            return
        selection.student_item_id = None
        selection.student_id = None
        self._state.active_student_index = -1
        self._state.selection_labels["student"] = ""

    def _first_ungraded_student_index(self) -> int | None:
        for index, item in enumerate(self._state.student_items):
            if self._student_status_for(item.item_id) != "graded":
                return index
        if self._state.student_items:
            return 0
        return None

    def _status_key(self, student_item_id: str) -> tuple[str, str, str]:
        return (
            str(self._state.selection.course_id or ""),
            str(self._state.selection.assessment_id or ""),
            student_item_id,
        )

    def _student_status_for(self, student_item_id: str) -> str:
        return self._state.student_status.get(
            self._status_key(student_item_id),
            "ungraded",
        )

    def _student_index(self, item_id: str) -> int | None:
        for index, item in enumerate(self._state.student_items):
            if item.item_id == item_id:
                return index
        return None

    @staticmethod
    def _numeric_id(raw_id: str) -> int | None:
        digits = "".join(ch for ch in raw_id if ch.isdigit())
        if not digits:
            return None
        return int(digits)

    def _open_create_course_dialog(self) -> None:
        self._app.push_screen(
            Dialog(
                title="Create Course",
                submit_label="Create",
                fields=[
                    DialogField("code", "Course Code", placeholder="CMPE-195"),
                    DialogField("name", "Course Name", placeholder="Senior Design"),
                    DialogField("term", "Term", placeholder="Spring 2026"),
                    DialogField("section", "Section", placeholder="01"),
                ],
            ),
            self._on_create_course_dialog_closed,
        )

    def _open_create_assignment_dialog(self, data: HomeNodeData) -> None:
        if data.course is None:
            return
        course_id = self._numeric_id(data.course.item_id)
        if course_id is None:
            self._app.get_home_screen().set_status(
                "Unable to identify selected course."
            )
            return
        self._app.push_screen(
            Dialog(
                title=f"Create Assignment ({data.course.label})",
                submit_label="Create",
                fields=[
                    DialogField(
                        "assessment_type",
                        "Type",
                        placeholder="assignment/quiz/exam/other",
                        value="assignment",
                    ),
                    DialogField("title", "Title", placeholder="HW 3"),
                    DialogField("max_points", "Max Points", placeholder="100"),
                ],
                validator=self._validate_assignment_form,
            ),
            lambda values: self._on_create_assignment_dialog_closed(
                data.course.item_id,
                course_id,
                values,
            ),
        )

    def _open_create_student_dialog(self, data: HomeNodeData) -> None:
        if data.course is None:
            return
        course_id = self._numeric_id(data.course.item_id)
        if course_id is None:
            self._app.get_home_screen().set_status(
                "Unable to identify selected course."
            )
            return
        self._app.push_screen(
            Dialog(
                title=f"Enroll Student ({data.course.label})",
                submit_label="Enroll",
                fields=[
                    DialogField("sid", "Student ID", placeholder="100000001"),
                    DialogField("first_name", "First Name", placeholder="Ada"),
                    DialogField("middle_name", "Middle Name", required=False),
                    DialogField("last_name", "Last Name", placeholder="Lovelace"),
                    DialogField("email", "Email", required=False),
                ],
            ),
            lambda values: self._on_create_student_dialog_closed(
                data.course.item_id,
                course_id,
                values,
            ),
        )

    def _on_create_course_dialog_closed(self, values: dict[str, str] | None) -> None:
        if values is None:
            return
        home = self._app.get_home_screen()
        try:
            created = self._data_provider.create_course(
                code=values["code"],
                name=values["name"],
                term=values["term"],
                section=values["section"],
            )
        except LocalGraderDomainError as exc:
            home.set_status(f"Create course failed: {exc}")
            return

        self._loaded_course_assignments.clear()
        self._loaded_course_students.clear()
        self._loaded_student_submissions.clear()
        home.set_courses(
            self._course_items(self._data_provider.list_courses()),
            highlight_first=True,
        )
        home.set_status(f"Created course: {created.code} {created.name}".strip())
        home.set_details(
            "Course created. Expand the course to add assignments and students."
        )
        home.focus_tree()

    def _on_create_assignment_dialog_closed(
        self,
        course_item_id: str,
        course_id: int,
        values: dict[str, str] | None,
    ) -> None:
        if values is None:
            return
        home = self._app.get_home_screen()
        try:
            created = self._data_provider.create_assignment(
                course_id=course_id,
                assessment_type=values["assessment_type"].lower(),
                title=values["title"],
                max_points=values["max_points"],
            )
        except LocalGraderDomainError as exc:
            home.set_status(f"Create assignment failed: {exc}")
            return

        assignments = self._assignment_items(
            self._data_provider.list_assignments(course_id)
        )
        home.set_course_assignments(course_item_id, assignments)
        self._loaded_course_assignments.add(course_item_id)
        stale_submission_keys = [
            key for key in self._loaded_student_submissions if key[0] == course_item_id
        ]
        for key in stale_submission_keys:
            self._loaded_student_submissions.discard(key)
        home.set_status(f"Created assignment: {created.title}")
        home.set_details("Assignment created and loaded.")
        home.focus_tree()

    def _on_create_student_dialog_closed(
        self,
        course_item_id: str,
        course_id: int,
        values: dict[str, str] | None,
    ) -> None:
        if values is None:
            return
        home = self._app.get_home_screen()
        try:
            created = self._data_provider.create_student(
                course_id=course_id,
                sid=values["sid"],
                first_name=values["first_name"],
                last_name=values["last_name"],
                middle_name=values["middle_name"] or None,
                email=values["email"] or None,
            )
        except LocalGraderDomainError as exc:
            home.set_status(f"Enroll student failed: {exc}")
            return

        students = self._student_items(self._data_provider.list_students(course_id))
        home.set_course_students(course_item_id, students)
        self._loaded_course_students.add(course_item_id)
        home.set_status(f"Enrolled student: {created.full_name}")
        home.set_details("Student enrolled and loaded.")
        home.focus_tree()

    @staticmethod
    def _validate_assignment_form(values: dict[str, str]) -> str | None:
        assessment_type = values.get("assessment_type", "").strip().lower()
        if assessment_type not in ALLOWED_ASSESSMENT_TYPES:
            return "Type must be one of: " + ", ".join(ALLOWED_ASSESSMENT_TYPES) + "."
        max_points_raw = values.get("max_points", "").strip()
        try:
            parsed = Decimal(max_points_raw)
        except (InvalidOperation, ValueError):
            return "Max Points must be a valid number."
        if parsed < Decimal("0"):
            return "Max Points must be 0 or greater."
        return None

    def _course_items(self, courses: list[CourseSummary]) -> list[NavItem]:
        return [
            NavItem(
                item_id=f"c{course.id}",
                label=f"{course.code} {course.name}".strip(),
                meta=f"{course.term} {course.section}".strip(),
            )
            for course in courses
        ]

    def _assignment_items(self, assignments: list[AssignmentSummary]) -> list[NavItem]:
        return [
            NavItem(
                item_id=f"a{assignment.id}",
                label=assignment.title,
                meta=assignment.assessment_type,
            )
            for assignment in assignments
        ]

    def _student_items(self, students: list[StudentSummary]) -> list[NavItem]:
        return [
            NavItem(
                item_id=f"s{student.id}",
                label=student.full_name,
                meta=self._student_status_for(f"s{student.id}"),
            )
            for student in students
        ]
