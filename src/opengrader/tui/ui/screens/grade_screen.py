from textual import events
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


class _SidebarResizeHandle(Static):
    def __init__(self) -> None:
        super().__init__(
            " ",
            id="grade-sidebar-resize-handle",
            classes="grade-screen__resize-handle",
        )
        self._dragging = False

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 1:
            return
        self._dragging = True
        self.capture_mouse()
        self.add_class("grade-screen__resize-handle--dragging")
        self._resize(event.screen_x)
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._dragging:
            return
        self._resize(event.screen_x)
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if not self._dragging:
            return
        self._dragging = False
        self.release_mouse()
        self.remove_class("grade-screen__resize-handle--dragging")
        self._resize(event.screen_x)
        event.stop()

    def on_leave(self, _event: events.Leave) -> None:
        if self._dragging:
            return
        self.remove_class("grade-screen__resize-handle--dragging")

    def _resize(self, screen_x: float | None) -> None:
        if screen_x is None:
            return
        screen = self.parent
        while screen is not None and not isinstance(screen, GradeScreen):
            screen = screen.parent
        if isinstance(screen, GradeScreen):
            screen.resize_sidebar_to_screen_x(screen_x)


class GradeScreen(Vertical):
    SIDEBAR_MIN_WIDTH = 32
    SIDEBAR_MAX_WIDTH = 72
    RESPONSE_MIN_WIDTH = 28

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
            # Invisible drag target for resizing the grading sidebar.
            yield _SidebarResizeHandle()
            with VerticalScroll(id="grade-sidebar"):
                yield Label("", id="grade-student-name")
                yield Label("Score: 100.0", id="total-grade-label")
                yield Label("Progress: 0/0", id="grade-progress")
                yield Rule()
                yield Label("Question", classes="sidebar-section-title")
                yield Static("", id="question-context")
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
            self.query_one("#question-context", Static).update("")
            comment_input = self.query_one("#question-comment", Input)
            if comment_input.value:
                comment_input.value = ""
            return

        viewer = self.query_one("#response-viewer", ResponseViewer)
        viewer.set_image_path(current.image_path)
        self.query_one("#question-context", Static).update(
            self._question_context_text(current)
        )

        self._rubric_count = len(current.rubric_options)
        self._sync_rubric_options(current)

        comment_input = self.query_one("#question-comment", Input)
        if comment_input.value != current.comment:
            comment_input.value = current.comment

    def set_progress(self, text: str) -> None:
        self.query_one("#grade-progress", Label).update(text)

    def resize_sidebar_to_screen_x(self, screen_x: float) -> None:
        workspace = self.query_one("#grade-workspace", Horizontal)
        handle = self.query_one("#grade-sidebar-resize-handle", _SidebarResizeHandle)
        desired_width = (
            workspace.region.x + workspace.size.width - int(round(screen_x))
        )
        desired_width -= max(1, handle.size.width)
        self.set_sidebar_width(desired_width)

    def set_sidebar_width(self, width: int) -> None:
        sidebar = self.query_one("#grade-sidebar", VerticalScroll)
        sidebar.styles.width = self._bounded_sidebar_width(width)
        self.refresh(layout=True)

    def _bounded_sidebar_width(self, width: int) -> int:
        try:
            workspace = self.query_one("#grade-workspace", Horizontal)
            handle = self.query_one(
                "#grade-sidebar-resize-handle",
                _SidebarResizeHandle,
            )
            available = workspace.size.width - max(1, handle.size.width)
        except Exception:
            available = self.SIDEBAR_MAX_WIDTH + self.RESPONSE_MIN_WIDTH

        upper_bound = min(
            self.SIDEBAR_MAX_WIDTH,
            max(self.SIDEBAR_MIN_WIDTH, available - self.RESPONSE_MIN_WIDTH),
        )
        lower_bound = min(self.SIDEBAR_MIN_WIDTH, upper_bound)
        return max(lower_bound, min(int(width), upper_bound))

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

    @staticmethod
    def _question_context_text(current) -> str:
        parts = [
            f"{current.question_id} ({current.points:g} points)",
            "",
            current.prompt or "No prompt available.",
        ]
        if current.expected_answers:
            parts.extend(["", "Expected:", "\n".join(current.expected_answers)])
        if current.response_text:
            parts.extend(["", "Response text:", current.response_text])
        return "\n".join(parts)

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
