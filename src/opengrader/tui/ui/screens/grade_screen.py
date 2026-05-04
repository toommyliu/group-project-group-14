from __future__ import annotations

from collections.abc import Callable

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import (
    Button,
    Input,
    Label,
    ListItem,
    ListView,
    Rule,
    Static,
)

from opengrader.core.qa_file.scoring import rubric_scoring_mode
from opengrader.tui.data import RubricOptionSeed
from opengrader.tui.state import QuestionGrade, StudentGradingRun
from opengrader.tui.ui.widgets.breadcrumb import Breadcrumb
from opengrader.tui.ui.widgets.response_viewer import ResponseViewer
from opengrader.tui.ui.widgets.shortcut_input import ShortcutInput

RubricEditorResult = list[RubricOptionSeed] | None
RubricEditorCallback = Callable[[RubricEditorResult], None]


class _RubricListItem(ListItem):
    def __init__(self) -> None:
        self.marker = Static("", classes="rubric-row-marker", markup=False)
        self.index_label = Static("", classes="rubric-row-index", markup=False)
        self.option_label = Static("", classes="rubric-row-label", markup=False)
        self.points_label = Static("", classes="rubric-row-points", markup=False)
        self.row = Horizontal(
            self.marker,
            self.index_label,
            self.option_label,
            self.points_label,
            classes="rubric-row",
        )
        super().__init__(self.row)

    def update_option(
        self,
        index: int,
        label: str,
        points_delta: float,
        selected: bool,
    ) -> None:
        """Refresh the visible rubric text and selected styling."""
        self.marker.update(">" if selected else "")
        self.index_label.update(f"{index + 1}.")
        self.option_label.update(label)
        self.points_label.update(f"{points_delta:+.1f}")
        self.set_class(selected, "rubric-selected")


class _RubricEditRow(Horizontal):
    def __init__(
        self,
        row_index: int,
        option: RubricOptionSeed,
        *,
        row_kind: str,
    ) -> None:
        self.row_index = row_index
        self.row_kind = row_kind
        self.index_label = Label(f"{row_index + 1}.", classes="rubric-edit-index")
        self.label_input = ShortcutInput(
            value=(
                _partial_subitem_label(option.label)
                if row_kind == "partial"
                else option.label
            ),
            classes="rubric-edit-label",
            compact=True,
        )
        self.delta_input = ShortcutInput(
            value=_render_points_delta(option.points_delta),
            classes="rubric-edit-delta",
            compact=True,
        )
        self.delete_button = Button(
            "x",
            classes="rubric-edit-delete",
            compact=True,
        )
        super().__init__(
            self.index_label,
            self.label_input,
            self.delta_input,
            self.delete_button,
            classes="rubric-edit-row",
        )
        self._apply_required_state()

    def _apply_required_state(self) -> None:
        """Disable editing controls for schema-owned rubric rows."""
        if self.row_kind == "correct":
            self.index_label.update("")
            self.label_input.disabled = True
            self.delete_button.disabled = True
            self.delete_button.display = False
            return
        if self.row_kind == "incorrect":
            self.index_label.update("")
            self.label_input.disabled = True
            self.delete_button.disabled = True
            self.delete_button.display = False

    def option(self) -> RubricOptionSeed:
        """Return this editor row as a provider-ready rubric option."""
        label = self.label_input.value.strip()
        kind = "partial"
        selection_mode = "checklist"
        if self.row_kind == "partial":
            label = f"Partially Correct - {_partial_subitem_label(label)}"
        elif self.row_kind == "correct":
            kind = "correct"
            selection_mode = "exclusive"
        elif self.row_kind == "incorrect":
            kind = "incorrect"
            selection_mode = "exclusive"
        return RubricOptionSeed(
            label=label,
            points_delta=float(self.delta_input.value.strip()),
            kind=kind,
            selection_mode=selection_mode,
        )


class _SidebarResizeHandle(Static):
    # Don't allow text selection on the invisible resize handle.
    ALLOW_SELECT = False

    def __init__(self) -> None:
        super().__init__(
            " ",
            id="grade-sidebar-resize-handle",
            classes="grade-screen__resize-handle",
        )
        self._dragging = False
        self._screen: GradeScreen | None = None

    def on_mouse_down(self, event: events.MouseDown) -> None:
        """Start dragging resize handle on left click."""
        if event.button != 1:
            return
        self._dragging = True
        self.capture_mouse()
        self.add_class("grade-screen__resize-handle--dragging")
        self._resize(event.screen_x, immediate=True)
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._dragging:
            return
        self._resize(event.screen_x, immediate=False)
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if not self._dragging:
            return
        self._dragging = False
        self.release_mouse()
        self.remove_class("grade-screen__resize-handle--dragging")
        self._resize(event.screen_x, immediate=True)
        event.stop()

    def on_leave(self, _event: events.Leave) -> None:
        if self._dragging:
            return
        self.remove_class("grade-screen__resize-handle--dragging")

    def _resize(self, screen_x: float | None, *, immediate: bool) -> None:
        """Apply or queue a sidebar resize from a screen x-coordinate."""
        if screen_x is None:
            return
        screen = self._grade_screen()
        if screen is None:
            return
        if immediate:
            screen.resize_sidebar_to_screen_x(screen_x)
            return
        screen.queue_sidebar_resize_to_screen_x(screen_x)

    def _grade_screen(self) -> GradeScreen | None:
        if self._screen is not None:
            return self._screen
        screen = self.parent
        while screen is not None and not isinstance(screen, GradeScreen):
            screen = screen.parent
        if isinstance(screen, GradeScreen):
            self._screen = screen
            return screen
        return None


class GradeScreen(Vertical):
    SIDEBAR_MIN_WIDTH = 32
    SIDEBAR_MAX_WIDTH = 72
    RESPONSE_MIN_WIDTH = 28
    SIDEBAR_RESIZE_STEP = 4

    class RubricSelected(Message):
        def __init__(self, option_index: int) -> None:
            self.option_index = option_index
            super().__init__()

    class CommentUpdated(Message):
        def __init__(self, comment: str) -> None:
            self.comment = comment
            super().__init__()

    class RubricEditRequested(Message):
        pass

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
        # Keep track of states to avoid frequent reflows during sidebar resizing.
        self._sidebar_width: int | None = None
        self._pending_sidebar_width: int | None = None
        self._sidebar_resize_scheduled = False
        self._rubric_edit_callback = None
        self._rubric_edit_mode = False
        self._rubric_edit_points = 0.0
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
                with Vertical(id="rubric-editor-panel"):
                    yield Static("", id="rubric-editor-status")
                    yield Label("Correct", classes="rubric-edit-section-title")
                    with Vertical(id="rubric-editor-correct"):
                        pass
                    yield Label(
                        "Partial-credit subitems",
                        classes="rubric-edit-section-title",
                    )
                    with Vertical(id="rubric-editor-subitems"):
                        pass
                    with Horizontal(id="rubric-subitem-actions"):
                        yield Button(
                            "+ Subitem",
                            id="rubric-edit-add",
                            compact=True,
                        )
                    yield Label("Incorrect", classes="rubric-edit-section-title")
                    with Vertical(id="rubric-editor-incorrect"):
                        pass
                    with Horizontal(id="rubric-editor-actions"):
                        yield Button(
                            "Save",
                            id="rubric-edit-save",
                            compact=True,
                        )
                        yield Button(
                            "Cancel",
                            id="rubric-edit-cancel",
                            compact=True,
                        )
                yield ShortcutInput(
                    placeholder="Comment for this question...",
                    id="question-comment",
                )
                yield Rule()
                yield Static(
                    "ctrl+j/k  Next/Prev Question\n"
                    "j/k       Next/Prev Student\n"
                    "n/p       Next/Prev Ungraded\n"
                    "ctrl+g    Finalize\n"
                    "e         Edit Rubric\n"
                    "c         Focus Comment",
                    id="grade-keybinding-ref",
                )
                yield Static("", id="db-status")

    def on_mount(self) -> None:
        self._update_rubric_list_visibility()

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
            self.exit_rubric_edit_mode()
            return

        viewer = self.query_one("#response-viewer", ResponseViewer)
        viewer.set_image_path(current.image_path)
        self.query_one("#question-context", Static).update(
            self._question_context_text(current)
        )

        self._rubric_count = len(current.rubric_options)
        if self._rubric_edit_mode:
            self._update_rubric_list_visibility()
        else:
            self._sync_rubric_options(current)
            self._focus_rubric_list()

        comment_input = self.query_one("#question-comment", Input)
        if comment_input.value != current.comment:
            comment_input.value = current.comment

    def set_progress(self, text: str) -> None:
        self.query_one("#grade-progress", Label).update(text)

    def rubric_edit_active(self) -> bool:
        return self._rubric_edit_mode

    def focus_question_comment(self) -> None:
        """Move keyboard focus to the question comment input."""
        if self._rubric_edit_mode:
            return
        comment_input = self.query_one("#question-comment", Input)

        def focus_comment() -> None:
            comment_input.focus()
            comment_input.cursor_position = len(comment_input.value)

        self.call_next(focus_comment)

    def present_rubric_editor(
        self,
        app: object,
        question: QuestionGrade,
        callback: RubricEditorCallback,
    ) -> None:
        _ = app
        initial_option_index = self._active_rubric_option_index()
        self._rubric_edit_callback = callback
        self._rubric_edit_mode = True
        self._rubric_edit_points = question.points
        self._clear_rubric_editor_rows()
        correct_rows = self.query_one("#rubric-editor-correct", Vertical)
        partial_rows = self.query_one("#rubric-editor-subitems", Vertical)
        incorrect_rows = self.query_one("#rubric-editor-incorrect", Vertical)

        correct_option: tuple[int, RubricOptionSeed] | None = None
        incorrect_option: tuple[int, RubricOptionSeed] | None = None
        partial_options: list[tuple[int, RubricOptionSeed]] = []
        for option_index, option in enumerate(question.rubric_options):
            seed = RubricOptionSeed(
                label=option.label,
                points_delta=option.points_delta,
                kind=option.kind,
                selection_mode=option.selection_mode,
            )
            if option.label == "Correct":
                correct_option = (option_index, seed)
            elif option.label == "Incorrect":
                incorrect_option = (option_index, seed)
            else:
                partial_options.append((option_index, seed))

        focus_target_by_option_index: dict[int, Input] = {}

        correct_index = correct_option[0] if correct_option is not None else None
        correct_row = _RubricEditRow(
            0,
            (
                correct_option[1]
                if correct_option is not None
                else RubricOptionSeed("Correct", 0.0, "correct", "exclusive")
            ),
            row_kind="correct",
        )
        correct_rows.mount(correct_row)
        if correct_index is not None:
            target = self._rubric_edit_row_initial_focus(correct_row)
            if target is not None:
                focus_target_by_option_index[correct_index] = target

        for option_index, option in partial_options:
            row = _RubricEditRow(
                len(partial_rows.children),
                option,
                row_kind="partial",
            )
            partial_rows.mount(row)
            target = self._rubric_edit_row_initial_focus(row)
            if target is not None:
                focus_target_by_option_index[option_index] = target

        incorrect_index = incorrect_option[0] if incorrect_option is not None else None
        incorrect_row = _RubricEditRow(
            0,
            (
                incorrect_option[1]
                if incorrect_option is not None
                else RubricOptionSeed(
                    "Incorrect",
                    -question.points,
                    "incorrect",
                    "exclusive",
                )
            ),
            row_kind="incorrect",
        )
        incorrect_rows.mount(incorrect_row)
        if incorrect_index is not None:
            target = self._rubric_edit_row_initial_focus(incorrect_row)
            if target is not None:
                focus_target_by_option_index[incorrect_index] = target

        self.query_one("#rubric-editor-status", Static).update(
            ""
        )
        self._update_rubric_list_visibility()
        target = (
            focus_target_by_option_index.get(initial_option_index)
            if initial_option_index is not None
            else None
        )
        if target is None:
            target = self._first_editable_rubric_input()
        if target is not None:
            self.call_next(target.focus)

    def exit_rubric_edit_mode(self) -> None:
        self._rubric_edit_callback = None
        self._rubric_edit_mode = False
        self._update_rubric_list_visibility()
        self._clear_rubric_editor_rows()
        self._focus_rubric_list()

    def resize_sidebar_to_screen_x(self, screen_x: float) -> None:
        self._pending_sidebar_width = None
        self.set_sidebar_width(self._sidebar_width_from_screen_x(screen_x))

    def queue_sidebar_resize_to_screen_x(self, screen_x: float) -> None:
        self._queue_sidebar_width(self._sidebar_width_from_screen_x(screen_x))

    def adjust_sidebar_width(self, delta: int) -> None:
        self.set_sidebar_width(self._current_sidebar_width() + delta)

    def _sidebar_width_from_screen_x(self, screen_x: float) -> int:
        """Convert a resize-handle x-coordinate into sidebar width."""
        workspace = self.query_one("#grade-workspace", Horizontal)
        handle = self.query_one("#grade-sidebar-resize-handle", _SidebarResizeHandle)
        desired_width = (
            workspace.region.x + workspace.size.width - int(round(screen_x))
        )
        desired_width -= max(1, round(handle.size.width / 2))
        return desired_width

    def set_sidebar_width(self, width: int) -> None:
        width = self._bounded_sidebar_width(width)
        if width == self._sidebar_width:
            return

        sidebar = self.query_one("#grade-sidebar", VerticalScroll)
        sidebar.styles.width = width
        self._sidebar_width = width
        self._refresh_sidebar_layout(sidebar)

    def _current_sidebar_width(self) -> int:
        """Return the tracked or measured sidebar width."""
        if self._sidebar_width is not None:
            return self._sidebar_width

        sidebar = self.query_one("#grade-sidebar", VerticalScroll)
        measured_width = sidebar.size.width
        if measured_width > 0:
            return self._bounded_sidebar_width(measured_width)

        return self._bounded_sidebar_width(self.SIDEBAR_MIN_WIDTH)

    def _queue_sidebar_width(self, width: int) -> None:
        """Coalesce sidebar width updates during mouse dragging."""
        self._pending_sidebar_width = self._bounded_sidebar_width(width)
        if self._sidebar_resize_scheduled:
            return
        self._sidebar_resize_scheduled = True
        self.call_next(self._apply_pending_sidebar_width)

    def _apply_pending_sidebar_width(self) -> None:
        """Apply the latest queued sidebar width."""
        self._sidebar_resize_scheduled = False
        width = self._pending_sidebar_width
        self._pending_sidebar_width = None
        if width is None:
            return
        self.set_sidebar_width(width)

    def _refresh_sidebar_layout(self, sidebar: VerticalScroll) -> None:
        """Refresh widgets affected by a sidebar width change."""
        sidebar.refresh(layout=True)
        self.query_one("#grade-workspace", Horizontal).refresh(layout=True)

    def _bounded_sidebar_width(self, width: int) -> int:
        """Clamp sidebar width to keep the response pane usable."""
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
        """Mirror the current question rubric into the sidebar list."""
        rubric_list = self.query_one("#rubric-list", ListView)
        option_count = len(current.rubric_options)

        while len(rubric_list.children) < option_count:
            rubric_list.append(_RubricListItem())

        for extra_child in list(rubric_list.children[option_count:]):
            extra_child.remove()

        for option_index, option in enumerate(current.rubric_options):
            selected = option_index in current.selected_option_indices
            li = rubric_list.children[option_index]
            if not isinstance(li, _RubricListItem):
                continue
            li.update_option(
                option_index,
                option.label,
                option.points_delta,
                selected,
            )

        if option_count:
            selected = sorted(current.selected_option_indices)
            rubric_list.index = selected[0] if selected else 0

    def _focus_rubric_list(self) -> None:
        """Return keyboard focus to the rubric list when grading."""
        if self._rubric_edit_mode or self._rubric_count == 0:
            return
        rubric_list = self.query_one("#rubric-list", ListView)
        self.call_next(rubric_list.focus)

    def _update_rubric_list_visibility(self) -> None:
        """Toggle between rubric selection and rubric editing views."""
        self.query_one("#rubric-list", ListView).display = not self._rubric_edit_mode
        self.query_one("#rubric-editor-panel", Vertical).display = (
            self._rubric_edit_mode
        )

    def _active_rubric_option_index(self) -> int | None:
        """Return the currently highlighted rubric option index, if valid."""
        rubric_list = self.query_one("#rubric-list", ListView)
        index = rubric_list.index
        if index is None or not 0 <= index < self._rubric_count:
            return None
        return index

    def _first_editable_rubric_input(self) -> Input | None:
        """Find the first editable rubric input for initial focus."""
        for row in self.query(_RubricEditRow):
            if not row.label_input.disabled:
                return row.label_input
            if not row.delta_input.disabled:
                return row.delta_input
        return None

    @staticmethod
    def _rubric_edit_row_initial_focus(row: _RubricEditRow) -> Input | None:
        """Return the first enabled input in an editor row."""
        if not row.label_input.disabled:
            return row.label_input
        if not row.delta_input.disabled:
            return row.delta_input
        return None

    def _collect_rubric_edit_options(self) -> list[RubricOptionSeed] | str:
        """Validate editor rows and return options or an error message."""
        options: list[RubricOptionSeed] = []
        partial_rows: list[_RubricEditRow] = []
        for row in self.query(_RubricEditRow):
            label = (
                _partial_subitem_label(row.label_input.value)
                if row.row_kind == "partial"
                else row.label_input.value.strip()
            )
            if not label:
                return "Rubric labels cannot be blank."
            if row.row_kind == "partial":
                partial_rows.append(row)
                continue
            try:
                option = row.option()
            except ValueError:
                return f"{label}: points delta must be a number."
            options.append(option)

        partial_result = self._collect_partial_rubric_options(partial_rows)
        if isinstance(partial_result, str):
            return partial_result
        options[1:1] = partial_result

        labels = [option.label for option in options]
        if len(set(labels)) != len(labels):
            return "Rubric labels must be unique."
        correct_options = [option for option in options if option.label == "Correct"]
        if len(correct_options) != 1:
            return "Correct must be present exactly once."
        if sum(1 for option in options if option.label == "Incorrect") != 1:
            return "Incorrect must be present exactly once."
        if not [
            option for option in options if option.label not in {"Correct", "Incorrect"}
        ]:
            return "At least one partial-credit option is required."
        try:
            mode = rubric_scoring_mode(option.points_delta for option in options)
        except ValueError:
            return "Use either positive awards or negative deductions, not both."
        correct_delta = correct_options[0].points_delta
        incorrect_options = [option for option in options if option.label == "Incorrect"]
        incorrect_delta = incorrect_options[0].points_delta
        if mode == "positive_awards":
            if abs(correct_delta - self._rubric_edit_points) > 1e-6:
                return f"Correct must award +{self._rubric_edit_points:g} points."
            if abs(incorrect_delta) > 1e-6:
                return "Incorrect must award +0 points."
        elif abs(correct_delta) > 1e-6:
            return "Correct must use +0 points for deduction rubrics."
        elif abs(incorrect_delta + self._rubric_edit_points) > 1e-6:
            return f"Incorrect must deduct -{self._rubric_edit_points:g} points."
        exact_sum_error = self._validate_partial_exact_sum(options, mode)
        if exact_sum_error:
            return exact_sum_error
        return options

    def _collect_partial_rubric_options(
        self,
        rows: list[_RubricEditRow],
    ) -> list[RubricOptionSeed] | str:
        """Collect partial rows as either one direct shortcut or checklist items."""
        if not rows:
            return []
        if len(rows) == 1 and rows[0].label_input.value.strip() == "Partially Correct":
            try:
                points_delta = float(rows[0].delta_input.value.strip())
            except ValueError:
                return "Partially Correct: points delta must be a number."
            return [
                RubricOptionSeed(
                    "Partially Correct",
                    points_delta,
                    "partial",
                    "exclusive",
                )
            ]

        options: list[RubricOptionSeed] = []
        for row in rows:
            try:
                points_delta = float(row.delta_input.value.strip())
            except ValueError:
                label = _partial_subitem_label(row.label_input.value)
                return f"{label}: points delta must be a number."
            options.append(
                RubricOptionSeed(
                    f"Partially Correct - {_partial_subitem_label(row.label_input.value)}",
                    points_delta,
                    "partial",
                    "checklist",
                )
            )
        return options

    def _validate_partial_exact_sum(
        self,
        options: list[RubricOptionSeed],
        mode: str,
    ) -> str:
        """Validate that checklist partial rows cover the whole question value."""
        checklist_partials = [
            option
            for option in options
            if option.kind == "partial" and option.selection_mode == "checklist"
        ]
        if not checklist_partials:
            return ""
        total = sum(option.points_delta for option in checklist_partials)
        target = (
            self._rubric_edit_points
            if mode == "positive_awards"
            else -self._rubric_edit_points
        )
        if abs(total - target) <= 1e-6:
            return ""
        return f"Checklist partial items must sum to {target:+g} points."

    def _save_rubric_edit(self) -> None:
        """Validate and submit rubric editor changes."""
        result = self._collect_rubric_edit_options()
        if isinstance(result, str):
            self.query_one("#rubric-editor-status", Static).update(result)
            return
        callback = self._rubric_edit_callback
        self.exit_rubric_edit_mode()
        if callable(callback):
            callback(result)

    def _cancel_rubric_edit(self) -> None:
        """Exit rubric editing without saving changes."""
        callback = self._rubric_edit_callback
        self.exit_rubric_edit_mode()
        if callable(callback):
            callback(None)

    def _rubric_editor_focus_chain(self) -> list[Widget]:
        """Return enabled focus targets inside the rubric editor."""
        editor = self.query_one("#rubric-editor-panel", Vertical)
        return [
            widget
            for widget in editor.query("Input, Button")
            if isinstance(widget, Widget)
            and widget.can_focus
            and not widget.disabled
            and widget.display
        ]

    def _move_rubric_editor_focus(self, *, reverse: bool) -> None:
        """Cycle focus within the rubric editor."""
        focus_chain = self._rubric_editor_focus_chain()
        if not focus_chain:
            return

        current_focus = self.app.focused
        if current_focus in focus_chain:
            current_index = focus_chain.index(current_focus)
            next_index = (current_index - 1 if reverse else current_index + 1) % len(
                focus_chain
            )
        else:
            next_index = len(focus_chain) - 1 if reverse else 0

        focus_chain[next_index].focus()

    def _press_focused_rubric_button(self) -> bool:
        """Press the focused rubric editor button if one is focused."""
        focused = self.app.focused
        editor = self.query_one("#rubric-editor-panel", Vertical)
        if not isinstance(focused, Button):
            return False
        if editor not in focused.ancestors:
            return False
        if focused.disabled or not focused.display:
            return False
        focused.press()
        return True

    @staticmethod
    def _question_context_text(current) -> str:
        """Render the current question details shown above the rubric."""
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

    @on(Button.Pressed, "#rubric-edit-add")
    def _on_rubric_add_pressed(self, event: Button.Pressed) -> None:
        """Add a new partial-credit subitem row."""
        event.stop()
        rows = self.query_one("#rubric-editor-subitems", Vertical)
        default_delta = (
            1.0
            if self._current_editor_mode() == "positive_awards"
            else -1.0
        )
        row = _RubricEditRow(
            len(rows.children),
            RubricOptionSeed(
                "Partially Correct - New subitem",
                default_delta,
                "partial",
                "checklist",
            ),
            row_kind="partial",
        )
        rows.mount(row)
        self.call_next(row.label_input.focus)

    def _current_editor_mode(self) -> str:
        """Return the current rubric editor scoring mode."""
        options: list[float] = []
        for row in self.query(_RubricEditRow):
            try:
                options.append(float(row.delta_input.value.strip()))
            except ValueError:
                continue
        try:
            return rubric_scoring_mode(options)
        except ValueError:
            return "negative_deductions"

    @on(Button.Pressed, "#rubric-edit-save")
    def _on_rubric_save_pressed(self, event: Button.Pressed) -> None:
        """Handle the rubric editor Save button."""
        event.stop()
        self._save_rubric_edit()

    @on(Button.Pressed, "#rubric-edit-cancel")
    def _on_rubric_cancel_pressed(self, event: Button.Pressed) -> None:
        """Handle the rubric editor Cancel button."""
        event.stop()
        self._cancel_rubric_edit()

    @on(Button.Pressed, ".rubric-edit-delete")
    def _on_rubric_delete_pressed(self, event: Button.Pressed) -> None:
        """Remove a partial-credit subitem row."""
        event.stop()
        row = event.button.parent
        if isinstance(row, _RubricEditRow):
            row.remove()
            self._renumber_rubric_edit_rows()

    def _renumber_rubric_edit_rows(self) -> None:
        """Refresh partial-credit row numbers after deletion."""
        rows = [
            row
            for row in self.query(_RubricEditRow)
            if row.row_kind == "partial"
        ]
        for index, row in enumerate(rows):
            row.row_index = index
            label = row.query_one(".rubric-edit-index", Label)
            label.update(f"{index + 1}.")

    def _clear_rubric_editor_rows(self) -> None:
        """Remove all mounted rubric editor rows."""
        for container_id in (
            "#rubric-editor-correct",
            "#rubric-editor-subitems",
            "#rubric-editor-incorrect",
        ):
            container = self.query_one(container_id, Vertical)
            for child in list(container.children):
                child.remove()

    def on_key(self, event: events.Key) -> None:
        if self._rubric_edit_mode:
            focused = self.app.focused
            if event.key in ("ctrl+s", "super+s", "cmd+s"):
                self._save_rubric_edit()
                event.prevent_default()
            elif event.key == "escape":
                self._cancel_rubric_edit()
                event.prevent_default()
            elif event.key == "tab":
                self._move_rubric_editor_focus(reverse=False)
                event.prevent_default()
            elif event.key == "shift+tab":
                self._move_rubric_editor_focus(reverse=True)
                event.prevent_default()
            elif event.key in ("enter", "space"):
                if self._press_focused_rubric_button():
                    event.prevent_default()
            elif isinstance(focused, Input):
                return
            event.stop()
            return

        focused = self.app.focused
        if isinstance(focused, Input) and focused.id == "question-comment":
            if event.key in ("enter", "escape"):
                self.post_message(self.CommentUpdated(focused.value))
                self._focus_rubric_list()
                event.prevent_default()
                event.stop()
                return
            if len(event.key) == 1:
                event.stop()
            return

        if isinstance(focused, Input):
            return

        # 1-9 to select the corresponding rubric item.
        if len(event.key) == 1 and event.key.isdigit() and event.key != "0":
            option_index = int(event.key) - 1
            if 0 <= option_index < self._rubric_count:
                event.stop()
                event.prevent_default()
                self.post_message(self.RubricSelected(option_index))

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


def _render_points_delta(points_delta: float) -> str:
    """Render a signed points delta for rubric inputs."""
    rendered = f"{points_delta:g}"
    if not rendered.startswith("-"):
        rendered = f"+{rendered}"
    return rendered


def _partial_subitem_label(label: str) -> str:
    """Strip the partial-credit prefix from a flat rubric label."""
    stripped = label.strip()
    prefix = "Partially Correct - "
    if stripped.startswith(prefix):
        stripped = stripped[len(prefix) :].strip()
    return stripped
