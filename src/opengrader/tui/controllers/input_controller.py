from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from textual.binding import Binding

from opengrader.tui.state import (
    PAGE_COURSE_PICKER,
    PAGE_GRADE,
    PAGE_HOME,
    PAGE_QA_PICKER,
    TuiWorkflowState,
)


class InputCommand(str, Enum):
    APP_QUIT = "app_quit"
    APP_BACK = "app_back"
    HOME_MOVE_UP = "home_move_up"
    HOME_MOVE_DOWN = "home_move_down"
    HOME_MOVE_LEFT = "home_move_left"
    HOME_MOVE_RIGHT = "home_move_right"
    HOME_CONFIRM = "home_confirm"
    HOME_ASSIGNMENT_START = "home_assignment_start"
    HOME_ASSIGNMENT_DETAILS = "home_assignment_details"
    HOME_ASSIGNMENT_EDIT_RUBRIC = "home_assignment_edit_rubric"
    HOME_ASSIGNMENT_EDIT_TEMPLATE = "home_assignment_edit_template"
    HOME_CREATE = "home_create"
    HOME_FOCUS_COURSES = "home_focus_courses"
    HOME_FOCUS_ASSIGNMENTS = "home_focus_assignments"
    HOME_FOCUS_STUDENTS = "home_focus_students"
    HOME_FOCUS_DETAILS = "home_focus_details"
    HOME_DETAILS_NEXT_TAB = "home_details_next_tab"
    HOME_DETAILS_PREV_TAB = "home_details_prev_tab"
    HOME_DETAILS_NEXT_PAGE = "home_details_next_page"
    HOME_DETAILS_PREV_PAGE = "home_details_prev_page"
    QA_MOVE_UP = "qa_move_up"
    QA_MOVE_DOWN = "qa_move_down"
    QA_CONFIRM = "qa_confirm"
    QA_JUMP_UNGRADED = "qa_jump_ungraded_question"
    GRADE_NEXT_STUDENT = "grade_next_student"
    GRADE_PREVIOUS_STUDENT = "grade_previous_student"
    GRADE_NEXT_UNGRADED_STUDENT = "grade_next_ungraded_student"
    GRADE_PREVIOUS_UNGRADED_STUDENT = "grade_previous_ungraded_student"
    GRADE_FINALIZE_STUDENT = "grade_finalize_student"


class InputContext(str, Enum):
    HOME = "home"
    QA = "qa"
    GRADE = "grade"
    COURSE = "course"
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
            (InputContext.GRADE, InputContext.HOME, InputContext.QA),
            show=True,
        ),
        CommandBindingSpec(
            "up",
            InputCommand.HOME_MOVE_UP,
            "Navigate Up",
            (InputContext.HOME, InputContext.COURSE),
            show=False,
        ),
        CommandBindingSpec(
            "down",
            InputCommand.HOME_MOVE_DOWN,
            "Navigate Down",
            (InputContext.HOME, InputContext.COURSE),
            show=False,
        ),
        CommandBindingSpec(
            "left",
            InputCommand.HOME_MOVE_LEFT,
            "Navigate Left",
            (InputContext.HOME,),
            show=False,
        ),
        CommandBindingSpec(
            "right",
            InputCommand.HOME_MOVE_RIGHT,
            "Navigate Right",
            (InputContext.HOME,),
            show=False,
        ),
        CommandBindingSpec(
            "enter",
            InputCommand.HOME_CONFIRM,
            "Select",
            (InputContext.HOME, InputContext.COURSE),
        ),
        CommandBindingSpec(
            "g",
            InputCommand.HOME_ASSIGNMENT_START,
            "Start Grading",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "d",
            InputCommand.HOME_ASSIGNMENT_DETAILS,
            "Assignment Details",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "r",
            InputCommand.HOME_ASSIGNMENT_EDIT_RUBRIC,
            "Edit Rubric",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "t",
            InputCommand.HOME_ASSIGNMENT_EDIT_TEMPLATE,
            "Edit Template",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "n",
            InputCommand.HOME_CREATE,
            "Create",
            (InputContext.HOME, InputContext.COURSE),
            show=True,
        ),
        CommandBindingSpec(
            "1",
            InputCommand.HOME_FOCUS_COURSES,
            "Focus Courses",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "2",
            InputCommand.HOME_FOCUS_ASSIGNMENTS,
            "Focus Assignments",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "3",
            InputCommand.HOME_FOCUS_STUDENTS,
            "Focus Students",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "0",
            InputCommand.HOME_FOCUS_DETAILS,
            "Focus Details",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "right_square_bracket",
            InputCommand.HOME_DETAILS_NEXT_TAB,
            "Next Tab",
            (InputContext.HOME,),
            key_display="]",
        ),
        CommandBindingSpec(
            "left_square_bracket",
            InputCommand.HOME_DETAILS_PREV_TAB,
            "Prev Tab",
            (InputContext.HOME,),
            key_display="[",
        ),
        CommandBindingSpec(
            "full_stop",
            InputCommand.HOME_DETAILS_NEXT_PAGE,
            "Next Page",
            (InputContext.HOME,),
            key_display=".",
        ),
        CommandBindingSpec(
            "comma",
            InputCommand.HOME_DETAILS_PREV_PAGE,
            "Prev Page",
            (InputContext.HOME,),
            key_display=",",
        ),
        CommandBindingSpec(
            "j",
            InputCommand.QA_MOVE_DOWN,
            "Next QA",
            (InputContext.QA,),
            key_display="j/down",
        ),
        CommandBindingSpec(
            "down",
            InputCommand.QA_MOVE_DOWN,
            "Next QA",
            (InputContext.QA,),
            show=False,
        ),
        CommandBindingSpec(
            "k",
            InputCommand.QA_MOVE_UP,
            "Prev QA",
            (InputContext.QA,),
            key_display="k/up",
        ),
        CommandBindingSpec(
            "up",
            InputCommand.QA_MOVE_UP,
            "Prev QA",
            (InputContext.QA,),
            show=False,
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
            show=True,
        ),
        CommandBindingSpec(
            "ctrl+right_square_bracket",
            InputCommand.GRADE_NEXT_STUDENT,
            "Next Student",
            (InputContext.GRADE,),
            show=False,
        ),
        CommandBindingSpec(
            "ctrl+left_square_bracket",
            InputCommand.GRADE_PREVIOUS_STUDENT,
            "Prev Student",
            (InputContext.GRADE,),
            show=False,
        ),
        CommandBindingSpec(
            "j",
            InputCommand.GRADE_NEXT_STUDENT,
            "Next Student",
            (InputContext.GRADE,),
        ),
        CommandBindingSpec(
            "k",
            InputCommand.GRADE_PREVIOUS_STUDENT,
            "Prev Student",
            (InputContext.GRADE,),
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
        CommandBindingSpec(
            "right",
            InputCommand.GRADE_NEXT_STUDENT,
            "Next Student",
            (InputContext.GRADE,),
            show=False,
        ),
        CommandBindingSpec(
            "left",
            InputCommand.GRADE_PREVIOUS_STUDENT,
            "Prev Student",
            (InputContext.GRADE,),
            show=False,
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
            InputCommand.HOME_MOVE_UP,
            "Navigate Up",
            (InputContext.HOME,),
            key_display="k/up",
        ),
        CommandBindingSpec(
            "up",
            InputCommand.HOME_MOVE_UP,
            "Navigate Up",
            (InputContext.HOME,),
            show=False,
        ),
        CommandBindingSpec(
            "j",
            InputCommand.HOME_MOVE_DOWN,
            "Navigate Down",
            (InputContext.HOME,),
            key_display="j/down",
        ),
        CommandBindingSpec(
            "down",
            InputCommand.HOME_MOVE_DOWN,
            "Navigate Down",
            (InputContext.HOME,),
            show=False,
        ),
        CommandBindingSpec(
            "h",
            InputCommand.HOME_MOVE_LEFT,
            "Navigate Left",
            (InputContext.HOME,),
            key_display="h/left",
        ),
        CommandBindingSpec(
            "left",
            InputCommand.HOME_MOVE_LEFT,
            "Navigate Left",
            (InputContext.HOME,),
            show=False,
        ),
        CommandBindingSpec(
            "l",
            InputCommand.HOME_MOVE_RIGHT,
            "Navigate Right",
            (InputContext.HOME,),
            key_display="l/right",
        ),
        CommandBindingSpec(
            "right",
            InputCommand.HOME_MOVE_RIGHT,
            "Navigate Right",
            (InputContext.HOME,),
            show=False,
        ),
        CommandBindingSpec(
            "enter",
            InputCommand.HOME_CONFIRM,
            "Select",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "g",
            InputCommand.HOME_ASSIGNMENT_START,
            "Start Grading",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "d",
            InputCommand.HOME_ASSIGNMENT_DETAILS,
            "Assignment Details",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "r",
            InputCommand.HOME_ASSIGNMENT_EDIT_RUBRIC,
            "Edit Rubric",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "t",
            InputCommand.HOME_ASSIGNMENT_EDIT_TEMPLATE,
            "Edit Template",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "n",
            InputCommand.HOME_CREATE,
            "Create Course",
            (InputContext.HOME,),
            show=True,
        ),
        CommandBindingSpec(
            "1",
            InputCommand.HOME_FOCUS_COURSES,
            "Focus Courses",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "2",
            InputCommand.HOME_FOCUS_ASSIGNMENTS,
            "Focus Assignments",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "3",
            InputCommand.HOME_FOCUS_STUDENTS,
            "Focus Students",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "4",
            InputCommand.HOME_FOCUS_DETAILS,
            "Focus Details",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "right_square_bracket",
            InputCommand.HOME_DETAILS_NEXT_TAB,
            "Next Tab",
            (InputContext.HOME,),
            key_display="]",
        ),
        CommandBindingSpec(
            "left_square_bracket",
            InputCommand.HOME_DETAILS_PREV_TAB,
            "Prev Tab",
            (InputContext.HOME,),
            key_display="[",
        ),
        CommandBindingSpec(
            "full_stop",
            InputCommand.HOME_DETAILS_NEXT_PAGE,
            "Next Page",
            (InputContext.HOME,),
            key_display=".",
        ),
        CommandBindingSpec(
            "comma",
            InputCommand.HOME_DETAILS_PREV_PAGE,
            "Prev Page",
            (InputContext.HOME,),
            key_display=",",
        ),
        CommandBindingSpec(
            "j",
            InputCommand.QA_MOVE_DOWN,
            "Next QA",
            (InputContext.QA,),
            key_display="j/down",
        ),
        CommandBindingSpec(
            "down",
            InputCommand.QA_MOVE_DOWN,
            "Next QA",
            (InputContext.QA,),
            show=False,
        ),
        CommandBindingSpec(
            "k",
            InputCommand.QA_MOVE_UP,
            "Prev QA",
            (InputContext.QA,),
            key_display="k/up",
        ),
        CommandBindingSpec(
            "up",
            InputCommand.QA_MOVE_UP,
            "Prev QA",
            (InputContext.QA,),
            show=False,
        ),
        CommandBindingSpec(
            "enter",
            InputCommand.QA_CONFIRM,
            "Grade QA",
            (InputContext.QA,),
        ),
        CommandBindingSpec(
            "ctrl+right_square_bracket",
            InputCommand.GRADE_NEXT_STUDENT,
            "Next Student",
            (InputContext.GRADE,),
            show=False,
        ),
        CommandBindingSpec(
            "ctrl+left_square_bracket",
            InputCommand.GRADE_PREVIOUS_STUDENT,
            "Prev Student",
            (InputContext.GRADE,),
            show=False,
        ),
        CommandBindingSpec(
            "j",
            InputCommand.GRADE_NEXT_STUDENT,
            "Next Student",
            (InputContext.GRADE,),
        ),
        CommandBindingSpec(
            "k",
            InputCommand.GRADE_PREVIOUS_STUDENT,
            "Prev Student",
            (InputContext.GRADE,),
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
        CommandBindingSpec(
            "right",
            InputCommand.GRADE_NEXT_STUDENT,
            "Next Student",
            (InputContext.GRADE,),
            show=False,
        ),
        CommandBindingSpec(
            "left",
            InputCommand.GRADE_PREVIOUS_STUDENT,
            "Prev Student",
            (InputContext.GRADE,),
            show=False,
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
            InputCommand.HOME_MOVE_UP,
            "Navigate Up",
            (InputContext.HOME,),
            key_display="ctrl+p/up",
        ),
        CommandBindingSpec(
            "up",
            InputCommand.HOME_MOVE_UP,
            "Navigate Up",
            (InputContext.HOME,),
            show=False,
        ),
        CommandBindingSpec(
            "ctrl+n",
            InputCommand.HOME_MOVE_DOWN,
            "Navigate Down",
            (InputContext.HOME,),
            key_display="ctrl+n/down",
        ),
        CommandBindingSpec(
            "down",
            InputCommand.HOME_MOVE_DOWN,
            "Navigate Down",
            (InputContext.HOME,),
            show=False,
        ),
        CommandBindingSpec(
            "ctrl+b",
            InputCommand.HOME_MOVE_LEFT,
            "Navigate Left",
            (InputContext.HOME,),
            key_display="ctrl+b/left",
        ),
        CommandBindingSpec(
            "left",
            InputCommand.HOME_MOVE_LEFT,
            "Navigate Left",
            (InputContext.HOME,),
            show=False,
        ),
        CommandBindingSpec(
            "ctrl+f",
            InputCommand.HOME_MOVE_RIGHT,
            "Navigate Right",
            (InputContext.HOME,),
            key_display="ctrl+f/right",
        ),
        CommandBindingSpec(
            "right",
            InputCommand.HOME_MOVE_RIGHT,
            "Navigate Right",
            (InputContext.HOME,),
            show=False,
        ),
        CommandBindingSpec(
            "enter",
            InputCommand.HOME_CONFIRM,
            "Select",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "g",
            InputCommand.HOME_ASSIGNMENT_START,
            "Start Grading",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "d",
            InputCommand.HOME_ASSIGNMENT_DETAILS,
            "Assignment Details",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "r",
            InputCommand.HOME_ASSIGNMENT_EDIT_RUBRIC,
            "Edit Rubric",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "t",
            InputCommand.HOME_ASSIGNMENT_EDIT_TEMPLATE,
            "Edit Template",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "n",
            InputCommand.HOME_CREATE,
            "Create Course",
            (InputContext.HOME,),
            show=True,
        ),
        CommandBindingSpec(
            "1",
            InputCommand.HOME_FOCUS_COURSES,
            "Focus Courses",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "2",
            InputCommand.HOME_FOCUS_ASSIGNMENTS,
            "Focus Assignments",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "3",
            InputCommand.HOME_FOCUS_STUDENTS,
            "Focus Students",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "4",
            InputCommand.HOME_FOCUS_DETAILS,
            "Focus Details",
            (InputContext.HOME,),
        ),
        CommandBindingSpec(
            "right_square_bracket",
            InputCommand.HOME_DETAILS_NEXT_TAB,
            "Next Tab",
            (InputContext.HOME,),
            key_display="]",
        ),
        CommandBindingSpec(
            "left_square_bracket",
            InputCommand.HOME_DETAILS_PREV_TAB,
            "Prev Tab",
            (InputContext.HOME,),
            key_display="[",
        ),
        CommandBindingSpec(
            "full_stop",
            InputCommand.HOME_DETAILS_NEXT_PAGE,
            "Next Page",
            (InputContext.HOME,),
            key_display=".",
        ),
        CommandBindingSpec(
            "comma",
            InputCommand.HOME_DETAILS_PREV_PAGE,
            "Prev Page",
            (InputContext.HOME,),
            key_display=",",
        ),
        CommandBindingSpec(
            "j",
            InputCommand.QA_MOVE_DOWN,
            "Next QA",
            (InputContext.QA,),
            key_display="j/down",
        ),
        CommandBindingSpec(
            "down",
            InputCommand.QA_MOVE_DOWN,
            "Next QA",
            (InputContext.QA,),
            show=False,
        ),
        CommandBindingSpec(
            "k",
            InputCommand.QA_MOVE_UP,
            "Prev QA",
            (InputContext.QA,),
            key_display="k/up",
        ),
        CommandBindingSpec(
            "up",
            InputCommand.QA_MOVE_UP,
            "Prev QA",
            (InputContext.QA,),
            show=False,
        ),
        CommandBindingSpec(
            "enter",
            InputCommand.QA_CONFIRM,
            "Grade QA",
            (InputContext.QA,),
        ),
        CommandBindingSpec(
            "ctrl+right_square_bracket",
            InputCommand.GRADE_NEXT_STUDENT,
            "Next Student",
            (InputContext.GRADE,),
            show=False,
        ),
        CommandBindingSpec(
            "ctrl+left_square_bracket",
            InputCommand.GRADE_PREVIOUS_STUDENT,
            "Prev Student",
            (InputContext.GRADE,),
            show=False,
        ),
        CommandBindingSpec(
            "j",
            InputCommand.GRADE_NEXT_STUDENT,
            "Next Student",
            (InputContext.GRADE,),
        ),
        CommandBindingSpec(
            "k",
            InputCommand.GRADE_PREVIOUS_STUDENT,
            "Prev Student",
            (InputContext.GRADE,),
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
        CommandBindingSpec(
            "right",
            InputCommand.GRADE_NEXT_STUDENT,
            "Next Student",
            (InputContext.GRADE,),
            show=False,
            key_display="ctrl+]/right",
        ),
        CommandBindingSpec(
            "left",
            InputCommand.GRADE_PREVIOUS_STUDENT,
            "Prev Student",
            (InputContext.GRADE,),
            show=False,
            key_display="ctrl+[/left",
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
        if self._state.current_page == PAGE_QA_PICKER:
            return InputContext.QA
        if self._state.current_page == PAGE_HOME:
            return InputContext.HOME
        if self._state.current_page == PAGE_COURSE_PICKER:
            return InputContext.COURSE
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
