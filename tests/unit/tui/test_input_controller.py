from local_grader.tui.controllers.input_controller import (
    DEFAULT_INPUT_PROFILE,
    InputCommand,
    InputContext,
    InputProfileName,
    InputController,
    current_default_input_profile,
)
from local_grader.tui.state import PAGE_HOME, PAGE_GRADE, TuiWorkflowState


def test_command_availability_tracks_active_context() -> None:
    state = TuiWorkflowState(current_page=PAGE_HOME)
    controller = InputController(state)

    assert controller.current_context() == InputContext.HOME
    assert controller.command_available(InputCommand.HOME_MOVE_UP) is True
    assert controller.command_available(InputCommand.GRADE_NEXT_QUESTION) is False
    assert controller.command_available(InputCommand.APP_QUIT) is True

    state.current_page = PAGE_GRADE

    assert controller.current_context() == InputContext.GRADE
    assert controller.command_available(InputCommand.HOME_MOVE_UP) is False
    assert controller.command_available(InputCommand.GRADE_NEXT_QUESTION) is True


def test_dispatch_calls_registered_handler_only_in_matching_context() -> None:
    state = TuiWorkflowState(current_page=PAGE_HOME)
    controller = InputController(state)
    calls: list[str] = []
    controller.register(InputCommand.HOME_MOVE_UP, lambda: calls.append("home"))
    controller.register(
        InputCommand.GRADE_NEXT_QUESTION,
        lambda: calls.append("grade"),
    )

    controller.dispatch(InputCommand.GRADE_NEXT_QUESTION)
    controller.dispatch(InputCommand.HOME_MOVE_UP)

    state.current_page = PAGE_GRADE
    controller.dispatch(InputCommand.HOME_MOVE_UP)
    controller.dispatch(InputCommand.GRADE_NEXT_QUESTION)

    assert calls == ["home", "grade"]


def test_profiles_change_keys_without_changing_semantic_commands() -> None:
    standard = InputController.profile(InputProfileName.STANDARD)
    vim = InputController.profile(InputProfileName.VIM)
    emacs = InputController.profile(InputProfileName.EMACS)

    standard_commands = {binding.command for binding in standard.bindings}
    vim_commands = {binding.command for binding in vim.bindings}
    emacs_commands = {binding.command for binding in emacs.bindings}

    assert standard_commands == vim_commands == emacs_commands
    assert standard.bindings != vim.bindings
    assert standard.bindings != emacs.bindings


def test_none_profile_uses_runtime_default() -> None:
    assert current_default_input_profile() == DEFAULT_INPUT_PROFILE
    assert InputController.profile().name == DEFAULT_INPUT_PROFILE


def test_binding_available_requires_matching_profile_and_context() -> None:
    state = TuiWorkflowState(current_page=PAGE_HOME)
    controller = InputController(state, profile_name=InputProfileName.VIM)

    assert (
        controller.binding_available(
            InputProfileName.VIM,
            InputCommand.HOME_MOVE_DOWN,
        )
        is True
    )
    assert (
        controller.binding_available(
            InputProfileName.STANDARD,
            InputCommand.HOME_MOVE_DOWN,
        )
        is False
    )
    assert (
        controller.binding_available(
            InputProfileName.EMACS,
            InputCommand.HOME_MOVE_DOWN,
        )
        is False
    )

    state.current_page = PAGE_GRADE

    assert (
        controller.binding_available(
            InputProfileName.VIM,
            InputCommand.HOME_MOVE_DOWN,
        )
        is False
    )
