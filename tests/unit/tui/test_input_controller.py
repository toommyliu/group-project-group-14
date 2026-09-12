from opengrader.tui.controllers.input_controller import (
    command_survives_text_focus,
    key_is_typeable,
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
    assert controller.command_available(InputCommand.QA_TOGGLE_STUDENT_DISPLAY) is True
    assert controller.command_available(InputCommand.QA_FILTER) is True
    assert controller.command_available(InputCommand.GRADE_NEXT_STUDENT) is False
    assert controller.command_available(InputCommand.GRADE_FOCUS_COMMENT) is False
    assert controller.command_available(InputCommand.GRADE_SHRINK_SIDEBAR) is False
    assert controller.command_available(InputCommand.GRADE_WIDEN_SIDEBAR) is False
    assert controller.command_available(InputCommand.APP_QUIT) is True

    state.current_page = PAGE_GRADE

    assert controller.current_context() == InputContext.GRADE
    assert controller.command_available(InputCommand.QA_MOVE_UP) is False
    assert controller.command_available(InputCommand.QA_SWITCH_PANE) is False
    assert controller.command_available(InputCommand.QA_TOGGLE_STUDENT_DISPLAY) is False
    assert controller.command_available(InputCommand.QA_FILTER) is False
    assert controller.command_available(InputCommand.GRADE_NEXT_STUDENT) is True
    assert controller.command_available(InputCommand.GRADE_FOCUS_COMMENT) is True
    assert controller.command_available(InputCommand.GRADE_SHRINK_SIDEBAR) is True
    assert controller.command_available(InputCommand.GRADE_WIDEN_SIDEBAR) is True


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
        bindings_by_command[InputCommand.QA_MOVE_STUDENT_DOWN].key_display == "j/down"
    )
    assert bindings_by_command[InputCommand.QA_MOVE_STUDENT_UP].keys == "k,up"
    assert bindings_by_command[InputCommand.QA_MOVE_STUDENT_UP].key_display == "k/up"
    assert bindings_by_command[InputCommand.QA_TOGGLE_STUDENT_DISPLAY].keys == "d"
    assert (
        bindings_by_command[InputCommand.QA_TOGGLE_STUDENT_DISPLAY].description
        == "Display"
    )
    assert bindings_by_command[InputCommand.QA_FILTER].keys == "/"
    assert bindings_by_command[InputCommand.QA_FILTER].description == "Filter"
    assert bindings_by_command[InputCommand.GRADE_NEXT_QUESTION].keys == "j,down"
    assert bindings_by_command[InputCommand.GRADE_PREVIOUS_QUESTION].keys == "k,up"
    assert bindings_by_command[InputCommand.GRADE_NEXT_STUDENT].keys == "ctrl+j,right"
    assert (
        bindings_by_command[InputCommand.GRADE_NEXT_STUDENT].key_display
        == "ctrl+j/right"
    )
    assert bindings_by_command[InputCommand.GRADE_PREVIOUS_STUDENT].keys == "ctrl+k,left"
    assert (
        bindings_by_command[InputCommand.GRADE_PREVIOUS_STUDENT].key_display
        == "ctrl+k/left"
    )
    assert bindings_by_command[InputCommand.GRADE_NEXT_UNGRADED_STUDENT].keys == "n"
    assert bindings_by_command[InputCommand.GRADE_PREVIOUS_UNGRADED_STUDENT].keys == "p"
    for command, key in (
        (InputCommand.OPEN_REVIEW_QUEUE, "R"),
        (InputCommand.OPEN_CONSISTENCY_AUDIT, "C"),
        (InputCommand.OPEN_SIMILARITY_AUDIT, "P"),
        (InputCommand.OPEN_ITEM_ANALYSIS, "S"),
        (InputCommand.OPEN_GRADE_HISTORY, "H"),
        (InputCommand.OPEN_GRADING_SESSION, "V"),
        (InputCommand.OPEN_REGRADE_QUEUE, "G"),
        (InputCommand.FILE_REGRADE, "F"),
    ):
        assert bindings_by_command[command].keys == key
    assert bindings_by_command[InputCommand.GRADE_FOCUS_COMMENT].keys == "c"
    assert bindings_by_command[InputCommand.GRADE_FOCUS_COMMENT].description == "Comment"
    assert bindings_by_command[InputCommand.GRADE_SHRINK_SIDEBAR].keys == "["
    assert bindings_by_command[InputCommand.GRADE_WIDEN_SIDEBAR].keys == "]"


def test_only_chords_survive_a_focused_text_box() -> None:
    bindings_by_command = {binding.command: binding for binding in INPUT_BINDINGS}

    assert command_survives_text_focus(InputCommand.GRADE_FINALIZE_STUDENT)
    assert command_survives_text_focus(InputCommand.GRADE_TOGGLE_LAYOUT)

    for command in (
        InputCommand.GRADE_FIRST_STUDENT,
        InputCommand.GRADE_NEXT_QUESTION,
        InputCommand.GRADE_NEXT_UNGRADED_STUDENT,
        InputCommand.GRADE_ZOOM_IN,
        InputCommand.GRADE_RESET_ZOOM,
        InputCommand.GRADE_SHRINK_SIDEBAR,
        InputCommand.GRADE_ROTATE_LEFT,
        InputCommand.OPEN_REVIEW_QUEUE,
    ):
        assert not command_survives_text_focus(command), bindings_by_command[command]

    assert not command_survives_text_focus(InputCommand.GRADE_NEXT_STUDENT)


def test_key_typeability_classification() -> None:
    assert key_is_typeable("a")
    assert key_is_typeable("R")
    assert key_is_typeable("comma")
    assert key_is_typeable("left")
    assert key_is_typeable("escape")
    assert key_is_typeable("shift+tab")
    assert not key_is_typeable("ctrl+g")
    assert not key_is_typeable("alt+1")
    assert not key_is_typeable("f5")


def test_visible_bindings_returns_shown_bindings_for_current_context() -> None:
    state = TuiWorkflowState(current_page=PAGE_QA_PICKER)
    controller = InputController(state)

    qa_commands = {binding.command for binding in controller.visible_bindings()}
    assert InputCommand.QA_FILTER in qa_commands
    assert InputCommand.QA_MOVE_DOWN in qa_commands
    assert InputCommand.QA_TOGGLE_STUDENT_DISPLAY in qa_commands
    assert InputCommand.GRADE_NEXT_STUDENT not in qa_commands
    assert InputCommand.GRADE_SHRINK_SIDEBAR not in qa_commands

    state.current_page = PAGE_GRADE
    grade_commands = {binding.command for binding in controller.visible_bindings()}
    assert InputCommand.QA_FILTER not in grade_commands
    assert InputCommand.GRADE_NEXT_STUDENT in grade_commands
    assert InputCommand.GRADE_SHRINK_SIDEBAR not in grade_commands


def test_grade_history_requires_teacher_mode() -> None:
    state = TuiWorkflowState(current_page=PAGE_QA_PICKER)
    controller = InputController(state)

    assert state.teacher_mode is False
    assert controller.command_available(InputCommand.OPEN_GRADE_HISTORY) is False
    assert InputCommand.OPEN_GRADE_HISTORY not in {
        b.command for b in controller.visible_bindings()
    }
    assert controller.command_available(InputCommand.OPEN_REVIEW_QUEUE) is True

    state.teacher_mode = True
    assert controller.command_available(InputCommand.OPEN_GRADE_HISTORY) is True
    assert InputCommand.OPEN_GRADE_HISTORY in {
        b.command for b in controller.visible_bindings()
    }

    state.current_page = "setup"
    assert controller.command_available(InputCommand.OPEN_GRADE_HISTORY) is False


def test_file_regrade_requires_teacher_mode() -> None:
    state = TuiWorkflowState(current_page=PAGE_QA_PICKER)
    controller = InputController(state)

    assert state.teacher_mode is False
    assert controller.command_available(InputCommand.FILE_REGRADE) is False
    assert InputCommand.FILE_REGRADE not in {
        b.command for b in controller.visible_bindings()
    }

    state.teacher_mode = True
    assert controller.command_available(InputCommand.FILE_REGRADE) is True
    state.current_page = PAGE_GRADE
    assert controller.command_available(InputCommand.FILE_REGRADE) is True


def test_teacher_mode_toggle_disabled_when_anonymity_locked() -> None:
    state = TuiWorkflowState(current_page=PAGE_QA_PICKER)
    controller = InputController(state)

    assert state.anonymity_locked is False
    assert controller.command_available(InputCommand.APP_TOGGLE_TEACHER_MODE) is True
    assert InputCommand.APP_TOGGLE_TEACHER_MODE in {
        b.command for b in controller.visible_bindings()
    }

    state.anonymity_locked = True
    assert controller.command_available(InputCommand.APP_TOGGLE_TEACHER_MODE) is False
    assert InputCommand.APP_TOGGLE_TEACHER_MODE not in {
        b.command for b in controller.visible_bindings()
    }

    calls: list[str] = []
    controller.register(
        InputCommand.APP_TOGGLE_TEACHER_MODE, lambda: calls.append("toggle")
    )
    controller.dispatch(InputCommand.APP_TOGGLE_TEACHER_MODE)
    assert calls == []


def test_handoff_and_backup_are_bound_on_the_workspace() -> None:
    from opengrader.tui.controllers.input_controller import (
        INPUT_BINDINGS,
        InputCommand,
        InputContext,
    )

    by_command = {spec.command: spec for spec in INPUT_BINDINGS}
    for command, key in (
        (InputCommand.OPEN_GRADER_PACKET, "E"),
        (InputCommand.OPEN_MERGE, "M"),
        (InputCommand.OPEN_BACKUP, "B"),
    ):
        spec = by_command[command]
        assert spec.keys == key
        assert InputContext.QA in spec.contexts
        assert InputContext.GRADE in spec.contexts
        assert key.isupper()
