from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

from opengrader.core.courses import ALLOWED_ASSIGNMENT_TYPES

from opengrader.tui.data import (
    AssignmentSummary,
    CourseSummary,
    StudentSummary,
    Provider,
)
from opengrader.tui.screens import Dialog, DialogField, HomeScreen
from opengrader.tui.state import TuiWorkflowState
from opengrader.tui.widgets.nav_item import NavItem
from opengrader.tui.screens.home_screen import _NavListItem
from textual.widgets import ListView, TabbedContent
from textual_fspicker import FileOpen, Filters


@dataclass(frozen=True)
class _TemplateUploadContext:
    assignment_id: int
    assignment_label: str
    strategy: str
    template_names: tuple[str, ...]
    current_index: int = 0
    template_pdf_paths: tuple[Path, ...] = ()


class HomeApp(Protocol):
    def get_home_screen(self) -> HomeScreen: ...

    def push_screen(self, screen: object, callback=None) -> None: ...


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

    def initialize(self) -> None:
        home_screen = self._app.get_home_screen()
        courses = self._course_items(self._data_provider.list_courses())
        home_screen.set_courses(courses)
        home_screen.set_status("Select a course to get started.")
        home_screen.focus_courses()

    def on_course_highlighted(self, item: NavItem) -> None:
        home = self._app.get_home_screen()
        tabs = home.query_one("#home-content-tabs", TabbedContent)
        if tabs.display:
            return

        self._select_course(item)
        self._ensure_course_data_loaded(item)

        home.show_content_tabs()
        home.set_status(f"Course: {item.label}")

    def on_assignment_highlighted(self, item: NavItem) -> None:
        home = self._app.get_home_screen()
        template_hint = "[t] Upload Template"
        assignment_id = self._numeric_id(item.item_id)
        if assignment_id is not None:
            linked_template = self._data_provider.get_assignment_template(assignment_id)
            linked_templates = (
                self._data_provider.list_assignment_templates_for_assignment(
                    assignment_id
                )
            )
            version_count = len(linked_templates)
            if linked_template is None or version_count == 0:
                template_hint = "[t] Upload Template (none linked)"
            elif linked_template.spec_pending:
                if version_count > 1:
                    template_hint = (
                        f"[t] Template: {linked_template.name} "
                        f"({version_count} versions, spec pending)"
                    )
                else:
                    template_hint = (
                        f"[t] Template: {linked_template.name} (spec pending)"
                    )
            else:
                if version_count > 1:
                    template_hint = f"[t] Template: {linked_template.name} ({version_count} versions)"
                else:
                    template_hint = f"[t] Template: {linked_template.name}"
        home.set_status(
            f"{item.label} · [g] Grade  [d] Details  [r] Rubric  {template_hint}  [n] New"
        )

    def on_assignment_selected(self, item: NavItem) -> None:
        self._select_assignment(item)
        self._start_grading_current_assignment()

    def on_student_highlighted(self, item: NavItem) -> None:
        home = self._app.get_home_screen()
        home.set_status(f"Student: {item.label} · [Enter] Show submissions")

    def on_student_selected(self, item: NavItem) -> None:
        self._select_student(item)
        home = self._app.get_home_screen()

        course_id = self._state.selection.course_id
        assignments = self._assignment_items(
            self._data_provider.list_assignments(course_id)
        )
        home.expand_student_assignments(item, assignments)
        home.set_status(f"{item.label} · Select an assignment to start grading")

    def on_student_submission_selected(
        self,
        student: NavItem,
        assignment: NavItem,
    ) -> None:
        self._select_student(student)
        self._select_assignment(assignment)

        students = self._student_items(
            self._data_provider.list_students(self._state.selection.course_id)
        )
        self._state.student_items = students
        student_index = self._student_index(student.item_id)
        if student_index is not None:
            self._open_student(student_index)

    def move_up(self) -> None:
        self._ensure_focus()
        home = self._app.get_home_screen()
        self._move_list_cursor(home, -1)

    def move_down(self) -> None:
        self._ensure_focus()
        home = self._app.get_home_screen()
        self._move_list_cursor(home, 1)

    def move_left(self) -> None:
        home = self._app.get_home_screen()
        focused_list = self._focused_list_id(home)
        if focused_list == "home-course-list":
            return
        if home.active_tab() == "students":
            home.switch_tab("assignments")
            home.focus_content()
        else:
            home.focus_courses()

    def move_right(self) -> None:
        home = self._app.get_home_screen()
        focused_list = self._focused_list_id(home)
        if focused_list == "home-course-list":
            home.focus_content()
        elif home.active_tab() == "assignments":
            home.switch_tab("students")
            home.focus_content()

    def confirm(self) -> None:
        home = self._app.get_home_screen()
        focused = self._focused_list_id(home)
        if focused is None:
            self._ensure_focus()
            return
        if focused == "home-course-list":
            item = home.selected_course()
            if item is not None:
                self.on_course_highlighted(item)
        elif focused == "home-assignment-list":
            item = home.selected_assignment()
            if item is not None:
                self.on_assignment_selected(item)
        elif focused == "home-student-list":
            lv = home.query_one("#home-student-list", ListView)
            child = lv.highlighted_child
            if isinstance(child, _NavListItem) and child.is_sub_item:
                if self._expanded_student_item is not None:
                    self.on_student_submission_selected(
                        self._expanded_student_item, child.nav
                    )
            else:
                item = home.selected_student()
                if item is not None:
                    self.on_student_selected(item)

    def refresh_students(self) -> None:
        selection = self._state.selection
        course_item_id = selection.course_item_id
        if course_item_id is None:
            return

        students = self._student_items(
            self._data_provider.list_students(selection.course_id)
        )
        home = self._app.get_home_screen()
        home.set_students(students)
        self._loaded_course_students.add(course_item_id)

    def start_grading_selected_assignment(self) -> None:
        home = self._app.get_home_screen()
        item = home.selected_assignment()
        if item is None:
            home.set_status("Highlight an assignment before starting grading.")
            return
        self._select_assignment(item)
        self._start_grading_current_assignment()

    def show_assignment_details(self) -> None:
        home = self._app.get_home_screen()
        item = home.selected_assignment()
        if item is None:
            home.set_status("Highlight an assignment to view details.")
            return
        templates = self._data_provider.list_assignment_templates_for_assignment(
            self._state.selection.assignment_id)
        home.set_status(f"Assignment details: {item.label} ({len(templates)} templates)")

    def edit_assignment_rubric(self) -> None:
        home = self._app.get_home_screen()
        item = home.selected_assignment()
        if item is None:
            home.set_status("Highlight an assignment to edit rubric.")
            return
        home.set_status(f"Edit rubric: {item.label}")

    def edit_assignment_template(self) -> None:
        home = self._app.get_home_screen()
        item = home.selected_assignment()
        if item is None:
            home.set_status("Highlight an assignment to edit template.")
            return

        course_id = self._state.selection.course_id
        assignment_id = self._numeric_id(item.item_id)
        if course_id is None or assignment_id is None:
            home.set_status("Select a valid course and assignment first.")
            return

        self._open_template_upload_dialog(
            assignment_id=assignment_id,
            assignment_label=item.label,
        )

    def create(self) -> None:
        home = self._app.get_home_screen()
        tab = home.active_tab()
        focused = self._focused_list_id(home)

        if focused == "home-course-list":
            self._open_create_course_dialog()
        elif tab == "assignments":
            course_item_id = self._state.selection.course_item_id
            course_id = self._state.selection.course_id
            if course_item_id is None or course_id is None:
                home.set_status("Select a course first.")
                return
            self._open_create_assignment_dialog(course_item_id, course_id)
        elif tab == "students":
            course_item_id = self._state.selection.course_item_id
            course_id = self._state.selection.course_id
            if course_item_id is None or course_id is None:
                home.set_status("Select a course first.")
                return
            self._open_create_student_dialog(course_item_id, course_id)
        else:
            home.set_status(
                "Press n on the course list, assignment list, or student list to create."
            )

    def _select_course(self, item: NavItem) -> None:
        selection = self._state.selection
        selection.course_item_id = item.item_id
        selection.course_id = self._numeric_id(item.item_id)
        self._state.selection_labels["course"] = item.label
        selection.assignment_item_id = None
        selection.assignment_id = None
        selection.student_item_id = None
        selection.student_id = None
        self._state.active_student_index = -1
        self._state.selection_labels["assignment"] = ""
        self._state.selection_labels["student"] = ""

    def _select_assignment(self, item: NavItem) -> None:
        selection = self._state.selection
        selection.assignment_item_id = item.item_id
        selection.assignment_id = self._numeric_id(item.item_id)
        self._state.selection_labels["assignment"] = item.label

    def _select_student(self, item: NavItem) -> None:
        selection = self._state.selection
        selection.student_item_id = item.item_id
        selection.student_id = self._numeric_id(item.item_id)
        self._state.selection_labels["student"] = item.label

    def _ensure_course_data_loaded(self, item: NavItem) -> None:
        course_item_id = item.item_id
        course_id = self._numeric_id(course_item_id)
        home = self._app.get_home_screen()

        assignments = self._assignment_items(
            self._data_provider.list_assignments(course_id)
        )
        home.set_assignments(assignments)

        students = self._student_items(self._data_provider.list_students(course_id))
        home.set_students(students)

    def _start_grading_current_assignment(self) -> None:
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

    def _ensure_focus(self) -> None:
        home = self._app.get_home_screen()
        if self._focused_list_id(home) is None:
            home.focus_courses()

    @staticmethod
    def _focused_list_id(home: HomeScreen) -> str | None:
        for list_id in (
            "home-course-list",
            "home-assignment-list",
            "home-student-list",
        ):
            lv = home.query_one(f"#{list_id}", ListView)
            if lv.has_focus:
                return list_id
        return None

    @staticmethod
    def _move_list_cursor(home: HomeScreen, direction: int) -> None:
        for list_id in (
            "home-course-list",
            "home-assignment-list",
            "home-student-list",
        ):
            lv = home.query_one(f"#{list_id}", ListView)
            if lv.has_focus:
                if direction < 0:
                    lv.action_cursor_up()
                else:
                    lv.action_cursor_down()
                return

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
            str(self._state.selection.assignment_id or ""),
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

    def _open_create_assignment_dialog(
        self,
        course_item_id: str,
        course_id: int,
    ) -> None:
        course_label = self._state.selection_labels.get("course", "")
        self._app.push_screen(
            Dialog(
                title=f"Create Assignment ({course_label})",
                submit_label="Create",
                fields=[
                    DialogField(
                        "assignment_type",
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
                course_item_id,
                course_id,
                values,
            ),
        )

    def _open_create_student_dialog(
        self,
        course_item_id: str,
        course_id: int,
    ) -> None:
        course_label = self._state.selection_labels.get("course", "")
        self._app.push_screen(
            Dialog(
                title=f"Enroll Student ({course_label})",
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
                course_item_id,
                course_id,
                values,
            ),
        )

    def _on_create_course_dialog_closed(
        self,
        values: dict[str, str] | None,
    ) -> None:
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
        except Exception as exc:
            home.set_status(f"Create course failed: {exc}")
            return

        self._loaded_course_assignments.clear()
        self._loaded_course_students.clear()
        home.set_courses(
            self._course_items(self._data_provider.list_courses()),
        )
        home.set_status(f"Created course: {created.code} {created.name}".strip())
        home.focus_courses()

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
                assignment_type=values["assignment_type"].lower(),
                title=values["title"],
                max_points=values["max_points"],
            )
        except Exception as exc:
            home.set_status(f"Create assignment failed: {exc}")
            return

        assignments = self._assignment_items(
            self._data_provider.list_assignments(course_id)
        )
        home.set_assignments(assignments)
        self._loaded_course_assignments.add(course_item_id)
        home.set_status(f"Created assignment: {created.title}")
        home.focus_content()

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
        except Exception as exc:
            home.set_status(f"Enroll student failed: {exc}")
            return

        students = self._student_items(self._data_provider.list_students(course_id))
        home.set_students(students)
        self._loaded_course_students.add(course_item_id)
        home.set_status(f"Enrolled student: {created.full_name}")
        home.focus_content()

    @staticmethod
    def _validate_assignment_form(values: dict[str, str]) -> str | None:
        assignment_type = values.get("assignment_type", "").strip().lower()
        if assignment_type not in ALLOWED_ASSIGNMENT_TYPES:
            return "Type must be one of: " + ", ".join(ALLOWED_ASSIGNMENT_TYPES) + "."
        max_points_raw = values.get("max_points", "").strip()
        try:
            parsed = Decimal(max_points_raw)
        except (InvalidOperation, ValueError):
            return "Max Points must be a valid number."
        if parsed < Decimal("0"):
            return "Max Points must be 0 or greater."
        return None

    @staticmethod
    def _validate_template_upload_form(values: dict[str, str]) -> str | None:
        template_name = values.get("name", "").strip()
        if not template_name:
            return "Template Name is required."

        strategy = values.get("strategy", "").strip().lower()
        if strategy not in ("pdf+spec", "pdf-only"):
            return "Strategy must be one of: pdf+spec, pdf-only."

        versions_raw = values.get("versions", "").strip()
        if versions_raw:
            labels = [part.strip() for part in versions_raw.split(",") if part.strip()]
            if not labels:
                return "Versions must be comma-separated labels, like A,B,C."
        return None

    def _open_template_upload_dialog(
        self,
        *,
        assignment_id: int,
        assignment_label: str,
    ) -> None:
        linked_template = self._data_provider.get_assignment_template(assignment_id)
        default_name = (
            linked_template.name if linked_template is not None else assignment_label
        )
        self._app.push_screen(
            Dialog(
                title=f"Upload Template ({assignment_label})",
                submit_label="Continue",
                fields=[
                    DialogField(
                        "name",
                        "Template Name",
                        placeholder="HW 1 Template",
                        value=default_name,
                    ),
                    DialogField(
                        "versions",
                        "Versions (Optional)",
                        placeholder="A,B,C",
                        required=False,
                    ),
                    DialogField(
                        "strategy",
                        "Upload Strategy",
                        placeholder="pdf+spec or pdf-only",
                        value="pdf+spec",
                    ),
                ],
                validator=self._validate_template_upload_form,
            ),
            lambda values: self._on_template_upload_dialog_closed(
                assignment_id=assignment_id,
                assignment_label=assignment_label,
                values=values,
            ),
        )

    def _on_template_upload_dialog_closed(
        self,
        *,
        assignment_id: int,
        assignment_label: str,
        values: dict[str, str] | None,
    ) -> None:
        if values is None:
            self._app.get_home_screen().set_status("Template upload cancelled.")
            return

        base_name = values["name"].strip()
        versions_raw = values.get("versions", "").strip()
        strategy = values["strategy"].strip().lower()

        template_names: tuple[str, ...]
        if versions_raw:
            versions = [
                part.strip() for part in versions_raw.split(",") if part.strip()
            ]
            template_names = tuple(
                self._template_name_with_version(base_name, version)
                for version in versions
            )
        else:
            template_names = (base_name,)

        context = _TemplateUploadContext(
            assignment_id=assignment_id,
            assignment_label=assignment_label,
            strategy=strategy,
            template_names=template_names,
        )
        self._open_template_pdf_picker(context)

    def _open_template_pdf_picker(self, context: _TemplateUploadContext) -> None:
        template_name = context.template_names[context.current_index]
        title_prefix = (
            f"Select Template PDF ({context.current_index + 1}/{len(context.template_names)})"
            if len(context.template_names) > 1
            else "Select Template PDF"
        )
        self._app.push_screen(
            FileOpen(
                title=f"{title_prefix}: {template_name}",
                open_button="Use PDF",
                filters=Filters(
                    (
                        "PDF",
                        lambda path: path.is_dir() or path.suffix.lower() == ".pdf",
                    ),
                    ("All", lambda _: True),
                ),
            ),
            lambda selected_path: self._on_template_pdf_selected(
                context=context,
                selected_path=selected_path,
            ),
        )

    def _on_template_pdf_selected(
        self,
        *,
        context: _TemplateUploadContext,
        selected_path: Path | None,
    ) -> None:
        home = self._app.get_home_screen()
        if selected_path is None:
            home.set_status("Template upload cancelled (no PDF selected).")
            return

        collected_pdf_paths = context.template_pdf_paths + (selected_path,)
        next_index = context.current_index + 1
        if next_index < len(context.template_names):
            next_context = _TemplateUploadContext(
                assignment_id=context.assignment_id,
                assignment_label=context.assignment_label,
                strategy=context.strategy,
                template_names=context.template_names,
                current_index=next_index,
                template_pdf_paths=collected_pdf_paths,
            )
            home.set_status(
                f"Saved template PDF {next_index}/{len(context.template_names)}."
            )
            self._open_template_pdf_picker(next_context)
            return

        final_context = _TemplateUploadContext(
            assignment_id=context.assignment_id,
            assignment_label=context.assignment_label,
            strategy=context.strategy,
            template_names=context.template_names,
            template_pdf_paths=collected_pdf_paths,
        )
        if context.strategy == "pdf-only":
            self._create_and_link_templates(
                context=final_context,
                shared_spec_json_path=None,
            )
            return

        self._open_template_spec_picker(context=final_context)

    def _open_template_spec_picker(
        self,
        *,
        context: _TemplateUploadContext,
    ) -> None:
        self._app.push_screen(
            FileOpen(
                title="Select Shared Template Spec JSON",
                open_button="Use Spec",
                filters=Filters(
                    (
                        "JSON",
                        lambda path: path.is_dir() or path.suffix.lower() == ".json",
                    ),
                    ("All", lambda _: True),
                ),
            ),
            lambda selected_path: self._on_template_spec_selected(
                context=context,
                selected_path=selected_path,
            ),
        )

    def _on_template_spec_selected(
        self,
        *,
        context: _TemplateUploadContext,
        selected_path: Path | None,
    ) -> None:
        home = self._app.get_home_screen()
        if selected_path is None:
            home.set_status("Template upload cancelled (no spec selected).")
            return

        self._create_and_link_templates(
            context=context,
            shared_spec_json_path=selected_path,
        )

    def _create_and_link_templates(
        self,
        *,
        context: _TemplateUploadContext,
        shared_spec_json_path: Path | None,
    ) -> None:
        home = self._app.get_home_screen()

        if len(context.template_pdf_paths) != len(context.template_names):
            home.set_status("Upload template failed: missing template PDFs.")
            return

        created_templates: list[tuple[int, str, bool]] = []
        try:
            for index, template_name in enumerate(context.template_names):
                created = self._data_provider.create_assignment_template(
                    assignment_id=context.assignment_id,
                    name=template_name,
                    template_pdf_path=str(context.template_pdf_paths[index]),
                    spec_json_path=(
                        str(shared_spec_json_path)
                        if shared_spec_json_path is not None
                        else None
                    ),
                )
                created_templates.append(
                    (created.id, created.name, created.spec_pending)
                )
        except Exception as exc:
            home.set_status(f"Upload template failed: {exc}")
            return

        created_template_ids = [template[0] for template in created_templates]
        created_template_names = [template[1] for template in created_templates]
        pending_count = sum(1 for template in created_templates if template[2])

        current_template_id = created_template_ids[0]
        try:
            self._data_provider.replace_assignment_templates(
                assignment_id=context.assignment_id,
                template_ids=created_template_ids,
                current_template_id=current_template_id,
            )
        except Exception as exc:
            home.set_status(f"Link templates failed: {exc}")
            return

        total = len(created_template_ids)
        if total == 1:
            if pending_count > 0:
                home.set_status(
                    f"Linked template '{created_template_names[0]}' to {context.assignment_label} (spec pending). "
                    "Generate or edit the spec later, then upload with pdf+spec."
                )
            else:
                home.set_status(
                    f"Linked template '{created_template_names[0]}' to {context.assignment_label}."
                )
            return

        if pending_count > 0:
            home.set_status(
                f"Linked {total} template versions to {context.assignment_label} "
                f"({pending_count} spec pending)."
            )
            return

        home.set_status(
            f"Linked {total} template versions to {context.assignment_label}."
        )

    @staticmethod
    def _template_name_with_version(base_name: str, version: str) -> str:
        if not version:
            return base_name
        return f"{base_name} [v{version}]"

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
                meta=assignment.assignment_type,
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
