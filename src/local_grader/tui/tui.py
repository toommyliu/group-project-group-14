from pathlib import Path

import fitz
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import ContentSwitcher, Footer, Input

from local_grader.tui.screens import (
    BrowseScreen,
    GradeScreen,
)
from local_grader.tui.state import (
    QuestionGrade,
    RubricOption,
    GradingSelection,
    StudentGradingRun,
    PAGE_BROWSE,
    PAGE_GRADE,
)
from local_grader.tui.widgets import MillerColumn
from local_grader.tui.widgets.miller_column import MillerItem


class TuiApp(App):
    CSS_PATH = Path(__file__).parent / "tui.tcss"
    print("CSS Path:", CSS_PATH)

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "back", "Back"),
        Binding("ctrl+j", "next_question", "Next Q", priority=True),
        Binding("ctrl+k", "previous_question", "Prev Q", priority=True),
        Binding("ctrl+right_square_bracket", "next_student", "Next Student", priority=True),
        Binding("ctrl+left_square_bracket", "previous_student", "Prev Student", priority=True),
        Binding("ctrl+g", "finalize_student", "Finalize", priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.default_image_path = str(Path("images/Grading.jpg"))
        self.selection = GradingSelection()
        self.grading_run = StudentGradingRun(
            student_id=None,
            student_name="",
            assignment_name="",
            max_points=100.0,
        )
        self.selection_labels: dict[str, str] = {
            "course": "",
            "assignment": "",
            "student": "",
        }
        self.student_items: list[MillerItem] = []
        self.active_student_index: int = -1
        self._sample_question_images: list[tuple[str, str]] = []
        self._student_status: dict[tuple[str, str, str], str] = {}

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial=PAGE_BROWSE, id="workspace-switcher"):
            yield BrowseScreen(id=PAGE_BROWSE)
            yield GradeScreen(self.default_image_path, id=PAGE_GRADE)
        yield Footer()

    def _switch_to(self, page_id: str) -> None:
        self.query_one(ContentSwitcher).current = page_id

    def on_miller_column_item_selected(self, event: MillerColumn.ItemSelected) -> None:
        browse_screen = self.query_one(f"#{PAGE_BROWSE}", BrowseScreen)

        if event.column_id == "courses":
            self.selection.course_id = self._numeric_id(event.item.item_id)
            self.selection_labels["course"] = event.item.label
            self.selection.assessment_id = None
            self.selection.student_id = None
            self.selection_labels["assignment"] = ""
            self.selection_labels["student"] = ""
            browse_screen.set_assignments(
                [
                    self._item("a1", "HW 1"),
                    self._item("a2", "HW 2"),
                    self._item("a3", "Midterm"),
                ]
            )
            browse_screen.set_students([])
            browse_screen.set_status(f"Course: {event.item.label}")
            return

        if event.column_id == "assignments":
            self.selection.assessment_id = self._numeric_id(event.item.item_id)
            self.selection_labels["assignment"] = event.item.label
            self.selection.student_id = None
            self.selection_labels["student"] = ""
            self.student_items = [
                self._item("s1", "Tommy Liu", self._student_status_for("s1")),
                self._item("s2", "Andy Cortes", self._student_status_for("s2")),
                self._item("s3", "Arjun Gill", self._student_status_for("s3")),
                self._item("s4", "Marco Lopez", self._student_status_for("s4")),
            ]
            browse_screen.set_students(self.student_items)
            browse_screen.set_status(f"Assignment: {event.item.label}")
            return

        if event.column_id == "students":
            self.selection.student_id = self._numeric_id(event.item.item_id)
            self.selection_labels["student"] = event.item.label
            for index, item in enumerate(self.student_items):
                if item.item_id == event.item.item_id:
                    self._open_student_by_index(index)
                    return

    def _open_student_by_index(self, index: int) -> None:
        if index < 0 or index >= len(self.student_items):
            return
        self.active_student_index = index
        student_item = self.student_items[index]
        self.selection.student_id = self._numeric_id(student_item.item_id)
        self.selection_labels["student"] = student_item.label

        self.grading_run.reset(
            student_id=self.selection.student_id,
            student_name=student_item.label,
            assignment_name=self.selection_labels.get("assignment") or "Assignment",
        )
        self.grading_run.max_points = 100.0
        self.grading_run.questions = self._build_question_grades()

        self._refresh_grade_screen(status="")
        self._switch_to(PAGE_GRADE)

    def _build_question_grades(self) -> list[QuestionGrade]:
        rubric_options = [
            RubricOption(label="Correct", points_delta=0.0),
            RubricOption(label="Partially Correct", points_delta=-4.0),
            RubricOption(label="Incorrect", points_delta=-10.0),
        ]
        output: list[QuestionGrade] = []
        for question_id, image_path in self._load_sample_question_images():
            output.append(
                QuestionGrade(
                    question_id=question_id,
                    image_path=image_path,
                    rubric_options=list(rubric_options),
                )
            )
        return output

    def _load_sample_question_images(self) -> list[tuple[str, str]]:
        if self._sample_question_images:
            return self._sample_question_images

        source_dir = Path("tests/question_split_output")
        image_dir = Path(".tui_cache/question_split_images")
        image_dir.mkdir(parents=True, exist_ok=True)

        question_to_path: dict[str, str] = {}
        for pdf_path in sorted(source_dir.glob("question_*.pdf")):
            image_path = image_dir / f"{pdf_path.stem}.png"
            if not image_path.exists():
                self._render_pdf_page_to_png(pdf_path, image_path)

            for question_id in self._question_ids_from_stem(pdf_path.stem):
                question_to_path[question_id] = str(image_path)

        if not question_to_path:
            return [("Q1", self.default_image_path)]

        ordered_questions = sorted(
            question_to_path.keys(),
            key=lambda qid: int(qid[1:]) if qid[1:].isdigit() else 999,
        )
        self._sample_question_images = [
            (question_id, question_to_path[question_id])
            for question_id in ordered_questions
        ]
        return self._sample_question_images

    @staticmethod
    def _question_ids_from_stem(stem: str) -> list[str]:
        raw = stem.removeprefix("question_")
        parts = [part for part in raw.split("_") if part]
        ids: list[str] = []
        for part in parts:
            if part.isdigit():
                ids.append(f"Q{int(part)}")
        if ids:
            return ids
        return [stem.upper()]

    @staticmethod
    def _render_pdf_page_to_png(pdf_path: Path, image_path: Path) -> None:
        doc = fitz.open(str(pdf_path))
        try:
            page = doc[0]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            pixmap.save(str(image_path))
        finally:
            doc.close()

    def on_grade_screen_question_selected(
        self, event: GradeScreen.QuestionSelected
    ) -> None:
        self.grading_run.select_question(event.question_index)
        self._refresh_grade_screen(status="")

    def on_grade_screen_rubric_selected(
        self, event: GradeScreen.RubricSelected
    ) -> None:
        self.grading_run.apply_option(event.option_index)
        self._refresh_grade_screen(status="")

    def on_grade_screen_comment_updated(
        self, event: GradeScreen.CommentUpdated
    ) -> None:
        self.grading_run.update_comment(event.comment)

    def on_grade_screen_return_to_browse_requested(
        self, _event: GradeScreen.ReturnToBrowseRequested
    ) -> None:
        self._switch_to(PAGE_BROWSE)

    def _refresh_grade_screen(self, status: str) -> None:
        grade_screen = self.query_one(f"#{PAGE_GRADE}", GradeScreen)
        grade_screen.bind_run(
            self.grading_run,
            breadcrumb_labels=[
                self.selection_labels.get("course") or "Course",
                self.selection_labels.get("assignment") or "Assignment",
                self.selection_labels.get("student") or "Student",
            ],
        )
        grade_screen.set_status(status)

    def _refresh_students_for_current_assignment(self) -> None:
        if not self.student_items:
            return
        refreshed = []
        for item in self.student_items:
            refreshed.append(
                self._item(
                    item.item_id, item.label, self._student_status_for(item.item_id)
                )
            )
        self.student_items = refreshed
        browse_screen = self.query_one(f"#{PAGE_BROWSE}", BrowseScreen)
        browse_screen.set_students(self.student_items)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "document-input":
            return
        note = event.value.strip()
        if not note:
            return
        event.input.value = ""

    def _status_key(self, student_item_id: str) -> tuple[str, str, str]:
        return (
            str(self.selection.course_id or ""),
            str(self.selection.assessment_id or ""),
            student_item_id,
        )

    def _student_status_for(self, student_item_id: str) -> str:
        return self._student_status.get(self._status_key(student_item_id), "ungraded")

    def _current_student_item(self) -> MillerItem | None:
        if self.active_student_index < 0:
            return None
        if self.active_student_index >= len(self.student_items):
            return None
        return self.student_items[self.active_student_index]

    def _on_grade_page(self) -> bool:
        return self.query_one(ContentSwitcher).current == PAGE_GRADE

    def action_browse(self) -> None:
        self._switch_to(PAGE_BROWSE)

    def action_next_question(self) -> None:
        if not self._on_grade_page():
            return
        self.grading_run.next_question()
        self._refresh_grade_screen(status="")

    def action_previous_question(self) -> None:
        if not self._on_grade_page():
            return
        self.grading_run.previous_question()
        self._refresh_grade_screen(status="")

    def action_next_student(self) -> None:
        if not self._on_grade_page():
            return
        next_index = self.active_student_index + 1
        if next_index >= len(self.student_items):
            self._refresh_grade_screen(status="Already on last student.")
            return
        self._open_student_by_index(next_index)

    def action_previous_student(self) -> None:
        if not self._on_grade_page():
            return
        prev_index = self.active_student_index - 1
        if prev_index < 0:
            self._refresh_grade_screen(status="Already on first student.")
            return
        self._open_student_by_index(prev_index)

    def action_finalize_student(self) -> None:
        if not self._on_grade_page():
            return
        current = self._current_student_item()
        if current is None:
            return
        self._student_status[self._status_key(current.item_id)] = "graded"
        self._refresh_students_for_current_assignment()

        next_index = self.active_student_index + 1
        if next_index < len(self.student_items):
            self._open_student_by_index(next_index)
            self._refresh_grade_screen(
                status="Student finalized. Loaded next student."
            )
            return

        self._switch_to(PAGE_BROWSE)
        browse_screen = self.query_one(f"#{PAGE_BROWSE}", BrowseScreen)
        browse_screen.set_status(
            "All students finalized for this assignment."
        )

    @staticmethod
    def _numeric_id(raw_id: str) -> int | None:
        digits = "".join(ch for ch in raw_id if ch.isdigit())
        if not digits:
            return None
        return int(digits)

    @staticmethod
    def _item(item_id: str, label: str, meta: str = "") -> MillerItem:
        return MillerItem(item_id=item_id, label=label, meta=meta)
