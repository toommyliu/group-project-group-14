from opengrader.tui.controllers.input_controller import (
    INPUT_BINDINGS,
    InputCommand,
    InputContext,
    InputController,
)
from opengrader.tui.state import PAGE_GRADE, PAGE_QA_PICKER, TuiWorkflowState


def test_command_availability_tracks_active_context() -> None:
    state = TuiWorkflowState(current_page=PAGE_QA_PICKER)
    controller = InputController(state)

    assert controller.current_context() == InputContext.QA
    assert controller.command_available(InputCommand.QA_MOVE_UP) is True
    assert controller.command_available(InputCommand.QA_SWITCH_PANE) is True
    assert controller.command_available(InputCommand.GRADE_NEXT_STUDENT) is False
    assert controller.command_available(InputCommand.APP_QUIT) is True

    state.current_page = PAGE_GRADE

    assert controller.current_context() == InputContext.GRADE
    assert controller.command_available(InputCommand.QA_MOVE_UP) is False
    assert controller.command_available(InputCommand.QA_SWITCH_PANE) is False
    assert controller.command_available(InputCommand.GRADE_NEXT_STUDENT) is True


def test_dispatch_calls_registered_handler_only_in_matching_context() -> None:
    state = TuiWorkflowState(current_page=PAGE_QA_PICKER)
    controller = InputController(state)
    calls: list[str] = []
    controller.register(InputCommand.QA_MOVE_UP, lambda: calls.append("qa"))
    controller.register(
        InputCommand.GRADE_NEXT_STUDENT,
        lambda: calls.append("grade"),
    )

    controller.dispatch(InputCommand.GRADE_NEXT_STUDENT)
    controller.dispatch(InputCommand.QA_MOVE_UP)

    state.current_page = PAGE_GRADE
    controller.dispatch(InputCommand.QA_MOVE_UP)
    controller.dispatch(InputCommand.GRADE_NEXT_STUDENT)

    assert calls == ["qa", "grade"]


def test_single_keymap_merges_standard_and_vim_navigation_keys() -> None:
    bindings_by_command = {binding.command: binding for binding in INPUT_BINDINGS}

    assert bindings_by_command[InputCommand.QA_MOVE_DOWN].keys == "j,down"
    assert bindings_by_command[InputCommand.QA_MOVE_DOWN].key_display == "j/down"
    assert bindings_by_command[InputCommand.QA_MOVE_UP].keys == "k,up"
    assert bindings_by_command[InputCommand.QA_MOVE_UP].key_display == "k/up"
    assert bindings_by_command[InputCommand.QA_MOVE_STUDENT_DOWN].keys == "j,down"
    assert (
        bindings_by_command[InputCommand.QA_MOVE_STUDENT_DOWN].key_display
        == "j/down"
    )
    assert bindings_by_command[InputCommand.QA_MOVE_STUDENT_UP].keys == "k,up"
    assert bindings_by_command[InputCommand.QA_MOVE_STUDENT_UP].key_display == "k/up"
    assert bindings_by_command[InputCommand.GRADE_NEXT_STUDENT].keys == "j,right"
    assert bindings_by_command[InputCommand.GRADE_NEXT_STUDENT].key_display == "j/right"
    assert bindings_by_command[InputCommand.GRADE_PREVIOUS_STUDENT].keys == "k,left"
    assert (
        bindings_by_command[InputCommand.GRADE_PREVIOUS_STUDENT].key_display
        == "k/left"
    )
