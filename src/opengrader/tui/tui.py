from pathlib import Path

from textual import events
from textual.app import App, ComposeResult
from textual.widgets import ContentSwitcher, Footer, Input

from opengrader.tui.controllers import GradeController, InputController
from opengrader.tui.controllers.input_controller import (
    InputCommand,
    InputProfileName,
)
from opengrader.tui.data import Provider
from opengrader.tui.data.stub_data_provider import StubDataProvider
from opengrader.tui.state import PAGE_GRADE, PAGE_QA_PICKER, TuiWorkflowState
from opengrader.tui.ui.screens import GradeScreen, QAPickerScreen


class TuiApp(App):
    CSS_PATH = Path(__file__).parent / "tui.tcss"
    COMMAND_PALETTE_BINDING = "ctrl+backslash"
    BINDINGS = InputController.all_bindings()  # pyright: ignore[reportAssignmentType]

    def __init__(
        self,
        *,
        default_image_path: str | None = None,
        data_provider: Provider | None = None,
        input_profile: InputProfileName | str | None = None,
    ) -> None:
        self._input_profile = InputController._coerce_profile_name(input_profile)
        super().__init__()
        self.default_image_path = default_image_path or str(Path("images/Grading.jpg"))
        self.workflow_state = TuiWorkflowState()
        self.data_provider = data_provider or StubDataProvider(self.default_image_path)
        self.grade_controller = GradeController(
            self,
            self.workflow_state,
            self.data_provider,
        )
        self.input_controller = InputController(
            self.workflow_state,
            profile_name=self._input_profile,
        )
        self._register_input_handlers()

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial=PAGE_QA_PICKER, id="workspace-switcher"):
            yield QAPickerScreen(id=PAGE_QA_PICKER)
            yield GradeScreen(self.default_image_path, id=PAGE_GRADE)
        yield Footer()

    def on_mount(self) -> None:
        self._load_demo_grading_queue()

    def switch_to(self, page_id: str) -> None:
        self.workflow_state.current_page = page_id
        self.query_one(ContentSwitcher).current = page_id
        self.refresh_bindings()

    def get_grade_screen(self) -> GradeScreen:
        return self.query_one(f"#{PAGE_GRADE}", GradeScreen)

    def get_qa_picker_screen(self) -> QAPickerScreen:
        return self.query_one(f"#{PAGE_QA_PICKER}", QAPickerScreen)

    def action_dispatch_input(self, profile_name: str, command: str) -> None:
        if not self.input_controller.binding_available(profile_name, command):
            return
        self.input_controller.dispatch(command)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action != "dispatch_input":
            return True

        if len(parameters) != 2:
            return False
        profile_name, command = parameters
        if not isinstance(profile_name, str) or not isinstance(command, str):
            return False

        if isinstance(self.focused, Input):
            return False

        if len(self.screen_stack) > 1:
            return False

        return self.input_controller.binding_available(profile_name, command)

    def on_key(self, event: events.Key) -> None:
        if self.workflow_state.current_page != PAGE_QA_PICKER:
            return
        qa_screen = self.get_qa_picker_screen()
        if event.key in ("j", "down"):
            if qa_screen.students_have_focus():
                qa_screen.move_student_cursor(1)
                event.stop()
                return
            self.grade_controller.qa_move_down()
            event.stop()
            return
        if event.key in ("k", "up"):
            if qa_screen.students_have_focus():
                qa_screen.move_student_cursor(-1)
                event.stop()
                return
            self.grade_controller.qa_move_up()
            event.stop()
            return
        if event.key == "enter":
            self.grade_controller.qa_confirm()
            event.stop()

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

    def on_qa_picker_screen_qa_selected(
        self, event: QAPickerScreen.QASelected
    ) -> None:
        self.grade_controller.on_qa_selected(event.item)

    def on_qa_picker_screen_q_a_selected(
        self, event: QAPickerScreen.QASelected
    ) -> None:
        self.grade_controller.on_qa_selected(event.item)

    def on_qapicker_screen_qaselected(
        self, event: QAPickerScreen.QASelected
    ) -> None:
        self.grade_controller.on_qa_selected(event.item)

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
            InputCommand.GRADE_FINALIZE_STUDENT,
            self.grade_controller.finalize_student,
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

    def _handle_qa_switch_pane(self) -> None:
        self.get_qa_picker_screen().switch_active_pane()

    def set_input_profile(self, profile_name: InputProfileName | str) -> None:
        self.input_controller.set_profile(profile_name)
        self.refresh_bindings()

    def _load_demo_grading_queue(self) -> None:
        selection = self.workflow_state.selection
        selection.course_item_id = "c1"
        selection.course_id = 1
        selection.assignment_item_id = "a1"
        selection.assignment_id = 1
        selection.question_id = None
        selection.student_item_id = None
        selection.student_id = None

        self.workflow_state.selection_labels.update(
            {
                "course": "CMPE 195",
                "assignment": "HW 1",
                "question": "",
                "student": "",
            }
        )
        self.grade_controller.open_question_picker_for_assignment()
