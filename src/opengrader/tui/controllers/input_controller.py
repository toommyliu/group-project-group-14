from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from textual.binding import Binding

from opengrader.tui.state import PAGE_GRADE, PAGE_QA_PICKER, TuiWorkflowState


class InputCommand(str, Enum):
    APP_QUIT = "app_quit"
    APP_BACK = "app_back"
    QA_MOVE_UP = "qa_move_up"
    QA_MOVE_DOWN = "qa_move_down"
    QA_MOVE_STUDENT_UP = "qa_move_student_up"
    QA_MOVE_STUDENT_DOWN = "qa_move_student_down"
    QA_SWITCH_PANE = "qa_switch_pane"
    QA_CONFIRM = "qa_confirm"
    QA_JUMP_UNGRADED = "qa_jump_ungraded_question"
    GRADE_NEXT_STUDENT = "grade_next_student"
    GRADE_PREVIOUS_STUDENT = "grade_previous_student"
    GRADE_NEXT_UNGRADED_STUDENT = "grade_next_ungraded_student"
    GRADE_PREVIOUS_UNGRADED_STUDENT = "grade_previous_ungraded_student"
    GRADE_FINALIZE_STUDENT = "grade_finalize_student"


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
        "tab",
        InputCommand.QA_SWITCH_PANE,
        "Switch Pane",
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
        "ctrl+g",
        InputCommand.GRADE_FINALIZE_STUDENT,
        "Finalize",
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
