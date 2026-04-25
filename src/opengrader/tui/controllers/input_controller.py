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


def _shared_command_set(
    *,
    qa_down: tuple[str, str | None],
    qa_up: tuple[str, str | None],
    grade_next: tuple[str, str | None],
    grade_prev: tuple[str, str | None],
) -> tuple[CommandBindingSpec, ...]:
    qa_down_keys, qa_down_display = qa_down
    qa_up_keys, qa_up_display = qa_up
    grade_next_keys, grade_next_display = grade_next
    grade_prev_keys, grade_prev_display = grade_prev

    bindings = [
        CommandBindingSpec("q", InputCommand.APP_QUIT, "Quit", (InputContext.GLOBAL,)),
        CommandBindingSpec(
            "escape",
            InputCommand.APP_BACK,
            "Back",
            (InputContext.QA, InputContext.GRADE),
        ),
        CommandBindingSpec(
            qa_down_keys,
            InputCommand.QA_MOVE_DOWN,
            "Next QA",
            (InputContext.QA,),
            key_display=qa_down_display,
        ),
        CommandBindingSpec(
            qa_up_keys,
            InputCommand.QA_MOVE_UP,
            "Prev QA",
            (InputContext.QA,),
            key_display=qa_up_display,
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
            grade_next_keys,
            InputCommand.GRADE_NEXT_STUDENT,
            "Next Student",
            (InputContext.GRADE,),
            key_display=grade_next_display,
        ),
        CommandBindingSpec(
            grade_prev_keys,
            InputCommand.GRADE_PREVIOUS_STUDENT,
            "Prev Student",
            (InputContext.GRADE,),
            key_display=grade_prev_display,
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
    ]

    if qa_down_keys != "down":
        bindings.append(
            CommandBindingSpec(
                "down",
                InputCommand.QA_MOVE_DOWN,
                "Next QA",
                (InputContext.QA,),
                show=False,
            )
        )
    if qa_up_keys != "up":
        bindings.append(
            CommandBindingSpec(
                "up",
                InputCommand.QA_MOVE_UP,
                "Prev QA",
                (InputContext.QA,),
                show=False,
            )
        )
    if grade_next_keys != "right":
        bindings.append(
            CommandBindingSpec(
                "right",
                InputCommand.GRADE_NEXT_STUDENT,
                "Next Student",
                (InputContext.GRADE,),
                show=False,
            )
        )
    if grade_prev_keys != "left":
        bindings.append(
            CommandBindingSpec(
                "left",
                InputCommand.GRADE_PREVIOUS_STUDENT,
                "Prev Student",
                (InputContext.GRADE,),
                show=False,
            )
        )

    return tuple(bindings)


STANDARD_PROFILE = InputProfile(
    name=InputProfileName.STANDARD,
    bindings=_shared_command_set(
        qa_down=("down", "down"),
        qa_up=("up", "up"),
        grade_next=("right", "right"),
        grade_prev=("left", "left"),
    ),
)

VIM_PROFILE = InputProfile(
    name=InputProfileName.VIM,
    bindings=_shared_command_set(
        qa_down=("j", "j/down"),
        qa_up=("k", "k/up"),
        grade_next=("j", None),
        grade_prev=("k", None),
    ),
)

EMACS_PROFILE = InputProfile(
    name=InputProfileName.EMACS,
    bindings=_shared_command_set(
        qa_down=("ctrl+n", "ctrl+n/down"),
        qa_up=("ctrl+p", "ctrl+p/up"),
        grade_next=("ctrl+f", "ctrl+f/right"),
        grade_prev=("ctrl+b", "ctrl+b/left"),
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
