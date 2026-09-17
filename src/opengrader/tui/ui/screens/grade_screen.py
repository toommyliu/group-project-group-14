from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import (
    Button,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
    TextArea,
)

from opengrader.core.qa_file.numeric import format_numeric_spec, parse_numeric_answer
from opengrader.core.qa_file.scoring import rubric_scoring_mode
from opengrader.core.question_types import NUMERIC
from opengrader.tui.data import RubricOptionSeed
from opengrader.tui.external_viewer import ExternalViewerError
from opengrader.tui.formatting import (
    format_confidence_chip,
    format_hotkey_reference,
    format_page_context_label,
    format_question_label,
)
from opengrader.tui.latex_render import render_response_latex_png, response_has_latex
from opengrader.tui.page_render import PageRenderOutcome, render_page_context_png
from opengrader.tui.state import QuestionGrade, StudentGradingRun
from opengrader.tui.ui.widgets.breadcrumb import Breadcrumb
from opengrader.tui.ui.widgets.sharp_image import SharpImage
from opengrader.tui.ui.widgets.response_viewer import ResponseViewer
from opengrader.tui.ui.widgets.shortcut_input import ShortcutInput

RubricEditorResult = list[RubricOptionSeed] | None
RubricEditorCallback = Callable[[RubricEditorResult], None]


def _format_numeric_expected(answer: str) -> str:
    spec = parse_numeric_answer(answer)
    return format_numeric_spec(spec) if spec is not None else answer


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
        self.marker.update(">" if selected else "")
        self.index_label.update(f"{index + 1}.")
        self.option_label.update(label)
        self.points_label.update(f"{points_delta:+.1f}")
        self.set_class(selected, "rubric-selected")


class _RubricEditRow(Vertical):
    def __init__(
        self,
        row_index: int,
        option: RubricOptionSeed,
        *,
        row_kind: str,
    ) -> None:
        self.row_index = row_index
        self.row_kind = row_kind
        self.index_label = Label(
            f"{row_index + 1}.",
            classes="rubric-edit-index",
        )
        self.label_input = ShortcutInput(
            value=(
                _partial_subitem_label(option.label)
                if row_kind == "partial"
                else _bonus_subitem_label(option.label)
                if row_kind == "bonus"
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
        self.comment_input = ShortcutInput(
            value=option.default_comment or "",
            placeholder="Auto-comment when picked (optional)…",
            classes="rubric-edit-comment",
            compact=True,
        )
        controls = Horizontal(
            self.index_label,
            self.label_input,
            self.delta_input,
            self.delete_button,
            classes="rubric-edit-controls",
        )
        super().__init__(
            controls,
            self.comment_input,
            classes="rubric-edit-row",
        )
        self._apply_required_state()

    @property
    def comment_value(self) -> str | None:
        return self.comment_input.value.strip() or None

    def _apply_required_state(self) -> None:
        if self.row_kind == "correct":
            self.label_input.disabled = True
            self.delete_button.disabled = True
            self.delete_button.display = False
            return
        if self.row_kind == "incorrect":
            self.label_input.disabled = True
            self.delete_button.disabled = True
            self.delete_button.display = False

    def option(self) -> RubricOptionSeed:
        label = self.label_input.value.strip()
        kind = "partial"
        selection_mode = "checklist"
        if self.row_kind == "partial":
            label = f"Partially Correct - {_partial_subitem_label(label)}"
        elif self.row_kind == "bonus":
            label = f"Bonus - {_bonus_subitem_label(label)}"
            kind = "bonus"
            selection_mode = "checklist"
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
            default_comment=self.comment_value,
        )


class _SidebarResizeHandle(Static):
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

    DEFAULT_CSS = """
    GradeScreen {
        padding: 0;
        margin: 0;
        width: 100%;
    }

    #grade-workspace {
        layout: horizontal;
        width: 100%;
        margin: 0;
        padding: 0;
    }

    #grade-workspace.-layout-top-bottom {
        layout: vertical;
    }

    .sidebar-quadrant {
        width: 100%;
        height: auto;
        background: transparent;
        border: round $surface-lighten-2;
        border-title-align: left;
        border-title-color: $text-muted;
        border-title-style: bold;
        padding: 0 1;
        margin-bottom: 1;
    }

    .sidebar-quadrant:focus-within {
        border: round $accent;
        border-title-color: $accent;
    }

    #grade-workspace.-layout-top-bottom > #grade-sidebar {
        layout: grid;
        grid-size: 2 2;
        grid-gutter: 0 1;
        width: 100%;
        min-width: 100%;
        max-width: 100%;
        margin: 0;
        height: 1fr;
        border-bottom: tall $surface-lighten-2;
        padding: 1 1 0 1;
        overflow-y: hidden;
    }

    #grade-workspace.-layout-top-bottom .sidebar-quadrant {
        height: 1fr;
        margin-bottom: 0;
    }

    #grade-workspace.-layout-top-bottom > #response-viewer {
        width: 1fr;
        height: 1fr;
    }

    #grade-workspace.-layout-top-bottom > #grade-sidebar-resize-handle {
        display: none;
    }
    #question-section-title {
        display: none;
    }

    #grade-answer-group {
        color: $accent;
        text-style: bold;
    }

    #question-confidence {
        margin-top: 1;
    }

    #question-math-toggle {
        margin-top: 1;
        display: none;
    }

    #question-page-toggle {
        margin-top: 1;
        display: none;
    }

    #question-page-indicator {
        margin-top: 1;
        display: none;
    }

    #question-open-external {
        margin-top: 1;
    }

    #transcription-toggle {
        margin-top: 1;
    }

    #transcription-editor {
        height: auto;
        display: none;
        margin-top: 1;
    }

    #transcription-editor.editing {
        display: block;
    }

    #transcription-text {
        height: 6;
        border: round $surface-lighten-2;
    }

    #transcription-actions {
        height: auto;
        margin-top: 1;
    }

    #db-status {
        color: $accent;
        text-style: bold;
        margin-top: 1;
    }

    #rubric-editor-panel {
        height: auto;
    }

    #rubric-editor-panel > Vertical {
        height: auto;
    }
    """

    # Legacy terminals cannot encode Ctrl+digit.
    BINDINGS = [
        Binding("a", "dispatch_input('grade_first_student')", "First Student", show=False),
        *(
            Binding(
                f"ctrl+{digit},alt+{digit}",
                f"rubric_finalize({(digit - 1) % 10})",
                show=False,
                priority=True,
            )
            for digit in (1, 2, 3, 4, 5, 6, 7, 8, 9, 0)
        ),
    ]

    # Shift+1 through Shift+0 on a US layout, using Textual's Unicode key names.
    _SHIFTED_DIGIT_FINALIZE_KEYS = {
        "exclamation_mark": 0,
        "at": 1,
        "number_sign": 2,
        "dollar_sign": 3,
        "percent_sign": 4,
        "circumflex_accent": 5,
        "ampersand": 6,
        "asterisk": 7,
        "left_parenthesis": 8,
        "right_parenthesis": 9,
    }

    class RubricSelected(Message):
        def __init__(self, option_index: int, finalize: bool = False) -> None:
            self.option_index = option_index
            self.finalize = finalize
            super().__init__()

    class CommentUpdated(Message):
        def __init__(self, comment: str) -> None:
            self.comment = comment
            super().__init__()

    class CommentCommitted(Message):
        pass

    class TranscriptionUpdated(Message):
        def __init__(self, response_text: str) -> None:
            self.response_text = response_text
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
        self._last_identity: tuple[int | None, str | None] = (None, None)
        self._sidebar_width: int | None = None
        self._sidebar_height: int | None = None
        self._pending_sidebar_width: int | None = None
        self._sidebar_resize_scheduled = False
        self._rubric_edit_callback = None
        self._rubric_edit_mode = False
        self._rubric_edit_points = 0.0
        self._zoom_fraction = 0.7
        self._zoom_at_limit = False
        self._rotation = 0
        self._math_view = False
        self._latex_cache_dir: Path | None = None
        self._page_view_index: int | None = None
        self._page_cache_dir: Path | None = None
        self._redact_identity = False
        self._page_redactions: dict[Path, bool] = {}
        self._current_grade: QuestionGrade | None = None
        super().__init__(id=id)

    def compose(self) -> ComposeResult:
        yield Breadcrumb(id="grade-breadcrumb")
        with Container(id="grade-workspace"):
            yield ResponseViewer(self._default_image_path, id="response-viewer")
            yield _SidebarResizeHandle()
            with VerticalScroll(id="grade-sidebar"):
                with Vertical(
                    id="sidebar-quadrant-info", classes="sidebar-quadrant"
                ) as info_card:
                    info_card.border_title = "Submission"
                    yield Label("", id="grade-student-name")
                    yield Label("", id="grade-source-pdf")
                    yield Label("", id="grade-claim-slice")
                    yield Label("Score: 100.0", id="total-grade-label")
                    yield Label("Progress: 0/0", id="grade-progress")
                    yield Label("", id="grade-answer-group")
                    yield Label("Zoom: 100%", id="grade-zoom-level")

                with VerticalScroll(
                    id="sidebar-quadrant-question", classes="sidebar-quadrant"
                ) as question_card:
                    question_card.border_title = "Question"
                    yield Label(
                        "Question",
                        id="question-section-title",
                        classes="sidebar-section-title",
                    )
                    yield Static("", id="question-context")
                    yield Label("", id="question-confidence")
                    yield Button(
                        "View rendered math ▸",
                        id="question-math-toggle",
                        compact=True,
                    )
                    yield Button(
                        "⛶ View whole page ▸",
                        id="question-page-toggle",
                        compact=True,
                    )
                    yield Label("", id="question-page-indicator")
                    yield Button(
                        "⧉ Open image in viewer",
                        id="question-open-external",
                        compact=True,
                    )
                    yield ShortcutInput(placeholder="Comment…", id="question-comment")
                    yield Button(
                        "✎ Fix transcription",
                        id="transcription-toggle",
                        compact=True,
                    )
                    with Vertical(id="transcription-editor"):
                        yield TextArea(id="transcription-text")
                        with Horizontal(id="transcription-actions"):
                            yield Button(
                                "Save", id="transcription-save", compact=True
                            )
                            yield Button(
                                "Cancel", id="transcription-cancel", compact=True
                            )

                with VerticalScroll(
                    id="sidebar-quadrant-rubric", classes="sidebar-quadrant"
                ) as rubric_card:
                    rubric_card.border_title = "Rubric"
                    yield ListView(id="rubric-list")
                    with Vertical(id="rubric-editor-panel"):
                        yield Static("", id="rubric-editor-status")
                        yield Label("Correct", id="rubric-edit-title-correct", classes="rubric-edit-section-title")
                        with Vertical(id="rubric-editor-correct"): pass
                        yield Label("Partial-credit subitems", id="rubric-edit-title-partial", classes="rubric-edit-section-title")
                        with Vertical(id="rubric-editor-subitems"): pass
                        yield Label("Incorrect", id="rubric-edit-title-incorrect", classes="rubric-edit-section-title")
                        with Vertical(id="rubric-editor-incorrect"): pass
                        yield Label("Bonus", id="rubric-edit-title-bonus", classes="rubric-edit-section-title")
                        with Vertical(id="rubric-editor-bonus"): pass
                        with Horizontal(id="rubric-editor-actions"):
                            yield Button("+ Subitem", id="rubric-edit-add", compact=True)
                            yield Button("+ Bonus", id="rubric-edit-add-bonus", compact=True)
                            yield Button("Save", id="rubric-edit-save", compact=True)
                            yield Button("Cancel", id="rubric-edit-cancel", compact=True)

                with VerticalScroll(
                    id="sidebar-quadrant-help", classes="sidebar-quadrant"
                ) as help_card:
                    help_card.border_title = "Hotkeys"
                    yield Static("", id="grade-keybinding-ref")
                    yield Static("", id="db-status")

    def on_mount(self) -> None:
        self._update_rubric_list_visibility()
        self._update_hotkey_ref()
        self._zoom_fraction = 0.7
        self._zoom_at_limit = False
        self._update_zoom_label()

    def bind_run(
        self,
        run: StudentGradingRun,
        breadcrumb_labels: list[str],
        *,
        preserve_view: bool = False,
    ) -> None:
        previous_comment = self._current_grade.comment if self._current_grade else ""
        rubric_index = self.query_one("#rubric-list", ListView).index
        self._redact_identity = bool(getattr(run, "redact_identity", False))
        self.query_one("#grade-breadcrumb", Breadcrumb).set_path(breadcrumb_labels)
        self.query_one("#grade-student-name", Label).update(
            f"{run.assignment_name}  -  {run.student_name}"
        )
        source_pdf_label = self.query_one("#grade-source-pdf", Label)
        source_pdf_label.update(f"PDF: {run.source_pdf_name}")
        source_pdf_label.display = bool(run.source_pdf_name)
        slice_label = self.query_one("#grade-claim-slice", Label)
        slice_label.update(f"Your slice: {run.claim_slice}" if run.claim_slice else "")
        slice_label.display = bool(run.claim_slice)
        self.query_one("#total-grade-label", Label).update(
            f"Score: {run.total_score:.1f} / {run.max_points:.1f}"
        )
        self.query_one("#total-grade-label", Label).set_class(  # type: ignore
            run.total_score < 70.0, "low-grade"
        )
        self.set_progress(f"Progress: {run.graded_count} / {run.total_questions}")

        current = run.current_question
        self._current_grade = current
        if run.student_id != self._last_identity[0]:
            self.query_one("#response-viewer", ResponseViewer).reset_rotation()
            self._rotation = 0
        identity = (run.student_id, current.question_id if current else None)
        if identity != self._last_identity:
            self._last_identity = identity
            self._zoom_fraction = 0.7
            self._zoom_at_limit = False
            self._math_view = False
            self._page_view_index = None
            self.query_one("#transcription-editor").remove_class("editing")
            self._update_zoom_label()
            self.query_one("#response-viewer", ResponseViewer).reset_zoom()

        if current is None:
            rubric_list = self.query_one("#rubric-list", ListView)
            for child in list(rubric_list.children):
                child.remove()
            self._rubric_count = 0
            self._set_question_title("Question")
            self.query_one("#question-context", Static).update("")
            confidence_label = self.query_one("#question-confidence", Label)
            confidence_label.update("")
            confidence_label.display = False
            self.query_one("#question-math-toggle", Button).display = False
            self.query_one("#question-page-toggle", Button).display = False
            self.query_one("#question-page-indicator", Label).display = False
            self._page_view_index = None
            comment_input = self.query_one("#question-comment", Input)
            if comment_input.value:
                comment_input.value = ""
            self.exit_rubric_edit_mode()
            return

        self._set_question_title(format_question_label(current.question_id))
        self.query_one("#question-context", Static).update(
            self._question_context_text(current)
        )
        self._update_confidence_chip(current)
        self._apply_response_view(current)

        self._rubric_count = len(current.rubric_options)
        if self._rubric_edit_mode:
            self._update_rubric_list_visibility()
        else:
            self._sync_rubric_options(current)
            if preserve_view:
                if rubric_index is not None and rubric_index < self._rubric_count:
                    self.query_one("#rubric-list", ListView).index = rubric_index
            else:
                self._restore_rubric_focus()

        comment_input = self.query_one("#question-comment", Input)
        if comment_input.value != current.comment and (
            self.app.focused is not comment_input
            or comment_input.value == previous_comment
        ):
            with comment_input.prevent(Input.Changed):
                comment_input.value = current.comment

    def _update_confidence_chip(self, current) -> None:
        label = self.query_one("#question-confidence", Label)
        chip = format_confidence_chip(
            getattr(current, "response_confidence", None),
            has_response=bool(current.response_text),
        )
        if chip is None:
            label.update("")
            label.display = False
        else:
            label.update(chip)
            label.display = True

    def _latex_png_path(self, question_id: str) -> Path:
        if self._latex_cache_dir is None:
            self._latex_cache_dir = Path(tempfile.mkdtemp(prefix="opengrader-latex-"))
        return self._latex_cache_dir / f"{question_id}.png"

    def _page_png_path(self, current, entry) -> Path:
        if self._page_cache_dir is None:
            self._page_cache_dir = Path(tempfile.mkdtemp(prefix="opengrader-page-"))
        stem = Path(current.source_pdf_path).stem or "submission"
        suffix = "-anon" if self._redact_identity else ""
        return (
            self._page_cache_dir
            / f"{stem}-{current.question_id}-p{entry.page_number}{suffix}.png"
        )

    @staticmethod
    def _page_context_of(current) -> tuple:
        if current is None or not getattr(current, "source_pdf_path", ""):
            return ()
        return tuple(getattr(current, "page_context", ()) or ())

    def _apply_response_view(self, current) -> None:
        viewer = self.query_one("#response-viewer", ResponseViewer)
        math_toggle = self.query_one("#question-math-toggle", Button)
        page_toggle = self.query_one("#question-page-toggle", Button)
        indicator = self.query_one("#question-page-indicator", Label)

        entries = self._page_context_of(current)
        page_toggle.display = bool(entries)
        if not entries:
            self._page_view_index = None

        if self._page_view_index is not None:
            index = max(0, min(self._page_view_index, len(entries) - 1))
            self._page_view_index = index
            entry = entries[index]
            png_path = self._page_png_path(current, entry)
            cached = self._page_redactions.get(png_path)
            if cached is not None and png_path.exists():
                outcome = PageRenderOutcome(True, redacted=cached)
            else:
                outcome = render_page_context_png(
                    current.source_pdf_path,
                    entry,
                    png_path,
                    redact_identity=self._redact_identity,
                )
                if outcome.ok:
                    self._page_redactions[png_path] = outcome.redacted
            if outcome.ok:
                viewer.set_image_path(str(png_path))
                page_toggle.label = "◂ View answer crop"
                indicator.update(
                    format_page_context_label(
                        entry, index, len(entries), redacted=outcome.redacted
                    )
                )
                indicator.display = True
                math_toggle.display = False
                return
            self._page_view_index = None
            self.set_status(outcome.reason)

        indicator.display = False
        page_toggle.label = "⛶ View whole page ▸"

        has_latex = response_has_latex(current.response_text)
        math_toggle.display = has_latex
        if not has_latex:
            self._math_view = False

        if self._math_view and has_latex:
            png_path = self._latex_png_path(current.question_id)
            if render_response_latex_png(current.response_text, png_path):
                viewer.set_image_path(str(png_path))
                math_toggle.label = "◂ View handwriting"
                return
            self._math_view = False

        viewer.set_image_path(current.image_path)
        math_toggle.label = "View rendered math ▸"

    def cycle_page_view(self, step: int = 1) -> None:
        current = self._current_grade
        entries = self._page_context_of(current)
        if not entries:
            self.set_status("No source page is available for this response.")
            return

        position = 0 if self._page_view_index is None else self._page_view_index + 1
        position = (position + step) % (len(entries) + 1)
        self._page_view_index = None if position == 0 else position - 1
        if self._page_view_index is not None:
            self._math_view = False
        self._apply_response_view(current)

    def toggle_page_view(self) -> None:
        current = self._current_grade
        entries = self._page_context_of(current)
        if not entries:
            self.set_status("No source page is available for this response.")
            return
        if self._page_view_index is None:
            self._page_view_index = 0
            self._math_view = False
        else:
            self._page_view_index = None
        self._apply_response_view(current)

    @on(Button.Pressed, "#question-page-toggle")
    def _on_page_toggle_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.toggle_page_view()

    @on(Button.Pressed, "#question-math-toggle")
    def _on_math_toggle_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        current = self._current_grade
        if current is None:
            return
        self._math_view = not self._math_view
        if self._math_view:
            self._page_view_index = None
        self._apply_response_view(current)

    def open_response_externally(self) -> None:
        viewer = self.query_one("#response-viewer", ResponseViewer)
        try:
            opened = viewer.open_externally()
        except ExternalViewerError as exc:
            self.set_status(f"Could not open image: {exc}")
            return
        self.set_status(f"Opened {opened} in your image viewer.")

    @on(Button.Pressed, "#question-open-external")
    def _on_open_external_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.open_response_externally()

    def transcription_editor_active(self) -> bool:
        return self.query_one("#transcription-editor").has_class("editing")

    def toggle_transcription_editor(self) -> None:
        if self.transcription_editor_active():
            self._close_transcription_editor()
        else:
            self._open_transcription_editor()

    def _open_transcription_editor(self) -> None:
        current = self._current_grade
        if current is None:
            return
        editor = self.query_one("#transcription-editor")
        text_area = self.query_one("#transcription-text", TextArea)
        text_area.text = current.response_text
        editor.add_class("editing")

        def focus_text() -> None:
            text_area.focus()

        self.call_next(focus_text)

    def _close_transcription_editor(self) -> None:
        self.query_one("#transcription-editor").remove_class("editing")
        self._focus_rubric_list()

    @on(Button.Pressed, "#transcription-toggle")
    def _on_transcription_toggle_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.toggle_transcription_editor()

    @on(Button.Pressed, "#transcription-save")
    def _on_transcription_save_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        text_area = self.query_one("#transcription-text", TextArea)
        self._close_transcription_editor()
        self.post_message(self.TranscriptionUpdated(text_area.text.strip()))

    @on(Button.Pressed, "#transcription-cancel")
    def _on_transcription_cancel_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self._close_transcription_editor()

    def set_progress(self, text: str) -> None:
        self.query_one("#grade-progress", Label).update(text)

    def set_answer_group(self, text: str) -> None:
        label = self.query_one("#grade-answer-group", Label)
        label.update(text)
        label.display = bool(text)

    def _set_question_title(self, title: str) -> None:
        self.query_one("#sidebar-quadrant-question").border_title = title
        self.query_one("#question-section-title", Label).update(title)

    def rubric_edit_active(self) -> bool:
        return self._rubric_edit_mode

    def zoom_in(self) -> None:
        if self._zoom_at_limit:
            self.set_status("Cannot zoom in: Image at rendering limit.")
            return
        self.query_one("#response-viewer", ResponseViewer).zoom_in()

    def zoom_out(self) -> None:
        self.set_status("")
        self.query_one("#response-viewer", ResponseViewer).zoom_out()

    def reset_zoom(self) -> None:
        self.query_one("#response-viewer", ResponseViewer).reset_zoom()

    def rotate_response(self, degrees: int) -> None:
        viewer = self.query_one("#response-viewer", ResponseViewer)
        viewer.rotate(degrees)
        self._rotation = viewer.rotation
        self._update_zoom_label()

    def toggle_layout(self) -> None:
        workspace = self.query_one("#grade-workspace", Container)
        sidebar = self.query_one("#grade-sidebar", VerticalScroll)
        viewer = self.query_one("#response-viewer")
        handle = self.query_one("#grade-sidebar-resize-handle")
        
        info = self.query_one("#sidebar-quadrant-info")
        question = self.query_one("#sidebar-quadrant-question")
        rubric = self.query_one("#sidebar-quadrant-rubric")
        help = self.query_one("#sidebar-quadrant-help")

        if workspace.has_class("-layout-top-bottom"):
            workspace.remove_class("-layout-top-bottom")
            workspace.move_child(viewer, before=0)
            workspace.move_child(handle, after=viewer)
            workspace.move_child(sidebar, after=handle)

            sidebar.move_child(info, before=0)
            sidebar.move_child(question, after=info)
            sidebar.move_child(rubric, after=question)
            sidebar.move_child(help, after=rubric)

            sidebar.styles.height = "1fr"
            sidebar.styles.width = self._sidebar_width if self._sidebar_width else 40
            sidebar.styles.max_width = None
        else:
            workspace.add_class("-layout-top-bottom")
            workspace.move_child(sidebar, before=0)
            workspace.move_child(handle, after=sidebar)
            workspace.move_child(viewer, after=handle)

            sidebar.move_child(info, before=0)
            sidebar.move_child(help, after=info)
            sidebar.move_child(question, after=help)
            sidebar.move_child(rubric, after=question)

            sidebar.styles.width = "100%"
            sidebar.styles.max_width = "100%"
            sidebar.styles.height = self._sidebar_height if self._sidebar_height else 30

        self._update_hotkey_ref()
        self._focus_rubric_list()
        self.refresh(layout=True)

    def focus_question_comment(self) -> None:
        if self._rubric_edit_mode:
            return
        comment_input = self.query_one("#question-comment", Input)

        def focus_comment() -> None:
            comment_input.focus()
            comment_input.cursor_position = len(comment_input.value)

        self.call_next(focus_comment)

    def current_comment_text(self) -> str:
        return self.query_one("#question-comment", Input).value

    def insert_comment_snippet(self, text: str) -> None:
        snippet = " ".join(text.split())
        if not snippet:
            return
        comment_input = self.query_one("#question-comment", Input)
        existing = comment_input.value.strip()
        comment_input.value = f"{existing} {snippet}".strip() if existing else snippet
        comment_input.cursor_position = len(comment_input.value)

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
        bonus_rows = self.query_one("#rubric-editor-bonus", Vertical)
        incorrect_rows = self.query_one("#rubric-editor-incorrect", Vertical)

        correct_option: tuple[int, RubricOptionSeed] | None = None
        incorrect_option: tuple[int, RubricOptionSeed] | None = None
        partial_options: list[tuple[int, RubricOptionSeed]] = []
        bonus_options: list[tuple[int, RubricOptionSeed]] = []
        for option_index, option in enumerate(question.rubric_options):
            seed = RubricOptionSeed(
                label=option.label,
                points_delta=option.points_delta,
                kind=option.kind,
                selection_mode=option.selection_mode,
                default_comment=option.default_comment,
            )
            if option.label == "Correct":
                correct_option = (option_index, seed)
            elif option.label == "Incorrect":
                incorrect_option = (option_index, seed)
            elif option.kind == "bonus":
                bonus_options.append((option_index, seed))
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

        for option_index, option in bonus_options:
            row = _RubricEditRow(
                len(bonus_rows.children),
                option,
                row_kind="bonus",
            )
            bonus_rows.mount(row)
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

        status = self.query_one("#rubric-editor-status", Static)
        status.update("")
        status.display = False
        self._renumber_rubric_edit_rows()
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
        if delta < 0 and self._zoom_at_limit:
            self.set_status("Cannot narrow sidebar: Image at rendering limit.")
            return
        if delta > 0:
            self.set_status("")
        self.set_sidebar_width(self._current_sidebar_width() + delta)

    def adjust_sidebar_height(self, delta: int) -> None:
        workspace = self.query_one("#grade-workspace", Container)
        if not workspace.has_class("-layout-top-bottom"):
            return

        if delta < 0 and self._zoom_at_limit:
            self.set_status("Cannot shrink sidebar: Image at rendering limit.")
            return
        if delta > 0:
            self.set_status("")

        sidebar = self.query_one("#grade-sidebar", VerticalScroll)
        current_height = self._sidebar_height if self._sidebar_height is not None else 30
        new_height = current_height + delta

        available_height = workspace.size.height
        if available_height > 0:
            max_height = min(42, max(10, available_height - 10))
        else:
            max_height = 42

        new_height = max(10, min(new_height, max_height))
        if new_height == self._sidebar_height:
            return

        self._sidebar_height = new_height
        sidebar.styles.height = new_height
        self._refresh_sidebar_layout(sidebar)

    def _sidebar_width_from_screen_x(self, screen_x: float) -> int:
        workspace = self.query_one("#grade-workspace", Container)
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
        if self._sidebar_width is not None:
            return self._sidebar_width

        sidebar = self.query_one("#grade-sidebar", VerticalScroll)
        measured_width = sidebar.size.width
        if measured_width > 0:
            return self._bounded_sidebar_width(measured_width)

        return self._bounded_sidebar_width(self.SIDEBAR_MIN_WIDTH)

    def _queue_sidebar_width(self, width: int) -> None:
        self._pending_sidebar_width = self._bounded_sidebar_width(width)
        if self._sidebar_resize_scheduled:
            return
        self._sidebar_resize_scheduled = True
        self.call_next(self._apply_pending_sidebar_width)

    def _apply_pending_sidebar_width(self) -> None:
        self._sidebar_resize_scheduled = False
        width = self._pending_sidebar_width
        self._pending_sidebar_width = None
        if width is None:
            return
        self.set_sidebar_width(width)

    def _refresh_sidebar_layout(self, sidebar: VerticalScroll) -> None:
        sidebar.refresh(layout=True)
        self.query_one("#grade-workspace", Container).refresh(layout=True)

    def _bounded_sidebar_width(self, width: int) -> int:
        try:
            workspace = self.query_one("#grade-workspace", Container)
            if workspace.has_class("-layout-top-bottom"):
                return width

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
        if self._rubric_edit_mode or self._rubric_count == 0:
            return
        rubric_list = self.query_one("#rubric-list", ListView)
        self.call_next(rubric_list.focus)

    def _restore_rubric_focus(self) -> None:
        focused = self.app.focused
        if isinstance(focused, (Input, TextArea)) and focused in self.query("*"):
            return
        self._focus_rubric_list()

    def _update_rubric_list_visibility(self) -> None:
        self.query_one("#rubric-list", ListView).display = not self._rubric_edit_mode
        self.query_one("#rubric-editor-panel", Vertical).display = (
            self._rubric_edit_mode
        )

    def _active_rubric_option_index(self) -> int | None:
        rubric_list = self.query_one("#rubric-list", ListView)
        index = rubric_list.index
        if index is None or not 0 <= index < self._rubric_count:
            return None
        return index

    def _first_editable_rubric_input(self) -> Input | None:
        for row in self.query(_RubricEditRow):
            if not row.label_input.disabled:
                return row.label_input
            if not row.delta_input.disabled:
                return row.delta_input
        return None

    @staticmethod
    def _rubric_edit_row_initial_focus(row: _RubricEditRow) -> Input | None:
        if not row.label_input.disabled:
            return row.label_input
        if not row.delta_input.disabled:
            return row.delta_input
        return None

    def _collect_rubric_edit_options(self) -> list[RubricOptionSeed] | str:
        options: list[RubricOptionSeed] = []
        partial_rows: list[_RubricEditRow] = []
        bonus_rows: list[_RubricEditRow] = []

        for row in self.query(_RubricEditRow):
            if row.row_kind == "partial":
                partial_rows.append(row)
                continue
            if row.row_kind == "bonus":
                bonus_rows.append(row)
                continue

            label = row.label_input.value.strip()
            if not label:
                return "Rubric labels cannot be blank."
            try:
                option = row.option()
            except ValueError:
                return f"{label}: points delta must be a number."
            options.append(option)

        partial_result = self._collect_partial_rubric_options(partial_rows)
        if isinstance(partial_result, str):
            return partial_result

        options[1:1] = partial_result
        options[len(options)-1 : len(options)-1] = [r.option() for r in bonus_rows]

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

        standard_options = [o for o in options if o.kind != "bonus"]
        try:
            mode = rubric_scoring_mode(option.points_delta for option in standard_options)
        except ValueError:
            return "Standard rubric items cannot mix positive awards and negative deductions."

        correct_delta = next(o.points_delta for o in options if o.label == "Correct")
        incorrect_delta = next(o.points_delta for o in options if o.label == "Incorrect")

        if mode == "positive_awards":
            if abs(correct_delta - self._rubric_edit_points) > 1e-6:
                return f"Correct must award +{self._rubric_edit_points:g} points."
            if abs(incorrect_delta) > 1e-6:
                return "Incorrect must award +0 points."
        elif abs(correct_delta) > 1e-6:
            return "Correct must use +0 points for deduction rubrics."
        elif abs(incorrect_delta + self._rubric_edit_points) > 1e-6:
            return f"Incorrect must deduct -{self._rubric_edit_points:g} points."

        exact_sum_error = self._validate_partial_exact_sum(standard_options, mode)
        if exact_sum_error:
            return exact_sum_error

        return options

    def _collect_partial_rubric_options(
        self,
        rows: list[_RubricEditRow],
    ) -> list[RubricOptionSeed] | str:
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
                    default_comment=rows[0].comment_value,
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
                    default_comment=row.comment_value,
                )
            )
        return options

    def _validate_partial_exact_sum(
        self,
        options: list[RubricOptionSeed],
        mode: str,
    ) -> str:
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
        result = self._collect_rubric_edit_options()
        if isinstance(result, str):
            status = self.query_one("#rubric-editor-status", Static)
            status.update(result)
            status.display = True
            return
        callback = self._rubric_edit_callback
        self.exit_rubric_edit_mode()
        if callable(callback):
            callback(result)

    def _cancel_rubric_edit(self) -> None:
        callback = self._rubric_edit_callback
        self.exit_rubric_edit_mode()
        if callable(callback):
            callback(None)

    def action_rubric_finalize(self, index: int | str) -> None:
        if self._rubric_edit_mode:
            # Finalizing during rubric edits could select a grade at a changed index.
            return
        idx = int(index)
        if 0 <= idx < self._rubric_count:
            self.post_message(self.RubricSelected(idx, finalize=True))

    def _rubric_editor_focus_chain(self) -> list[Widget]:
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
        awarded = f"{current.points_awarded:g}" if current.points_awarded is not None else "--"
        parts = [
            f"Score: {awarded} / {current.points:g} points",
            "",
            current.prompt or "No prompt available.",
        ]
        if current.expected_answers:
            label = "Expected"
            answers = list(current.expected_answers)
            if getattr(current, "question_type", None) == NUMERIC:
                label = "Expected (numeric)"
                answers = [_format_numeric_expected(answer) for answer in answers]
            parts.extend(["", f"{label}:", "\n".join(answers)])
        if current.response_text:
            parts.extend(["", "Response text:", current.response_text])
        return "\n".join(parts)

    def set_status(self, text: str) -> None:
        self.query_one("#db-status", Static).update(text)

    @on(Button.Pressed, "#rubric-edit-add")
    def _on_rubric_add_pressed(self, event: Button.Pressed) -> None:
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
        self._renumber_rubric_edit_rows()
        self.call_after_refresh(row.label_input.focus)

    @on(Button.Pressed, "#rubric-edit-add-bonus")
    def _on_rubric_add_bonus_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        rows = self.query_one("#rubric-editor-bonus", Vertical)
        row = _RubricEditRow(
            len(rows.children),
            RubricOptionSeed(
                "Bonus - Extra Credit",
                1.0,
                "bonus",
                "checklist",
            ),
            row_kind="bonus",
        )
        rows.mount(row)
        self._renumber_rubric_edit_rows()
        self.call_after_refresh(row.label_input.focus)

    def _current_editor_mode(self) -> str:
        options: list[float] = []
        for row in self.query(_RubricEditRow):
            if row.row_kind != "bonus":
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
        event.stop()
        self._save_rubric_edit()

    @on(Button.Pressed, "#rubric-edit-cancel")
    def _on_rubric_cancel_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self._cancel_rubric_edit()

    @on(Button.Pressed, ".rubric-edit-delete")
    def _on_rubric_delete_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        row = next(
            (a for a in event.button.ancestors if isinstance(a, _RubricEditRow)),
            None,
        )
        if row is not None:
            row.remove()
            self._renumber_rubric_edit_rows()

    def _renumber_rubric_edit_rows(self) -> None:
        current_index = 1
        for kind in ("correct", "partial", "bonus", "incorrect"):
            container_id = _container_id(kind)
            rows = list(self.query(f"#{container_id} _RubricEditRow"))
            has_rows = len(rows) > 0
            
            self.query_one(f"#rubric-edit-title-{kind}").display = has_rows
            self.query_one(f"#{container_id}").display = has_rows

            for row in rows:
                row.row_index = current_index - 1
                # Children may not have mounted yet.
                row.index_label.update(f"{current_index}.")
                current_index += 1


    def on_key(self, event: events.Key) -> None:
        key_name = event.name

        if event.key in ("up", "down", "left", "right", "home", "end", "pageup", "pagedown"):
            return


        if any(m in key_name for m in ("ctrl+", "alt+", "super+", "cmd+")):
            return

        if self._rubric_edit_mode:
            focused = self.app.focused
            if event.key in ("ctrl+s", "super+s", "cmd+s"):
                self._save_rubric_edit()
                event.prevent_default()
                event.stop()
            elif event.key == "escape":
                self._cancel_rubric_edit()
                event.prevent_default()
                event.stop()
            elif event.key == "tab":
                self._move_rubric_editor_focus(reverse=False)
                event.prevent_default()
                event.stop()
            elif event.key == "shift+tab":
                self._move_rubric_editor_focus(reverse=True)
                event.prevent_default()
                event.stop()
            elif event.key in ("enter", "space"):
                if self._press_focused_rubric_button():
                    event.prevent_default()
                    event.stop()
            elif isinstance(focused, Input):
                return

        focused = self.app.focused
        if isinstance(focused, Input) and focused.id == "question-comment":
            if event.key in ("enter", "escape"):
                self.post_message(self.CommentUpdated(focused.value))
                self.post_message(self.CommentCommitted())
                self._focus_rubric_list()
                event.prevent_default()
                event.stop()
                return

            if event.key == "up":
                self.query_one("#rubric-list", ListView).action_cursor_up()
                event.stop()
                return
            if event.key == "down":
                self.query_one("#rubric-list", ListView).action_cursor_down()
                event.stop()
                return
            if len(event.key) == 1 and event.key not in ("+", "=", "-", "_", "0", "[", "]"):
                event.stop()
            return

        if isinstance(focused, Input) and len(event.key) == 1:
            return

        if len(event.key) == 1 and event.key.isdigit() and event.key != "0":
            idx = int(event.key) - 1
            if 0 <= idx < self._rubric_count:
                event.stop()
                self.post_message(self.RubricSelected(idx))
                return

        shifted_idx = self._SHIFTED_DIGIT_FINALIZE_KEYS.get(event.key)
        if shifted_idx is not None and 0 <= shifted_idx < self._rubric_count:
            event.stop()
            self.action_rubric_finalize(shifted_idx)
            return


    def _clear_rubric_editor_rows(self) -> None:
        for container_id in (
            "#rubric-editor-correct",
            "#rubric-editor-subitems",
            "#rubric-editor-bonus",
            "#rubric-editor-incorrect",
        ):
            container = self.query_one(container_id, Vertical)
            for child in list(container.children):
                child.remove()

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
            self.post_message(self.CommentCommitted())

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "question-comment":
            self.post_message(self.CommentUpdated(event.value))

    def on_descendant_blur(self, event: events.DescendantBlur) -> None:
        widget = getattr(event, "widget", None)
        if getattr(widget, "id", None) == "question-comment":
            self.post_message(self.CommentCommitted())

    @on(ResponseViewer.ZoomChanged)
    def _on_zoom_changed(self, event: ResponseViewer.ZoomChanged) -> None:
        self._zoom_fraction = event.fraction
        self._update_zoom_label()

    @on(SharpImage.ScalingError)
    def _on_scaling_error(self, event: SharpImage.ScalingError) -> None:
        self._zoom_at_limit = event.at_limit
        self._update_zoom_label()

    def _update_zoom_label(self) -> None:
        label = self.query_one("#grade-zoom-level", Label)
        text = f"Zoom: {round(self._zoom_fraction * 100)}%"
        if self._zoom_at_limit:
            text += " (Max reached)"
        if self._rotation:
            text += f"  ·  Rotated {self._rotation}°"
        label.update(text)

    def _update_hotkey_ref(self) -> None:
        workspace = self.query_one("#grade-workspace", Container)
        top_bottom = workspace.has_class("-layout-top-bottom")
        layout_mode = "Top-bottom" if top_bottom else "Side-by-side"
        resize_label = "height" if top_bottom else "width"

        self.query_one("#grade-keybinding-ref", Static).update(
            format_hotkey_reference(
                [
                    ("j/k", "Next/prev question"),
                    ("ctrl+j/k", "Next/prev student"),
                    ("n/p", "Next/prev ungraded"),
                    ("a", "First student"),
                    ("shift+1-9", "Rubric + finalize"),
                    ("", "(or ctrl/alt+1-9)"),
                    ("b", "Grade by answer (mode)"),
                    ("g", "Grade answer group"),
                    ("v", "Confirm AI grades (mode)"),
                    ("y", "Confirm this AI grade"),
                    ("shift+U", "Unify similar answers"),
                    ("ctrl+g", "Finalize student"),
                    ("e", "Edit rubric"),
                    ("c", "Focus comment"),
                    ("x", "Fix transcription"),
                    ("l", "Comment library"),
                    ("z", "Undo last grade"),
                    ("+/-", "Zoom"),
                    (",/.", "Rotate view"),
                    ("w/shift+W", "Whole page / extra work"),
                    ("o", "Open image in viewer"),
                    ("[ ]", f"Sidebar {resize_label}"),
                    ("ctrl+v", f"Layout: {layout_mode}"),
                    ("", ""),
                    ("SHIFT+", "…opens a report:"),
                    ("R", "Needs-review queue"),
                    ("G", "Regrade requests"),
                    ("S", "Item analysis"),
                    ("C", "Consistency audit"),
                    ("P", "Similarity audit"),
                    ("I", "Grader agreement"),
                    ("H", "Grade history · teacher"),
                    ("V", "Grading sessions · teacher"),
                    ("F", "File a regrade · teacher"),
                    ("", ""),
                    ("esc", "Back to questions"),
                    ("?", "Help"),
                ],
                key_width=11,
            )
        )


def _partial_subitem_label(label: str) -> str:
    stripped = label.strip()
    prefix = "Partially Correct - "
    if stripped.startswith(prefix):
        stripped = stripped[len(prefix) :].strip()
    return stripped


def _bonus_subitem_label(label: str) -> str:
    stripped = label.strip()
    prefix = "Bonus - "
    if stripped.startswith(prefix):
        stripped = stripped[len(prefix) :].strip()
    return stripped


def _container_id(kind: str) -> str:
    mapping = {
        "correct": "rubric-editor-correct",
        "partial": "rubric-editor-subitems",
        "bonus": "rubric-editor-bonus",
        "incorrect": "rubric-editor-incorrect",
    }
    return mapping.get(kind, "")


def _render_points_delta(points_delta: float) -> str:
    rendered = f"{points_delta:g}"
    if not rendered.startswith("-"):
        rendered = f"+{rendered}"
    return rendered
