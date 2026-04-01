from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from textual.binding import Binding

from local_grader.tui.state import PAGE_BROWSE, PAGE_GRADE, TuiWorkflowState


class InputCommand(str, Enum):
    APP_QUIT = "app_quit"
    APP_BACK = "app_back"
    BROWSE_MOVE_UP = "browse_move_up"
    BROWSE_MOVE_DOWN = "browse_move_down"
    BROWSE_MOVE_LEFT = "browse_move_left"
    BROWSE_MOVE_RIGHT = "browse_move_right"
    BROWSE_CONFIRM = "browse_confirm"
    GRADE_NEXT_QUESTION = "grade_next_question"
    GRADE_PREVIOUS_QUESTION = "grade_previous_question"
    GRADE_NEXT_STUDENT = "grade_next_student"
    GRADE_PREVIOUS_STUDENT = "grade_previous_student"
    GRADE_FINALIZE_STUDENT = "grade_finalize_student"


class InputContext(str, Enum):
    BROWSE = "browse"
    GRADE = "grade"
    GLOBAL = "global"


class InputProfileName(str, Enum):
    STANDARD = "standard"
    VIM = "vim"
    EMACS = "emacs"


DEFAULT_INPUT_PROFILE = InputProfileName.STANDARD


def current_default_input_profile() -> InputProfileName:
    return DEFAULT_INPUT_PROFILE


@dataclass(frozen=True)
class CommandBindingSpec:
    keys: str
    command: InputCommand
    description: str
    contexts: tuple[InputContext, ...]
    show: bool = True
    priority: bool = True
    key_display: str | None = None

    def to_binding(self, profile_name: InputProfileName) -> Binding:
        return Binding(
            self.keys,
            f"dispatch_input('{profile_name.value}','{self.command.value}')",
            self.description,
            show=self.show,
            priority=self.priority,
            key_display=self.key_display,
        )


@dataclass(frozen=True)
class InputProfile:
    name: InputProfileName
    bindings: tuple[CommandBindingSpec, ...]


STANDARD_PROFILE = InputProfile(
    name=InputProfileName.STANDARD,
    bindings=(
        CommandBindingSpec(
            "q",
            InputCommand.APP_QUIT,
            "Quit",
            (InputContext.GLOBAL,),
            show=True,
        ),
        CommandBindingSpec(
            "escape",
            InputCommand.APP_BACK,
            "Back",
            (InputContext.GLOBAL,),
            show=True,
        ),
        CommandBindingSpec(
            "up",
            InputCommand.BROWSE_MOVE_UP,
            "Browse Up",
            (InputContext.BROWSE,),
        ),
        CommandBindingSpec(
            "down",
            InputCommand.BROWSE_MOVE_DOWN,
            "Browse Down",
            (InputContext.BROWSE,),
        ),
        CommandBindingSpec(
            "left",
            InputCommand.BROWSE_MOVE_LEFT,
            "Browse Left",
            (InputContext.BROWSE,),
        ),
        CommandBindingSpec(
            "right",
            InputCommand.BROWSE_MOVE_RIGHT,
            "Browse Right",
            (InputContext.BROWSE,),
        ),
        CommandBindingSpec(
            "enter",
            InputCommand.BROWSE_CONFIRM,
            "Select",
            (InputContext.BROWSE,),
        ),
        CommandBindingSpec(
            "ctrl+j",
            InputCommand.GRADE_NEXT_QUESTION,
            "Next Q",
            (InputContext.GRADE,),
        ),
        CommandBindingSpec(
            "ctrl+k",
            InputCommand.GRADE_PREVIOUS_QUESTION,
            "Prev Q",
            (InputContext.GRADE,),
        ),
        CommandBindingSpec(
            "ctrl+right_square_bracket",
            InputCommand.GRADE_NEXT_STUDENT,
            "Next Student",
            (InputContext.GRADE,),
        ),
        CommandBindingSpec(
            "ctrl+left_square_bracket",
            InputCommand.GRADE_PREVIOUS_STUDENT,
            "Prev Student",
            (InputContext.GRADE,),
        ),
        CommandBindingSpec(
            "ctrl+g",
            InputCommand.GRADE_FINALIZE_STUDENT,
            "Finalize",
            (InputContext.GRADE,),
        ),
    ),
)

VIM_PROFILE = InputProfile(
    name=InputProfileName.VIM,
    bindings=(
        CommandBindingSpec(
            "q",
            InputCommand.APP_QUIT,
            "Quit",
            (InputContext.GLOBAL,),
            show=True,
        ),
        CommandBindingSpec(
            "escape",
            InputCommand.APP_BACK,
            "Back",
            (InputContext.GLOBAL,),
            show=True,
        ),
        CommandBindingSpec(
            "k",
            InputCommand.BROWSE_MOVE_UP,
            "Browse Up",
            (InputContext.BROWSE,),
            key_display="k/up",
        ),
        CommandBindingSpec(
            "up",
            InputCommand.BROWSE_MOVE_UP,
            "Browse Up",
            (InputContext.BROWSE,),
            show=False,
        ),
        CommandBindingSpec(
            "j",
            InputCommand.BROWSE_MOVE_DOWN,
            "Browse Down",
            (InputContext.BROWSE,),
            key_display="j/down",
        ),
        CommandBindingSpec(
            "down",
            InputCommand.BROWSE_MOVE_DOWN,
            "Browse Down",
            (InputContext.BROWSE,),
            show=False,
        ),
        CommandBindingSpec(
            "h",
            InputCommand.BROWSE_MOVE_LEFT,
            "Browse Left",
            (InputContext.BROWSE,),
            key_display="h/left",
        ),
        CommandBindingSpec(
            "left",
            InputCommand.BROWSE_MOVE_LEFT,
            "Browse Left",
            (InputContext.BROWSE,),
            show=False,
        ),
        CommandBindingSpec(
            "l",
            InputCommand.BROWSE_MOVE_RIGHT,
            "Browse Right",
            (InputContext.BROWSE,),
            key_display="l/right",
        ),
        CommandBindingSpec(
            "right",
            InputCommand.BROWSE_MOVE_RIGHT,
            "Browse Right",
            (InputContext.BROWSE,),
            show=False,
        ),
        CommandBindingSpec(
            "enter",
            InputCommand.BROWSE_CONFIRM,
            "Select",
            (InputContext.BROWSE,),
        ),
        CommandBindingSpec(
            "ctrl+j",
            InputCommand.GRADE_NEXT_QUESTION,
            "Next Q",
            (InputContext.GRADE,),
        ),
        CommandBindingSpec(
            "ctrl+k",
            InputCommand.GRADE_PREVIOUS_QUESTION,
            "Prev Q",
            (InputContext.GRADE,),
        ),
        CommandBindingSpec(
            "ctrl+right_square_bracket",
            InputCommand.GRADE_NEXT_STUDENT,
            "Next Student",
            (InputContext.GRADE,),
        ),
        CommandBindingSpec(
            "ctrl+left_square_bracket",
            InputCommand.GRADE_PREVIOUS_STUDENT,
            "Prev Student",
            (InputContext.GRADE,),
        ),
        CommandBindingSpec(
            "ctrl+g",
            InputCommand.GRADE_FINALIZE_STUDENT,
            "Finalize",
            (InputContext.GRADE,),
        ),
    ),
)

EMACS_PROFILE = InputProfile(
    name=InputProfileName.EMACS,
    bindings=(
        CommandBindingSpec(
            "q",
            InputCommand.APP_QUIT,
            "Quit",
            (InputContext.GLOBAL,),
            show=True,
        ),
        CommandBindingSpec(
            "escape",
            InputCommand.APP_BACK,
            "Back",
            (InputContext.GLOBAL,),
            show=True,
        ),
        CommandBindingSpec(
            "ctrl+p",
            InputCommand.BROWSE_MOVE_UP,
            "Browse Up",
            (InputContext.BROWSE,),
            key_display="ctrl+p/up",
        ),
        CommandBindingSpec(
            "up",
            InputCommand.BROWSE_MOVE_UP,
            "Browse Up",
            (InputContext.BROWSE,),
            show=False,
        ),
        CommandBindingSpec(
            "ctrl+n",
            InputCommand.BROWSE_MOVE_DOWN,
            "Browse Down",
            (InputContext.BROWSE,),
            key_display="ctrl+n/down",
        ),
        CommandBindingSpec(
            "down",
            InputCommand.BROWSE_MOVE_DOWN,
            "Browse Down",
            (InputContext.BROWSE,),
            show=False,
        ),
        CommandBindingSpec(
            "ctrl+b",
            InputCommand.BROWSE_MOVE_LEFT,
            "Browse Left",
            (InputContext.BROWSE,),
            key_display="ctrl+b/left",
        ),
        CommandBindingSpec(
            "left",
            InputCommand.BROWSE_MOVE_LEFT,
            "Browse Left",
            (InputContext.BROWSE,),
            show=False,
        ),
        CommandBindingSpec(
            "ctrl+f",
            InputCommand.BROWSE_MOVE_RIGHT,
            "Browse Right",
            (InputContext.BROWSE,),
            key_display="ctrl+f/right",
        ),
        CommandBindingSpec(
            "right",
            InputCommand.BROWSE_MOVE_RIGHT,
            "Browse Right",
            (InputContext.BROWSE,),
            show=False,
        ),
        CommandBindingSpec(
            "enter",
            InputCommand.BROWSE_CONFIRM,
            "Select",
            (InputContext.BROWSE,),
        ),
        CommandBindingSpec(
            "ctrl+j",
            InputCommand.GRADE_NEXT_QUESTION,
            "Next Q",
            (InputContext.GRADE,),
        ),
        CommandBindingSpec(
            "ctrl+k",
            InputCommand.GRADE_PREVIOUS_QUESTION,
            "Prev Q",
            (InputContext.GRADE,),
        ),
        CommandBindingSpec(
            "ctrl+right_square_bracket",
            InputCommand.GRADE_NEXT_STUDENT,
            "Next Student",
            (InputContext.GRADE,),
        ),
        CommandBindingSpec(
            "ctrl+left_square_bracket",
            InputCommand.GRADE_PREVIOUS_STUDENT,
            "Prev Student",
            (InputContext.GRADE,),
        ),
        CommandBindingSpec(
            "ctrl+g",
            InputCommand.GRADE_FINALIZE_STUDENT,
            "Finalize",
            (InputContext.GRADE,),
        ),
    ),
)

INPUT_PROFILES = {
    STANDARD_PROFILE.name: STANDARD_PROFILE,
    VIM_PROFILE.name: VIM_PROFILE,
    EMACS_PROFILE.name: EMACS_PROFILE,
}


class InputController:
    def __init__(
        self,
        state: TuiWorkflowState,
        *,
        profile_name: InputProfileName | str | None = None,
    ) -> None:
        self._state = state
        self._handlers: dict[InputCommand, Callable[[], None]] = {}
        self._profile_name = self._coerce_profile_name(profile_name)

    @classmethod
    def all_bindings(cls) -> list[Binding]:
        bindings: list[Binding] = []
        for profile_name, profile in INPUT_PROFILES.items():
            for binding in profile.bindings:
                bindings.append(binding.to_binding(profile_name))
        return bindings

    @classmethod
    def bindings_for_profile(
        cls,
        profile_name: InputProfileName | str | None = None,
    ) -> list[Binding]:
        profile = cls.profile(profile_name)
        return [binding.to_binding(profile.name) for binding in profile.bindings]

    @classmethod
    def profile(
        cls,
        profile_name: InputProfileName | str | None = None,
    ) -> InputProfile:
        normalized_name = cls._coerce_profile_name(profile_name)
        try:
            return INPUT_PROFILES[normalized_name]
        except KeyError as exc:
            raise ValueError(f"Unknown input profile: {profile_name}") from exc

    @classmethod
    def available_profiles(cls) -> tuple[InputProfileName, ...]:
        return tuple(INPUT_PROFILES.keys())

    def profile_name(self) -> InputProfileName:
        return self._profile_name

    def set_profile(self, profile_name: InputProfileName | str) -> None:
        self._profile_name = self._coerce_profile_name(profile_name)

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

    def binding_available(
        self,
        profile_name: InputProfileName | str,
        command: InputCommand | str,
    ) -> bool:
        normalized_profile = self._coerce_profile_name(profile_name)
        if normalized_profile != self._profile_name:
            return False
        return self.command_available(command)

    def command_available(self, command: InputCommand | str) -> bool:
        normalized_command = self._coerce_command(command)
        current_context = self.current_context()
        for binding in self.profile(self._profile_name).bindings:
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
        if self._state.current_page == PAGE_BROWSE:
            return InputContext.BROWSE
        return InputContext.GLOBAL

    @staticmethod
    def _coerce_command(command: InputCommand | str) -> InputCommand:
        if isinstance(command, InputCommand):
            return command
        try:
            return InputCommand(command)
        except ValueError as exc:
            raise ValueError(f"Unknown input command: {command}") from exc

    @staticmethod
    def _coerce_profile_name(
        profile_name: InputProfileName | str | None,
    ) -> InputProfileName:
        if profile_name is None:
            return current_default_input_profile()
        if isinstance(profile_name, InputProfileName):
            return profile_name
        try:
            return InputProfileName(profile_name)
        except ValueError as exc:
            raise ValueError(f"Unknown input profile: {profile_name}") from exc
