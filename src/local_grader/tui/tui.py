from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import ContentSwitcher, Footer

from local_grader.tui.controllers import (
    BrowseController,
    GradeController,
    InputController,
)
from local_grader.tui.controllers.input_controller import (
    InputCommand,
    InputProfileName,
)
from local_grader.tui.screens import (
    BrowseScreen,
    GradeScreen,
)
from local_grader.tui.services import SampleDataService
from local_grader.tui.state import PAGE_BROWSE, PAGE_GRADE, TuiWorkflowState
from local_grader.tui.widgets import MillerColumn


class TuiApp(App):
    CSS_PATH = Path(__file__).parent / "tui.tcss"
    COMMAND_PALETTE_BINDING = "ctrl+backslash"
    BINDINGS = InputController.all_bindings()

    def __init__(
        self,
        *,
        default_image_path: str | None = None,
        data_service: SampleDataService | None = None,
        input_profile: InputProfileName | str | None = None,
    ) -> None:
        self._input_profile = InputController._coerce_profile_name(input_profile)
        super().__init__()
        self.default_image_path = default_image_path or str(Path("images/Grading.jpg"))
        self.workflow_state = TuiWorkflowState()
        self.data_service = data_service or SampleDataService(self.default_image_path)
        self.grade_controller = GradeController(
            self,
            self.workflow_state,
            self.data_service,
        )
        self.browse_controller = BrowseController(
            self,
            self.workflow_state,
            self.data_service,
            self.grade_controller.open_student_by_index,
        )
        self.grade_controller.attach_student_refresh(
            self.browse_controller.refresh_students
        )
        self.input_controller = InputController(
            self.workflow_state,
            profile_name=self._input_profile,
        )
        self._register_input_handlers()

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial=PAGE_BROWSE, id="workspace-switcher"):
            yield BrowseScreen(id=PAGE_BROWSE)
            yield GradeScreen(self.default_image_path, id=PAGE_GRADE)
        yield Footer()

    def on_mount(self) -> None:
        self.browse_controller.initialize()
        self._refresh_keymap_labels()

    def switch_to(self, page_id: str) -> None:
        self.workflow_state.current_page = page_id
        self.query_one(ContentSwitcher).current = page_id
        self.refresh_bindings()

    def get_browse_screen(self) -> BrowseScreen:
        return self.query_one(f"#{PAGE_BROWSE}", BrowseScreen)

    def get_grade_screen(self) -> GradeScreen:
        return self.query_one(f"#{PAGE_GRADE}", GradeScreen)

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
        return self.input_controller.binding_available(profile_name, command)

    def on_miller_column_item_selected(self, event: MillerColumn.ItemSelected) -> None:
        self.browse_controller.handle_item_selected(event.column_id, event.item)

    def on_grade_screen_question_selected(
        self, event: GradeScreen.QuestionSelected
    ) -> None:
        self.grade_controller.on_question_selected(event.question_index)

    def on_grade_screen_rubric_selected(
        self, event: GradeScreen.RubricSelected
    ) -> None:
        self.grade_controller.on_rubric_selected(event.option_index)

    def on_grade_screen_comment_updated(
        self, event: GradeScreen.CommentUpdated
    ) -> None:
        self.grade_controller.on_comment_updated(event.comment)

    def on_grade_screen_return_to_browse_requested(
        self, _event: GradeScreen.ReturnToBrowseRequested
    ) -> None:
        self.grade_controller.return_to_browse()

    def _register_input_handlers(self) -> None:
        self.input_controller.register(InputCommand.APP_QUIT, self.exit)
        self.input_controller.register(InputCommand.APP_BACK, self._handle_back)
        self.input_controller.register(
            InputCommand.BROWSE_MOVE_UP,
            self.browse_controller.move_up,
        )
        self.input_controller.register(
            InputCommand.BROWSE_MOVE_DOWN,
            self.browse_controller.move_down,
        )
        self.input_controller.register(
            InputCommand.BROWSE_MOVE_LEFT,
            self.browse_controller.move_left,
        )
        self.input_controller.register(
            InputCommand.BROWSE_MOVE_RIGHT,
            self.browse_controller.move_right,
        )
        self.input_controller.register(
            InputCommand.BROWSE_CONFIRM,
            self.browse_controller.confirm,
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
            InputCommand.GRADE_NEXT_STUDENT,
            self.grade_controller.next_student,
        )
        self.input_controller.register(
            InputCommand.GRADE_PREVIOUS_STUDENT,
            self.grade_controller.previous_student,
        )
        self.input_controller.register(
            InputCommand.GRADE_FINALIZE_STUDENT,
            self.grade_controller.finalize_student,
        )

    def _handle_back(self) -> None:
        if self.workflow_state.current_page == PAGE_GRADE:
            self.grade_controller.return_to_browse()

    def set_input_profile(self, profile_name: InputProfileName | str) -> None:
        self.input_controller.set_profile(profile_name)
        self._refresh_keymap_labels()
        self.refresh_bindings()

    def _refresh_keymap_labels(self) -> None:
        profile_name = self.input_controller.profile_name().value
        self.get_browse_screen().set_keymap_label(profile_name)
        self.get_grade_screen().set_keymap_label(profile_name)
