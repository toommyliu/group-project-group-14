from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Input, Label, ListItem, ListView, Rule, Static

from local_grader.tui.state import StudentGradingRun
from local_grader.tui.widgets.breadcrumb import Breadcrumb
from local_grader.tui.widgets.response_viewer import ResponseViewer


class GradeScreen(Vertical):
    class QuestionSelected(Message):
        def __init__(self, question_index: int) -> None:
            self.question_index = question_index
            super().__init__()

    class RubricSelected(Message):
        def __init__(self, option_index: int) -> None:
            self.option_index = option_index
            super().__init__()

    class CommentUpdated(Message):
        def __init__(self, comment: str) -> None:
            self.comment = comment
            super().__init__()

    class NextQuestionRequested(Message):
        pass

    class PreviousQuestionRequested(Message):
        pass

    class NextStudentRequested(Message):
        pass

    class PreviousStudentRequested(Message):
        pass

    class FinalizeStudentRequested(Message):
        pass

    class ReturnToBrowseRequested(Message):
        pass

    def __init__(self, default_image_path: str, *, id: str | None = None) -> None:
        self._default_image_path = default_image_path
        self._question_count = 0
        self._rubric_count = 0
        super().__init__(id=id)

    def compose(self) -> ComposeResult:
        yield Breadcrumb(id="grade-breadcrumb")
        with Horizontal(id="grade-workspace"):
            yield ResponseViewer(self._default_image_path, id="response-viewer")
            with VerticalScroll(id="grade-sidebar"):
                yield Label("", id="grade-student-name")
                yield Label("Score: 100.0", id="total-grade-label")
                yield Label("Progress: 0/0", id="grade-progress")
                yield Rule()
                yield Label("Questions", classes="sidebar-section-title")
                yield ListView(id="question-list")
                yield Rule()
                yield Label("Rubric", classes="sidebar-section-title")
                yield ListView(id="rubric-list")
                yield Input(
                    placeholder="Comment for this question…",
                    id="question-comment",
                )
                yield Rule()
                yield Static(
                    "ctrl+j / ctrl+k  Prev / Next question\n"
                    "ctrl+[ / ctrl+]  Prev / Next student\n"
                    "ctrl+g           Finalize + next student\n"
                    "escape           Back to browse",
                    id="keybinding-ref",
                )
                yield Static("", id="grade-keymap")
                yield Static("", id="db-status")

    def bind_run(self, run: StudentGradingRun, breadcrumb_labels: list[str]) -> None:
        self.query_one("#grade-breadcrumb", Breadcrumb).set_path(breadcrumb_labels)
        self.query_one("#grade-student-name", Label).update(
            f"{run.assignment_name}  ·  {run.student_name}"
        )
        self.query_one("#total-grade-label", Label).update(
            f"Score: {run.total_score:.1f} / {run.max_points:.1f}"
        )
        self.query_one("#total-grade-label", Label).set_class(
            run.total_score < 70.0, "low-grade"
        )
        self.query_one("#grade-progress", Label).update(
            f"Progress: {run.graded_count} / {run.total_questions}"
        )

        question_list = self.query_one("#question-list", ListView)
        question_list.clear()
        self._question_count = len(run.questions)
        for index, question in enumerate(run.questions):
            is_current = index == run.current_question_index
            status_icon = "✓" if question.status == "graded" else "○"
            cursor = "▸" if is_current else " "
            item_text = f"{cursor} {status_icon} {question.question_id}"
            li = ListItem(Label(item_text))
            if is_current:
                li.add_class("question-active")
            question_list.append(li)

        current = run.current_question
        if current is None:
            self.query_one("#rubric-list", ListView).clear()
            self._rubric_count = 0
            comment_input = self.query_one("#question-comment", Input)
            if comment_input.value:
                comment_input.value = ""
            return

        viewer = self.query_one("#response-viewer", ResponseViewer)
        viewer.set_image_path(current.image_path)

        rubric_list = self.query_one("#rubric-list", ListView)
        rubric_list.clear()
        self._rubric_count = len(current.rubric_options)
        for option_index, option in enumerate(current.rubric_options):
            selected = current.selected_option_index == option_index
            marker = "●" if selected else "○"
            li = ListItem(
                Label(f"{marker}  {option.label}  ({option.points_delta:+.1f})")
            )
            if selected:
                li.add_class("rubric-selected")
            rubric_list.append(li)

        comment_input = self.query_one("#question-comment", Input)
        if comment_input.value != current.comment:
            comment_input.value = current.comment

    def set_status(self, text: str) -> None:
        self.query_one("#db-status", Static).update(text)

    def set_keymap_label(self, profile_name: str) -> None:
        self.query_one("#grade-keymap", Static).update(
            f"Keymap: {profile_name.upper()} (standard keys also enabled)"
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        list_id = event.list_view.id or ""
        if event.index is None:
            return
        if list_id == "question-list":
            if 0 <= event.index < self._question_count:
                self.post_message(self.QuestionSelected(event.index))
            return
        if list_id == "rubric-list":
            if 0 <= event.index < self._rubric_count:
                self.post_message(self.RubricSelected(event.index))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "question-comment":
            self.post_message(self.CommentUpdated(event.value))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "question-comment":
            self.post_message(self.CommentUpdated(event.value))
