from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, Static

from opengrader.tui.formatting import format_question_type
from opengrader.tui.statistics import (
    QuestionStatistics,
    question_statistics_chart_image,
    render_question_statistics,
)
from opengrader.tui.ui.screens.canvas_export_screen import CanvasExportScreen
from opengrader.tui.ui.widgets.breadcrumb import Breadcrumb
from opengrader.tui.ui.widgets.nav_item import NavItem
from opengrader.tui.ui.widgets.sharp_image import SharpImage
from opengrader.tui.ui.widgets.tabbed_list_pane import IndexedListView, TabbedListPane


class _QAListItem(ListItem):
    def __init__(self, nav: NavItem) -> None:
        self.nav = nav
        meta = f" [{nav.meta}]" if nav.meta else ""
        super().__init__(Label(f"{nav.label}{meta}"))

    def on_click(self, event: events.Click) -> None:
        self.post_message(QAPickerScreen.QASelected(self.nav))
        event.stop()


class _StudentListItem(ListItem):
    def __init__(self, nav: NavItem, text: str) -> None:
        self.nav = nav
        meta = f" [{nav.meta}]" if nav.meta else ""
        super().__init__(Label(f"{text}{meta}"))


class QAPickerScreen(Vertical):
    _STUDENT_NAME_TAB_INDEX = 1

    class QAHighlighted(Message):
        def __init__(self, item: NavItem) -> None:
            self.item = item
            super().__init__()

    class QASelected(Message):
        def __init__(self, item: NavItem) -> None:
            self.item = item
            super().__init__()

    class StudentHighlighted(Message):
        def __init__(self, item: NavItem) -> None:
            self.item = item
            super().__init__()

    def __init__(self, *, id: str | None = None) -> None:
        self._items: list[NavItem] = []
        self._all_items: list[NavItem] = []
        self._students: list[NavItem] = []
        self._all_students: list[NavItem] = []
        self._student_question_statuses: dict[tuple[str, str], str] = {}
        self._question_statistics: dict[str, QuestionStatistics] = {}
        self._active_list_id = "qa-list"
        self._filter_target_id: str | None = None
        self._question_filter = ""
        self._student_filter = ""
        self._student_name_list = IndexedListView(id="student-name-list")
        self._student_id_list = IndexedListView(id="student-id-list")
        super().__init__(id=id)

    def compose(self) -> ComposeResult:
        yield Breadcrumb(id="qa-breadcrumb")
        yield Label("Choose a question & answer to grade", id="qa-picker-title")
        yield Label(
            "Grade one QA across every student, Gradescope-style.",
            id="qa-picker-subtitle",
        )
        with Horizontal(id="qa-picker-workspace"):
            with Vertical(id="qa-picker-left-column"):
                yield ListView(id="qa-list")
                yield TabbedListPane(
                    "Students",
                    tabs=[
                        ("ID", self._student_id_list),
                        ("Name", self._student_name_list),
                    ],
                    id="student-list",
                )
            with Vertical(id="qa-details"):
                yield Label("Question", classes="sidebar-section-title")
                yield Static("Highlight a QA to preview it.", id="qa-preview")
                yield SharpImage(
                    None,
                    id="qa-statistics-chart",
                    fit_width_fraction=0.9,
                    allow_upscale=True,
                    max_cell_height=18,
                )
                yield Static(
                    "j/k or up/down  Move\n"
                    "tab/shift+tab  Switch pane\n"
                    "d              Student display\n"
                    "t              Teacher mode (names)\n"
                    "x              Export to Canvas\n"
                    "click or enter  Grade selected QA\n"
                    "escape          Back to assignments",
                    id="qa-keybinding-ref",
                )
        yield Static("", id="qa-status")

    def bind_assignment(
        self,
        questions: list[NavItem],
        *,
        students: list[NavItem],
        breadcrumb_labels: list[str],
        assignment_name: str,
        student_question_statuses: dict[tuple[str, str], str] | None = None,
        question_statistics: dict[str, QuestionStatistics] | None = None,
    ) -> None:
        self._items = list(questions)
        self._all_items = list(questions)
        self._all_students = list(students)
        self._students = list(students)
        self._student_question_statuses = dict(student_question_statuses or {})
        self._question_statistics = dict(question_statistics or {})
        self._filter_target_id = None
        self.query_one("#qa-breadcrumb", Breadcrumb).set_path(breadcrumb_labels)
        self.query_one("#qa-picker-title", Label).update(
            f"Choose QA for {assignment_name}"
        )

        qa_list = self.query_one("#qa-list", ListView)
        qa_list.border_title = "Questions"
        qa_list.clear()
        for question in questions:
            qa_list.append(_QAListItem(question))

        self._question_filter = ""
        self._student_filter = ""

        student_pane = self._student_pane()
        student_pane.action_pane_activate_tab(0)
        self._populate_student_lists(students)

        if questions:
            qa_list.index = 0
            self.show_question_details(questions[0])
        else:
            self.query_one("#qa-preview", Static).update(
                "No questions were found for this assignment."
            )
            self._sync_statistics_chart(None)
        if students:
            self._set_student_index(0)
        self._sync_active_list_classes()

    def focus_questions(self) -> None:
        qa_list = self.query_one("#qa-list", ListView)
        if qa_list.index is None and self._items:
            qa_list.index = 0
        self._set_active_list("qa-list")
        qa_list.focus()
        self._restore_list_highlight(qa_list)
        self.call_after_refresh(self.app.refresh_bindings)

    def focus_students(self) -> None:
        student_list = self._student_active_list()
        if student_list.index is None and self._students:
            self._set_student_index(0)
        if not self._all_students:
            self.focus_questions()
            return
        self._set_active_list("student-list")
        student_list.focus()
        self._restore_list_highlight(student_list)
        self._restore_student_details()
        self.set_timer(0.01, self._restore_student_details)
        self.call_after_refresh(self.app.refresh_bindings)

    def _restore_student_details(self) -> None:
        item = self.selected_student()
        if item is not None:
            self.show_student_details(item)

    def selected_question(self) -> NavItem | None:
        qa_list = self.query_one("#qa-list", ListView)
        child = qa_list.highlighted_child
        if isinstance(child, _QAListItem):
            return child.nav
        if qa_list.index is not None and 0 <= qa_list.index < len(self._items):
            return self._items[qa_list.index]
        return None

    def selected_student(self) -> NavItem | None:
        student_list = self._student_active_list()
        child = student_list.highlighted_child
        if isinstance(child, _StudentListItem):
            return child.nav
        if student_list.index is not None and 0 <= student_list.index < len(
            self._students
        ):
            return self._students[student_list.index]
        return None

    def _extract_prompt(self, item: NavItem) -> str:
        """Extract the question prompt text from a NavItem or its payload."""
        # Try NavItem attributes directly
        for attr in ("prompt", "description"):
            val = getattr(item, attr, None)
            if val and isinstance(val, str):
                return val

        # Try item.payload, supporting both objects and dictionaries
        payload = getattr(item, "payload", None)
        if payload:
            if isinstance(payload, dict):
                for key in ("prompt", "description", "text"):
                    val = payload.get(key)
                    if val and isinstance(val, str):
                        return val
            else:
                for attr in ("prompt", "description", "text"):
                    val = getattr(payload, attr, None)
                    if val and isinstance(val, str):
                        return val

        # Fallback to the NavItem's label (usually q1, q2...)
        return item.label

    def show_question_details(self, item: NavItem) -> None:
        status = f"\nStatus: {item.meta}" if item.meta else ""
        prompt = self._extract_prompt(item)
        type_label = format_question_type(item.question_type)
        parts = [f"Question: [b]{prompt}[/b]"]
        if type_label:
            parts.append(f"[dim]Type: {type_label}[/dim]")
        parts.append(
            f"Select this QA to grade it for all students.{status}"
            f"{self._statistics_text(item)}"
        )
        self.query_one("#qa-preview", Static).update("\n\n".join(parts))
        self._sync_statistics_chart(item)

    def show_student_details(self, item: NavItem) -> None:
        progress = self._progress_for_student(item)
        question = self.selected_question()
        if question is None:
            highlighted_question = "none"
            question_status = ""
        else:
            q_prompt = self._extract_prompt(question)
            status = self._student_question_statuses.get(
                (question.item_id, item.item_id),
                "ungraded",
            )
            question_status = f"\n{question.label} status for this student: {status}"
            highlighted_question = q_prompt

        detail_id = self._student_detail_id(item)
        identity = (
            f"{item.label} ({detail_id})" if self._teacher_mode() else detail_id
        )
        self.query_one("#qa-preview", Static).update(
            f"{identity}\n\n"
            f"Progress: {progress}\n\n"
            f"Highlighted QA: {highlighted_question}"
            f"{question_status}"
            f"{self._statistics_text(question)}"
        )
        self._sync_statistics_chart(question)

    def _progress_for_student(self, item: NavItem) -> str:
        total_questions = len(self._items)
        graded_text = item.meta.split("/", maxsplit=1)[0] if item.meta else "0"
        return f"{graded_text} / {total_questions} questions graded"

    def _statistics_text(self, question: NavItem | None) -> str:
        if question is None:
            return ""
        stats = self._question_statistics.get(question.item_id)
        if stats is None:
            return ""
        return f"\n\n{render_question_statistics(stats, include_distribution=False)}"

    def _sync_statistics_chart(self, question: NavItem | None) -> None:
        chart = self.query_one("#qa-statistics-chart", SharpImage)
        stats = (
            self._question_statistics.get(question.item_id)
            if question is not None
            else None
        )
        image = question_statistics_chart_image(stats) if stats is not None else None
        if image is None:
            chart.display = False
            chart.image = None
            return
        chart.image = image
        chart.display = True

    def move_to_index(self, index: int) -> None:
        qa_list = self.query_one("#qa-list", ListView)
        if not 0 <= index < len(qa_list.children):
            return

        self._set_active_list("qa-list")
        qa_list.index = index
        self._restore_list_highlight(qa_list)

        item = self.selected_question()
        if item is not None:
            self.show_question_details(item)

    def move_student_to_index(self, index: int) -> None:
        if not 0 <= index < len(self._students):
            return

        self._set_student_index(index)

    def set_status(self, text: str) -> None:
        self.query_one("#qa-status", Static).update(text)

    def start_filter(self) -> tuple[str, str]:
        self._filter_target_id = self._active_list_id
        if self._filter_target_id == "student-list":
            return "Search students:", self._student_filter
        return "Search questions:", self._question_filter

    def update_filter(self, query: str) -> str:
        target_id = self._filter_target_id or self._active_list_id
        self._filter_target_id = target_id
        if target_id == "student-list":
            matches = self._apply_student_filter(query)
            status = self._filter_status(
                target="students",
                query=query,
                matches=matches,
                total=len(self._all_students),
            )
            self.set_status(status)
            return status

        matches = self._apply_question_filter(query)
        status = self._filter_status(
            target="questions",
            query=query,
            matches=matches,
            total=len(self._all_items),
        )
        self.set_status(status)
        return status

    def finish_filter(self) -> None:
        target_id = self._filter_target_id or self._active_list_id
        self._filter_target_id = None
        if target_id == "student-list":
            self.focus_students()
            return
        self.focus_questions()

    def filter_mode_active(self) -> bool:
        return self._filter_target_id is not None

    def move_cursor(self, direction: int) -> None:
        self._set_active_list("qa-list")
        qa_list = self.query_one("#qa-list", ListView)
        if direction < 0:
            qa_list.action_cursor_up()
        else:
            qa_list.action_cursor_down()
        item = self.selected_question()
        if item is not None:
            self.show_question_details(item)

    def move_student_cursor(self, direction: int) -> None:
        self._set_active_list("student-list")
        student_list = self._student_active_list()
        if direction < 0:
            student_list.action_cursor_up()
        else:
            student_list.action_cursor_down()
        item = self.selected_student()
        if item is not None:
            self.show_student_details(item)

    def switch_active_pane(self) -> None:
        if self._active_list_id == "student-list":
            self.focus_questions()
            item = self.selected_question()
            if item is not None:
                self.show_question_details(item)
            return

        if not self._students:
            return
        self.focus_students()

    def on_mount(self) -> None:
        self.apply_teacher_mode()

    def apply_teacher_mode(self) -> None:
        """Sync the Students pane tabs with the current anonymity mode.

        Anonymous grading hides the Name tab entirely; teacher mode brings
        it back.
        """
        self._student_pane().set_tab_hidden(
            self._STUDENT_NAME_TAB_INDEX,
            not self._teacher_mode(),
        )

    def toggle_student_display_mode(self) -> None:
        if not self._students:
            return

        if not self._teacher_mode():
            self.set_status(
                "Anonymous grading shows IDs only. "
                "Enable teacher mode (t) to view student names."
            )
            return

        pane = self._student_pane()
        next_tab_index = 1 if pane.active_tab_index == 0 else 0
        pane.action_pane_activate_tab(next_tab_index)
        self._set_active_list("student-list")
        student_list = self._student_active_list()
        if student_list.can_focus:
            student_list.focus()
        item = self.selected_student()
        if item is not None:
            self.show_student_details(item)
        mode = "names" if next_tab_index == 1 else "IDs"
        self.set_status(f"Showing student {mode}.")

    def _apply_question_filter(self, query: str) -> int:
        qa_list = self.query_one("#qa-list", ListView)
        qa_list.clear()

        self._question_filter = query
        normalized_query = query.strip().lower()
        if not normalized_query:
            self._items = list(self._all_items)
        else:
            self._items = self._filter_items(normalized_query, self._all_items)

        for question in self._items:
            qa_list.append(_QAListItem(question))

        if self._items:
            qa_list.index = 0
            self._restore_list_highlight(qa_list)
            self.show_question_details(self._items[0])
        else:
            qa_list.index = None
            self.query_one("#qa-preview", Static).update("No matching questions.")
            self._sync_statistics_chart(None)
        return len(self._items)

    def _apply_student_filter(self, query: str) -> int:
        self._student_filter = query
        normalized_query = query.strip().lower()
        if not normalized_query:
            self._students = list(self._all_students)
        else:
            self._students = self._filter_items(normalized_query, self._all_students)

        self._populate_student_lists(self._students)

        if self._students:
            self._set_student_index(0)
            self._restore_student_details()
        else:
            self._clear_student_indexes()
            self.query_one("#qa-preview", Static).update("No matching students.")
            self._sync_statistics_chart(self.selected_question())
        return len(self._students)

    @staticmethod
    def _filter_items(query: str, items: list[NavItem]) -> list[NavItem]:
        return [
            item
            for item in items
            if query in item.item_id.lower()
            or query in item.label.lower()
            or query in item.display_id.lower()
        ]

    @staticmethod
    def _filter_status(
        *,
        target: str,
        query: str,
        matches: int,
        total: int,
    ) -> str:
        if query.strip() and matches == 0:
            return f"No matches for '{query}'"
        return f"Search {target}: {matches}/{total} matches"

    def students_have_focus(self) -> bool:
        student_pane = self._student_pane()
        if self._focused_within(student_pane):
            return True

        qa_list = self.query_one("#qa-list", ListView)
        if self._focused_within(qa_list):
            return False

        return self._active_list_id == "student-list"

    def confirm(self) -> None:
        item = self.selected_question()
        if item is not None:
            self.post_message(self.QASelected(item))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, _QAListItem):
            if self._active_list_id == "qa-list":
                self.show_question_details(event.item.nav)
                self.post_message(self.QAHighlighted(event.item.nav))
            return
        if isinstance(event.item, _StudentListItem):
            if self._active_list_id == "student-list":
                self._sync_student_index_from_list(event.item)
                self.show_student_details(event.item.nav)
                self.post_message(self.StudentHighlighted(event.item.nav))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, _QAListItem):
            self.post_message(self.QASelected(event.item.nav))
        elif isinstance(event.item, _StudentListItem):
            # Ensure we jump to the correct question when a student is selected via keyboard/click
            self._set_active_list("student-list")
            self._sync_student_index_from_list(event.item)
            self._jump_to_student_ungraded(event.item)

    def on_focus(self, event: events.Focus) -> None:
        self._activate_list_for_widget(event.control)

    def on_click(self, event: events.Click) -> None:
        student_pane = self._student_pane()
        if self._widget_contains(student_pane, event.widget):
            self._set_active_list("student-list")
            student_list = self._student_active_list()

            student = self._clicked_student_item(event.widget)
            if student is not None:
                student_list.focus()
                try:
                    self._set_student_index(list(student_list.children).index(student))
                    self._jump_to_student_ungraded(student)
                except ValueError:
                    pass
                event.stop()
                return

            if self._widget_contains(student_list, event.widget):
                student_list.focus()
                item = self.selected_student()
                if item is not None:
                    self.show_student_details(item)
                event.stop()
            return

        qa_list = self.query_one("#qa-list", ListView)
        if self._widget_contains(qa_list, event.widget):
            self._set_active_list("qa-list")
            qa_list.focus()

        item = self._clicked_qa_item(event.widget)
        if item is None:
            return
        self.post_message(self.QASelected(item.nav))
        event.stop()

    def _jump_to_student_ungraded(self, student_item: _StudentListItem) -> None:
        """Helper to jump the question list to the first ungraded item for a student."""
        target_q_idx = 0
        for i, q_nav in enumerate(self._items):
            status = self._student_question_statuses.get(
                (q_nav.item_id, student_item.nav.item_id), "ungraded"
            )
            if status != "graded":
                target_q_idx = i
                break

        qa_list = self.query_one("#qa-list", ListView)
        if 0 <= target_q_idx < len(qa_list.children):
            qa_list.index = target_q_idx
            self._restore_list_highlight(qa_list)
        self.show_student_details(student_item.nav)

    def on_key(self, event: events.Key) -> None:
        if event.key in ("tab", "shift+tab"):
            self.switch_active_pane()
            event.stop()
            return

        if event.key in ("j", "down"):
            if self.students_have_focus():
                self.move_student_cursor(1)
            else:
                self.move_cursor(1)
            event.stop()
            return
        if event.key in ("k", "up"):
            if self.students_have_focus():
                self.move_student_cursor(-1)
            else:
                self.move_cursor(-1)
            event.stop()
            return
        if event.key == "enter":
            self.confirm()
            event.stop()
            return
        if event.key == "x":
            self._open_canvas_export()
            event.stop()

    def _open_canvas_export(self) -> None:
        """Open the Canvas export dialog, prefilled from the current session."""
        provider = getattr(self.app, "data_provider", None)
        submissions = str(getattr(provider, "submissions_path", "") or "")

        workflow_state = getattr(self.app, "workflow_state", None)
        labels = workflow_state.selection_labels if workflow_state else {}
        self.app.push_screen(
            CanvasExportScreen(
                submissions_path=submissions,
                course=labels.get("course", ""),
                assignment=labels.get("assignment", ""),
            )
        )

    def _activate_list_for_widget(self, widget: Widget | None) -> None:
        student_pane = self._student_pane()
        if self._widget_contains(student_pane, widget):
            self._set_active_list("student-list")
            return

        qa_list = self.query_one("#qa-list", ListView)
        if self._widget_contains(qa_list, widget):
            self._set_active_list("qa-list")

    def _set_active_list(self, list_id: str) -> None:
        previous_list_id = self._active_list_id
        self._active_list_id = list_id
        self._sync_active_list_classes()
        if previous_list_id != list_id and self.is_attached:
            self.app.refresh_bindings()

    def _sync_active_list_classes(self) -> None:
        try:
            qa_list = self.query_one("#qa-list", ListView)
            student_pane = self._student_pane()
        except Exception:
            return
        qa_list.set_class(self._active_list_id == "qa-list", "-pane-active")
        student_pane.set_class(
            self._active_list_id == "student-list",
            "-pane-active",
        )

    def _student_pane(self) -> TabbedListPane:
        return self.query_one("#student-list", TabbedListPane)

    def _student_lists(self) -> tuple[IndexedListView, IndexedListView]:
        return self._student_id_list, self._student_name_list

    def _student_active_list(self) -> IndexedListView:
        active = self._student_pane().active_list()
        return active if active is not None else self._student_id_list

    def _populate_student_lists(self, students: list[NavItem]) -> None:
        id_list, name_list = self._student_lists()
        name_list.clear()
        id_list.clear()
        display_id_counts = self._student_display_id_counts(self._all_students)

        for student in students:
            name_list.append(_StudentListItem(student, student.label))
            id_list.append(
                _StudentListItem(
                    student,
                    self._student_display_id(student, display_id_counts),
                )
            )

        self._student_pane().refresh_index_subtitle()

    def _set_student_index(self, index: int) -> None:
        for student_list in self._student_lists():
            if 0 <= index < len(student_list.children):
                student_list.index = index
                self._restore_list_highlight(student_list)
            else:
                student_list.index = None
        self._student_pane().refresh_index_subtitle()

    def _clear_student_indexes(self) -> None:
        for student_list in self._student_lists():
            student_list.index = None
        self._student_pane().refresh_index_subtitle()

    def _sync_student_index_from_list(self, item: _StudentListItem) -> None:
        active_list = self._student_active_list()
        try:
            index = list(active_list.children).index(item)
        except ValueError:
            return
        self._set_student_index(index)

    def _teacher_mode(self) -> bool:
        """Whether names may be revealed; grading is anonymous by default."""
        state = getattr(self.app, "workflow_state", None)
        return bool(getattr(state, "teacher_mode", False))

    def _student_detail_id(self, item: NavItem) -> str:
        return self._student_display_id(
            item,
            self._student_display_id_counts(self._all_students),
        )

    @staticmethod
    def _student_display_id_counts(students: list[NavItem]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for student in students:
            display_id = QAPickerScreen._raw_student_display_id(student)
            counts[display_id] = counts.get(display_id, 0) + 1
        return counts

    @staticmethod
    def _student_display_id(item: NavItem, counts: dict[str, int]) -> str:
        display_id = QAPickerScreen._raw_student_display_id(item)
        if counts.get(display_id, 0) > 1:
            return f"{display_id} ({item.item_id})"
        return display_id

    @staticmethod
    def _raw_student_display_id(item: NavItem) -> str:
        return (item.display_id or item.item_id).strip() or item.item_id

    @staticmethod
    def _normalize_list_highlight(list_view: ListView) -> None:
        target = None
        if list_view.index is not None and 0 <= list_view.index < len(
            list_view.children
        ):
            target = list_view.children[list_view.index]

        for child in list_view.children:
            if isinstance(child, ListItem):
                child.highlighted = child is target
            else:
                child.set_class(child is target, "-highlight")

    def _restore_list_highlight(self, list_view: ListView) -> None:
        self._normalize_list_highlight(list_view)
        # ListView clear/mount settles later; reapply the restored row highlight.
        self.set_timer(0.01, lambda: self._normalize_list_highlight(list_view))

    def _focused_within(self, widget: Widget) -> bool:
        return self._widget_contains(widget, self.app.focused)

    @staticmethod
    def _widget_contains(parent: Widget, widget: Widget | None) -> bool:
        current = widget
        while current is not None:
            if current is parent:
                return True
            current = current.parent
        return False

    @staticmethod
    def _clicked_qa_item(widget: Widget | None) -> _QAListItem | None:
        current = widget
        while current is not None:
            if isinstance(current, _QAListItem):
                return current
            current = current.parent
        return None

    @staticmethod
    def _clicked_student_item(widget: Widget | None) -> _StudentListItem | None:
        current = widget
        while current is not None:
            if isinstance(current, _StudentListItem):
                return current
            current = current.parent
        return None
