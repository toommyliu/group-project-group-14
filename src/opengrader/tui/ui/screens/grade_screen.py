from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Input, Label, ListItem, ListView, Rule, Static

from opengrader.tui.state import StudentGradingRun
from opengrader.tui.ui.widgets.breadcrumb import Breadcrumb
from opengrader.tui.ui.widgets.response_viewer import ResponseViewer


class _RubricListItem(ListItem):
    def __init__(self) -> None:
        self.label = Label("")
        super().__init__(self.label)


class GradeScreen(Vertical):
    class RubricSelected(Message):
        def __init__(self, option_index: int) -> None:
            self.option_index = option_index
            super().__init__()

    class CommentUpdated(Message):
        def __init__(self, comment: str) -> None:
            self.comment = comment
            super().__init__()

    class NextStudentRequested(Message):
        pass

    class PreviousStudentRequested(Message):
        pass

    class FinalizeStudentRequested(Message):
        pass

    class ReturnToHomeRequested(Message):
        pass

    def __init__(self, default_image_path: str, *, id: str | None = None) -> None:
        self._default_image_path = default_image_path
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
                yield Label("Rubric", classes="sidebar-section-title")
                yield ListView(id="rubric-list")
                yield Input(
                    placeholder="Comment for this question...",
                    id="question-comment",
                )
                yield Rule()
                yield Static("", id="db-status")

    def bind_run(self, run: StudentGradingRun, breadcrumb_labels: list[str]) -> None:
        self.query_one("#grade-breadcrumb", Breadcrumb).set_path(breadcrumb_labels)
        self.query_one("#grade-student-name", Label).update(
            f"{run.assignment_name}  -  {run.student_name}"
        )
        self.query_one("#total-grade-label", Label).update(
            f"Score: {run.total_score:.1f} / {run.max_points:.1f}"
        )
        self.query_one("#total-grade-label", Label).set_class(
            run.total_score < 70.0, "low-grade"
        )
        self.set_progress(f"Progress: {run.graded_count} / {run.total_questions}")

        current = run.current_question
        if current is None:
            rubric_list = self.query_one("#rubric-list", ListView)
            for child in list(rubric_list.children):
                child.remove()
            self._rubric_count = 0
            comment_input = self.query_one("#question-comment", Input)
            if comment_input.value:
                comment_input.value = ""
            return

        viewer = self.query_one("#response-viewer", ResponseViewer)
        viewer.set_image_path(current.image_path)

        self._rubric_count = len(current.rubric_options)
        self._sync_rubric_options(current)

        comment_input = self.query_one("#question-comment", Input)
        if comment_input.value != current.comment:
            comment_input.value = current.comment

    def set_progress(self, text: str) -> None:
        self.query_one("#grade-progress", Label).update(text)

    def _sync_rubric_options(self, current) -> None:
        rubric_list = self.query_one("#rubric-list", ListView)
        option_count = len(current.rubric_options)

        while len(rubric_list.children) < option_count:
            rubric_list.append(_RubricListItem())

        for extra_child in list(rubric_list.children[option_count:]):
            extra_child.remove()

        for option_index, option in enumerate(current.rubric_options):
            selected = current.selected_option_index == option_index
            marker = "*" if selected else "o"
            li = rubric_list.children[option_index]
            if not isinstance(li, _RubricListItem):
                continue
            li.label.update(f"{marker}  {option.label}  ({option.points_delta:+.1f})")
            if selected:
                li.add_class("rubric-selected")
                continue
            li.remove_class("rubric-selected")

    def set_status(self, text: str) -> None:
        self.query_one("#db-status", Static).update(text)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.index is None:
            return
        if (
            event.list_view.id == "rubric-list"
            and 0 <= event.index < self._rubric_count
        ):
            self.post_message(self.RubricSelected(event.index))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "question-comment":
            self.post_message(self.CommentUpdated(event.value))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "question-comment":
            self.post_message(self.CommentUpdated(event.value))
