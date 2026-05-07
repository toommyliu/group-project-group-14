from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import ContentSwitcher, Input

from opengrader.tui.controllers import GradeController, InputController
from opengrader.tui.controllers.input_controller import CommandBindingSpec, InputCommand
from opengrader.tui.data import Provider
from opengrader.tui.data.submission_files_provider import SubmissionFilesProvider
from opengrader.tui.data.stub_data_provider import StubDataProvider
from opengrader.tui.state import PAGE_GRADE, PAGE_QA_PICKER, TuiWorkflowState
from opengrader.tui.ui.screens import GradeScreen, QAPickerScreen
from opengrader.tui.ui.widgets import StatusBar, StatusBarBinding


class TuiApp(App):
    CSS_PATH = Path(__file__).parent / "tui.tcss"
    COMMAND_PALETTE_BINDING = "ctrl+backslash"
    BINDINGS = InputController.all_bindings()  # pyright: ignore[reportAssignmentType]

    def __init__(
        self,
        *,
        default_image_path: str | None = None,
        data_provider: Provider | None = None,
        qa_file_path: str | Path | None = None,
        submissions_path: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.default_image_path = default_image_path or str(Path("images/Grading.jpg"))
        self.workflow_state = TuiWorkflowState()
        self.data_provider = data_provider or self._build_data_provider(
            qa_file_path=qa_file_path,
            submissions_path=submissions_path,
        )
        self.grade_controller = GradeController(
            self,
            self.workflow_state,
            self.data_provider,
        )
        self.input_controller = InputController(self.workflow_state)
        self._register_input_handlers()

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial=PAGE_QA_PICKER, id="workspace-switcher"):
            yield QAPickerScreen(id=PAGE_QA_PICKER)
            yield GradeScreen(self.default_image_path, id=PAGE_GRADE)
        yield StatusBar(id="app-status-bar")

    def on_mount(self) -> None:
        self._load_grading_queue()
        self.refresh_bindings()

    def switch_to(self, page_id: str) -> None:
        self.workflow_state.current_page = page_id
        self.query_one(ContentSwitcher).current = page_id
        self.refresh_bindings()

    def get_grade_screen(self) -> GradeScreen:
        return self.query_one(f"#{PAGE_GRADE}", GradeScreen)

    def get_qa_picker_screen(self) -> QAPickerScreen:
        return self.query_one(f"#{PAGE_QA_PICKER}", QAPickerScreen)

    def action_dispatch_input(self, command: str) -> None:
        if not self._input_command_available(command):
            return
        self.input_controller.dispatch(command)

    def refresh_bindings(self) -> None:
        super().refresh_bindings()
        self._refresh_status_bar_bindings()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action != "dispatch_input":
            return True

        if len(parameters) != 1:
            return False
        command = parameters[0]
        if not isinstance(command, str):
            return False

        if isinstance(self.focused, Input):
            return False

        if len(self.screen_stack) > 1:
            return False

        return self._input_command_available(command)

    def on_qa_picker_screen_qa_highlighted(
        self, event: QAPickerScreen.QAHighlighted
    ) -> None:
        self.grade_controller.on_qa_highlighted(event.item)

    def on_qa_picker_screen_q_a_highlighted(
        self, event: QAPickerScreen.QAHighlighted
    ) -> None:
        self.grade_controller.on_qa_highlighted(event.item)

    def on_qapicker_screen_qahighlighted(
        self, event: QAPickerScreen.QAHighlighted
    ) -> None:
        self.grade_controller.on_qa_highlighted(event.item)

    def on_qa_picker_screen_qa_selected(self, event: QAPickerScreen.QASelected) -> None:
        self.grade_controller.on_qa_selected(event.item)

    def on_qa_picker_screen_q_a_selected(
        self, event: QAPickerScreen.QASelected
    ) -> None:
        self.grade_controller.on_qa_selected(event.item)

    def on_qapicker_screen_qaselected(self, event: QAPickerScreen.QASelected) -> None:
        self.grade_controller.on_qa_selected(event.item)

    def on_status_bar_search_changed(self, event: StatusBar.SearchChanged) -> None:
        if self.workflow_state.current_page != PAGE_QA_PICKER:
            return
        status = self.get_qa_picker_screen().update_filter(event.value)
        self.query_one(StatusBar).update_search_status(status)

    def on_status_bar_search_submitted(
        self, _event: StatusBar.SearchSubmitted
    ) -> None:
        self._finish_qa_filter()

    def on_status_bar_search_cancelled(
        self, _event: StatusBar.SearchCancelled
    ) -> None:
        self._finish_qa_filter()

    def on_grade_screen_rubric_selected(
        self, event: GradeScreen.RubricSelected
    ) -> None:
        self.grade_controller.on_rubric_selected(event.option_index)

    def on_grade_screen_comment_updated(
        self, event: GradeScreen.CommentUpdated
    ) -> None:
        self.grade_controller.on_comment_updated(event.comment)

    def on_grade_screen_return_to_home_requested(
        self, _event: GradeScreen.ReturnToHomeRequested
    ) -> None:
        self.grade_controller.return_to_qa_picker()

    def _register_input_handlers(self) -> None:
        self.input_controller.register(InputCommand.APP_QUIT, self.exit)
        self.input_controller.register(InputCommand.APP_BACK, self._handle_back)
        self.input_controller.register(InputCommand.QA_FILTER, self._handle_qa_filter)
        self.input_controller.register(
            InputCommand.QA_JUMP_UNGRADED,
            self.grade_controller.qa_jump_ungraded_question,
        )
        self.input_controller.register(
            InputCommand.QA_MOVE_UP,
            self._handle_qa_move_up,
        )
        self.input_controller.register(
            InputCommand.QA_MOVE_DOWN,
            self._handle_qa_move_down,
        )
        self.input_controller.register(
            InputCommand.QA_MOVE_STUDENT_UP,
            self._handle_student_move_up,
        )
        self.input_controller.register(
            InputCommand.QA_MOVE_STUDENT_DOWN,
            self._handle_student_move_down,
        )
        self.input_controller.register(
            InputCommand.QA_SWITCH_PANE,
            self._handle_qa_switch_pane,
        )
        self.input_controller.register(
            InputCommand.QA_CONFIRM,
            self.grade_controller.qa_confirm,
        )
        self.input_controller.register(
            InputCommand.GRADE_NEXT_STUDENT,
            self.grade_controller.next_student,
        )
        self.input_controller.register(
            InputCommand.GRADE_PREVIOUS_STUDENT,
            self.grade_controller.previous_student,
        )
        self.input_controller.register(
            InputCommand.GRADE_NEXT_UNGRADED_STUDENT,
            self.grade_controller.next_ungraded_student,
        )
        self.input_controller.register(
            InputCommand.GRADE_PREVIOUS_UNGRADED_STUDENT,
            self.grade_controller.previous_ungraded_student,
        )
        self.input_controller.register(
            InputCommand.GRADE_NEXT_QUESTION,
            self.grade_controller.next_question,
        )
        self.input_controller.register(
            InputCommand.GRADE_PREVIOUS_QUESTION,
            self.grade_controller.previous_question,
        )
        self.input_controller.register(
            InputCommand.GRADE_FINALIZE_STUDENT,
            self.grade_controller.finalize_student,
        )
        self.input_controller.register(
            InputCommand.GRADE_EDIT_RUBRIC,
            self.grade_controller.open_rubric_editor,
        )
        self.input_controller.register(
            InputCommand.GRADE_FOCUS_COMMENT,
            lambda: self.get_grade_screen().focus_question_comment(),
        )
        self.input_controller.register(
            InputCommand.GRADE_SHRINK_SIDEBAR,
            self._handle_grade_shrink_sidebar,
        )
        self.input_controller.register(
            InputCommand.GRADE_WIDEN_SIDEBAR,
            self._handle_grade_widen_sidebar,
        )

    def _handle_back(self) -> None:
        if self.workflow_state.current_page == PAGE_GRADE:
            self.grade_controller.return_to_qa_picker()
            return
        if self.workflow_state.current_page == PAGE_QA_PICKER:
            self.get_qa_picker_screen().set_status("Already viewing the grading queue.")

    def _handle_qa_move_down(self) -> None:
        qa_screen = self.get_qa_picker_screen()
        if qa_screen.students_have_focus():
            qa_screen.move_student_cursor(1)
            return
        self.grade_controller.qa_move_down()

    def _handle_qa_move_up(self) -> None:
        qa_screen = self.get_qa_picker_screen()
        if qa_screen.students_have_focus():
            qa_screen.move_student_cursor(-1)
            return
        self.grade_controller.qa_move_up()

    def _handle_student_move_down(self) -> None:
        self.get_qa_picker_screen().move_student_cursor(1)

    def _handle_student_move_up(self) -> None:
        self.get_qa_picker_screen().move_student_cursor(-1)

    def _handle_qa_switch_pane(self) -> None:
        self.get_qa_picker_screen().switch_active_pane()
        self.refresh_bindings()

    def _handle_qa_filter(self) -> None:
        qa_screen = self.get_qa_picker_screen()
        prompt, value = qa_screen.start_filter()
        status = qa_screen.update_filter(value)
        self.query_one(StatusBar).enter_search(
            prompt=prompt,
            value=value,
            status=status,
        )
        self.refresh_bindings()

    def _finish_qa_filter(self) -> None:
        self.query_one(StatusBar).exit_search()
        if self.workflow_state.current_page == PAGE_QA_PICKER:
            self.get_qa_picker_screen().finish_filter()
        self.call_after_refresh(self.refresh_bindings)

    def _handle_grade_shrink_sidebar(self) -> None:
        grade_screen = self.get_grade_screen()
        grade_screen.adjust_sidebar_width(-grade_screen.SIDEBAR_RESIZE_STEP)

    def _handle_grade_widen_sidebar(self) -> None:
        grade_screen = self.get_grade_screen()
        grade_screen.adjust_sidebar_width(grade_screen.SIDEBAR_RESIZE_STEP)

    def _input_command_available(self, command: InputCommand | str) -> bool:
        if isinstance(self.focused, Input):
            return False

        if not self.input_controller.command_available(command):
            return False

        normalized_command = InputController._coerce_command(command)

        # Disable all input commands when editing the rubric and
        # prioritize GradeScreen on_key.
        if (
            self.workflow_state.current_page == PAGE_GRADE
            and self.get_grade_screen().rubric_edit_active()
        ):
            return False

        if self.workflow_state.current_page != PAGE_QA_PICKER:
            return True

        if normalized_command in (
            InputCommand.QA_MOVE_STUDENT_DOWN,
            InputCommand.QA_MOVE_STUDENT_UP,
        ):
            return self.get_qa_picker_screen().students_have_focus()

        if normalized_command in (InputCommand.QA_MOVE_DOWN, InputCommand.QA_MOVE_UP):
            return not self.get_qa_picker_screen().students_have_focus()

        return True

    def _refresh_status_bar_bindings(self) -> None:
        try:
            status_bar = self.query_one(StatusBar)
        except Exception:
            return
        if status_bar.search_active():
            return

        visible_bindings = [
            self._status_bar_binding(binding)
            for binding in self.input_controller.visible_bindings()
            if self._input_command_available(binding.command)
        ]
        status_bar.show_bindings(visible_bindings)

    @staticmethod
    def _status_bar_binding(binding: CommandBindingSpec) -> StatusBarBinding:
        key = binding.key_display or binding.keys
        if key == "enter":
            key = "↩"
        if key == "escape":
            key = "esc"
        return StatusBarBinding(key=key, description=binding.description)

    def _build_data_provider(
        self,
        *,
        qa_file_path: str | Path | None,
        submissions_path: str | Path | None,
    ) -> Provider:
        if qa_file_path is not None and submissions_path is not None:
            return SubmissionFilesProvider(
                qa_file_path=qa_file_path,
                submissions_path=submissions_path,
                default_image_path=self.default_image_path,
            )
        return StubDataProvider(self.default_image_path)

    def _load_grading_queue(self) -> None:
        selection = self.workflow_state.selection
        selection.course_item_id = "c1"
        selection.course_id = 1
        selection.assignment_item_id = "a1"
        selection.assignment_id = 1
        selection.question_id = None
        selection.student_item_id = None
        selection.student_id = None

        course_label = getattr(self.data_provider, "course_label", "CMPE 195")
        assignment_label = getattr(self.data_provider, "assignment_label", "HW 1")
        self.workflow_state.selection_labels.update(
            {
                "course": str(course_label or "Course"),
                "assignment": str(assignment_label or "Assignment"),
                "question": "",
                "student": "",
            }
        )
        self.grade_controller.open_question_picker_for_assignment()
