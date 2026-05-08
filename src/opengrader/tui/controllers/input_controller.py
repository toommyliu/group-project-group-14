from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from textual.binding import Binding

from opengrader.tui.state import PAGE_GRADE, PAGE_QA_PICKER, TuiWorkflowState


class InputCommand(str, Enum):
    APP_QUIT = "app_quit"
    APP_BACK = "app_back"
    QA_FILTER = "qa_filter"
    QA_MOVE_UP = "qa_move_up"
    QA_MOVE_DOWN = "qa_move_down"
    QA_MOVE_STUDENT_UP = "qa_move_student_up"
    QA_MOVE_STUDENT_DOWN = "qa_move_student_down"
    QA_SWITCH_PANE = "qa_switch_pane"
    QA_TOGGLE_STUDENT_DISPLAY = "qa_toggle_student_display"
    QA_CONFIRM = "qa_confirm"
    QA_JUMP_UNGRADED = "qa_jump_ungraded_question"
    GRADE_NEXT_STUDENT = "grade_next_student"
    GRADE_PREVIOUS_STUDENT = "grade_previous_student"
    GRADE_NEXT_UNGRADED_STUDENT = "grade_next_ungraded_student"
    GRADE_PREVIOUS_UNGRADED_STUDENT = "grade_previous_ungraded_student"
    GRADE_NEXT_QUESTION = "grade_next_question"
    GRADE_PREVIOUS_QUESTION = "grade_previous_question"
    GRADE_FINALIZE_STUDENT = "grade_finalize_student"
    GRADE_EDIT_RUBRIC = "grade_edit_rubric"
    GRADE_FOCUS_COMMENT = "grade_focus_comment"
    GRADE_SHRINK_SIDEBAR = "grade_shrink_sidebar"
    GRADE_WIDEN_SIDEBAR = "grade_widen_sidebar"
    GRADE_ZOOM_IN = "grade_zoom_in"
    GRADE_ZOOM_OUT = "grade_zoom_out"
    GRADE_RESET_ZOOM = "grade_reset_zoom"


class InputContext(str, Enum):
    QA = "qa"
    GRADE = "grade"
    GLOBAL = "global"


@dataclass(frozen=True)
class CommandBindingSpec:
    keys: str
    command: InputCommand
    description: str
    contexts: tuple[InputContext, ...]
    show: bool = True
    priority: bool = True
    key_display: str | None = None

    def to_binding(self) -> Binding:
        return Binding(
            self.keys,
            f"dispatch_input('{self.command.value}')",
            self.description,
            show=self.show,
            priority=self.priority,
            key_display=self.key_display,
        )


INPUT_BINDINGS: tuple[CommandBindingSpec, ...] = (
    CommandBindingSpec("q", InputCommand.APP_QUIT, "Quit", (InputContext.GLOBAL,)),
    CommandBindingSpec(
        "escape",
        InputCommand.APP_BACK,
        "Back",
        (InputContext.QA, InputContext.GRADE),
    ),
    CommandBindingSpec("/", InputCommand.QA_FILTER, "Filter", (InputContext.QA,)),
    # Duplicated bindings for both QA and Students so they
    # display properly in either pane
    CommandBindingSpec(
        "j,down",
        InputCommand.QA_MOVE_DOWN,
        "Next QA",
        (InputContext.QA,),
        key_display="j/down",
    ),
    CommandBindingSpec(
        "k,up",
        InputCommand.QA_MOVE_UP,
        "Prev QA",
        (InputContext.QA,),
        key_display="k/up",
    ),
    CommandBindingSpec(
        "j,down",
        InputCommand.QA_MOVE_STUDENT_DOWN,
        "Next Student",
        (InputContext.QA,),
        key_display="j/down",
    ),
    CommandBindingSpec(
        "k,up",
        InputCommand.QA_MOVE_STUDENT_UP,
        "Prev Student",
        (InputContext.QA,),
        key_display="k/up",
    ),
    CommandBindingSpec(
        "tab,shift+tab",
        InputCommand.QA_SWITCH_PANE,
        "Switch Pane",
        (InputContext.QA,),
        key_display="tab",
    ),
    CommandBindingSpec(
        "d",
        InputCommand.QA_TOGGLE_STUDENT_DISPLAY,
        "Display",
        (InputContext.QA,),
    ),
    CommandBindingSpec(
        "enter",
        InputCommand.QA_CONFIRM,
        "Grade QA",
        (InputContext.QA,),
    ),
    CommandBindingSpec(
        "u",
        InputCommand.QA_JUMP_UNGRADED,
        "Jump Ungraded",
        (InputContext.QA,),
    ),
    CommandBindingSpec(
        "j,right",
        InputCommand.GRADE_NEXT_STUDENT,
        "Next Student",
        (InputContext.GRADE,),
        key_display="j/right",
    ),
    CommandBindingSpec(
        "k,left",
        InputCommand.GRADE_PREVIOUS_STUDENT,
        "Prev Student",
        (InputContext.GRADE,),
        key_display="k/left",
    ),
    CommandBindingSpec(
        "n",
        InputCommand.GRADE_NEXT_UNGRADED_STUDENT,
        "Next Ungraded",
        (InputContext.GRADE,),
    ),
    CommandBindingSpec(
        "p",
        InputCommand.GRADE_PREVIOUS_UNGRADED_STUDENT,
        "Prev Ungraded",
        (InputContext.GRADE,),
    ),
    CommandBindingSpec(
        "ctrl+j",
        InputCommand.GRADE_NEXT_QUESTION,
        "Next Question",
        (InputContext.GRADE,),
    ),
    CommandBindingSpec(
        "ctrl+k",
        InputCommand.GRADE_PREVIOUS_QUESTION,
        "Prev Question",
        (InputContext.GRADE,),
    ),
    CommandBindingSpec(
        "e",
        InputCommand.GRADE_EDIT_RUBRIC,
        "Edit Rubric",
        (InputContext.GRADE,),
    ),
    CommandBindingSpec(
        "c",
        InputCommand.GRADE_FOCUS_COMMENT,
        "Comment",
        (InputContext.GRADE,),
    ),
    CommandBindingSpec(
        "[",
        InputCommand.GRADE_SHRINK_SIDEBAR,
        "Narrow Sidebar",
        (InputContext.GRADE,),
        show=False
    ),
    CommandBindingSpec(
        "]",
        InputCommand.GRADE_WIDEN_SIDEBAR,
        "Widen Sidebar",
        (InputContext.GRADE,),
        show=False
    ),
    CommandBindingSpec(
        "ctrl+g",
        InputCommand.GRADE_FINALIZE_STUDENT,
        "Finalize",
        (InputContext.GRADE,),
    ),
    CommandBindingSpec(
        "+,=",
        InputCommand.GRADE_ZOOM_IN,
        "Zoom In",
        (InputContext.GRADE,),
        key_display="+",
    ),
    CommandBindingSpec(
        "-,_",
        InputCommand.GRADE_ZOOM_OUT,
        "Zoom Out",
        (InputContext.GRADE,),
        key_display="-",
    ),
    CommandBindingSpec(
        "0",
        InputCommand.GRADE_RESET_ZOOM,
        "Reset Zoom",
        (InputContext.GRADE,),
    ),
)


class InputController:
    def __init__(self, state: TuiWorkflowState) -> None:
        self._state = state
        self._handlers: dict[InputCommand, Callable[[], None]] = {}

    @classmethod
    def all_bindings(cls) -> list[Binding]:
        return [binding.to_binding() for binding in INPUT_BINDINGS]

    def visible_bindings(self) -> list[CommandBindingSpec]:
        return [
            binding
            for binding in INPUT_BINDINGS
            if binding.show and self.command_available(binding.command)
        ]

    def register(
        self,
        command: InputCommand | str,
        handler: Callable[[], None],
    ) -> None:
        self._handlers[self._coerce_command(command)] = handler

    def dispatch(self, command: InputCommand | str) -> None:
        normalized_command = self._coerce_command(command)
        if not self.command_available(normalized_command):
            return
        handler = self._handlers.get(normalized_command)
        if handler is None:
            return
        handler()

    def command_available(self, command: InputCommand | str) -> bool:
        normalized_command = self._coerce_command(command)
        current_context = self.current_context()
        for binding in INPUT_BINDINGS:
            if binding.command != normalized_command:
                continue
            if InputContext.GLOBAL in binding.contexts:
                return True
            if current_context in binding.contexts:
                return True
        return False

    def current_context(self) -> InputContext:
        if self._state.current_page == PAGE_GRADE:
            return InputContext.GRADE
        if self._state.current_page == PAGE_QA_PICKER:
            return InputContext.QA
        return InputContext.GLOBAL

    @staticmethod
    def _coerce_command(command: InputCommand | str) -> InputCommand:
        if isinstance(command, InputCommand):
            return command
        try:
            return InputCommand(command)
        except ValueError as exc:
            raise ValueError(f"Unknown input command: {command}") from exc
