from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from textual.binding import Binding

from opengrader.tui.state import (
    PAGE_GRADE,
    PAGE_QA_PICKER,
    PAGE_SETUP,
    TuiWorkflowState,
)


class InputCommand(str, Enum):
    APP_QUIT = "app_quit"
    APP_HELP = "app_help"
    APP_BACK = "app_back"
    SWITCH_ASSIGNMENT = "switch_assignment"
    OPEN_START_PAGE = "open_start_page"
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
    GRADE_APPLY_GROUP = "grade_apply_group"
    GRADE_TOGGLE_GROUP_MODE = "grade_toggle_group_mode"
    GRADE_TOGGLE_AI_REVIEW = "grade_toggle_ai_review"
    GRADE_CONFIRM_AI = "grade_confirm_ai"
    GRADE_AI_DRAFT_GRADES = "grade_ai_draft_grades"
    OPEN_UNIFY_ANSWERS = "open_unify_answers"
    GRADE_EDIT_RUBRIC = "grade_edit_rubric"
    GRADE_EDIT_TRANSCRIPTION = "grade_edit_transcription"
    GRADE_FOCUS_COMMENT = "grade_focus_comment"
    GRADE_SHRINK_SIDEBAR = "grade_shrink_sidebar"
    GRADE_WIDEN_SIDEBAR = "grade_widen_sidebar"
    GRADE_OPEN_EXTERNAL = "grade_open_external"
    GRADE_NEXT_PAGE_VIEW = "grade_next_page_view"
    GRADE_PREVIOUS_PAGE_VIEW = "grade_previous_page_view"
    GRADE_ZOOM_IN = "grade_zoom_in"
    GRADE_ZOOM_OUT = "grade_zoom_out"
    GRADE_RESET_ZOOM = "grade_reset_zoom"
    GRADE_ROTATE_LEFT = "grade_rotate_left"
    GRADE_ROTATE_RIGHT = "grade_rotate_right"
    GRADE_TOGGLE_LAYOUT = "grade_toggle_layout"
    GRADE_FIRST_STUDENT = "grade_first_student"
    APP_TOGGLE_TEACHER_MODE = "app_toggle_teacher_mode"
    UNDO_LAST_GRADE = "undo_last_grade"
    OPEN_REVIEW_QUEUE = "open_review_queue"
    OPEN_GRADE_HISTORY = "open_grade_history"
    OPEN_GRADING_SESSION = "open_grading_session"
    OPEN_GRADER_AGREEMENT = "open_grader_agreement"
    OPEN_CONSISTENCY_AUDIT = "open_consistency_audit"
    OPEN_SIMILARITY_AUDIT = "open_similarity_audit"
    OPEN_REGRADE_QUEUE = "open_regrade_queue"
    FILE_REGRADE = "file_regrade"
    OPEN_ITEM_ANALYSIS = "open_item_analysis"
    OPEN_LLM_EVAL = "open_llm_eval"
    OPEN_GRADER_PACKET = "open_grader_packet"
    OPEN_MERGE = "open_merge"
    OPEN_BACKUP = "open_backup"
    OPEN_RELEASE = "open_release"
    OPEN_WORK_CLAIMS = "open_work_claims"
    JOIN_SPLIT_SUBMISSIONS = "join_split_submissions"
    OPEN_CHANGE_REQUESTS = "open_change_requests"
    OPEN_COMMENT_LIBRARY = "open_comment_library"
    OPEN_FORMAT_GUIDE = "open_format_guide"


class InputContext(str, Enum):
    QA = "qa"
    GRADE = "grade"
    SETUP = "setup"
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
    requires_teacher_mode: bool = False
    requires_unlocked_anonymity: bool = False
    requires_open_session: bool = False

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
    # F2 leaves grading to choose something else: the workspace's assignment
    # list (`opengrader tui`), or the Start grading card (`opengrader classic`).
    # There is nothing to leave on the Tools page itself, so it is not bound there.
    CommandBindingSpec(
        "f2",
        InputCommand.SWITCH_ASSIGNMENT,
        "Switch assignment",
        (InputContext.QA, InputContext.GRADE),
    ),
    CommandBindingSpec(
        "f3",
        InputCommand.OPEN_START_PAGE,
        "Tools",
        (InputContext.QA, InputContext.GRADE),
    ),
    CommandBindingSpec(
        "f3",
        InputCommand.OPEN_START_PAGE,
        "Back to grading",
        (InputContext.SETUP,),
        requires_open_session=True,
    ),
    CommandBindingSpec(
        "t",
        InputCommand.OPEN_FORMAT_GUIDE,
        "Templates",
        (InputContext.SETUP,),
    ),
    CommandBindingSpec(
        "question_mark",
        InputCommand.APP_HELP,
        "Help",
        (InputContext.GLOBAL,),
        key_display="?",
    ),
    CommandBindingSpec(
        "escape",
        InputCommand.APP_BACK,
        "Back",
        (InputContext.GRADE,),
    ),
    CommandBindingSpec("/", InputCommand.QA_FILTER, "Filter", (InputContext.QA,)),
    # Both picker panes need bindings for Textual to display their shortcuts.
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
        "t",
        InputCommand.APP_TOGGLE_TEACHER_MODE,
        "Teacher Mode",
        (InputContext.QA, InputContext.GRADE),
        requires_unlocked_anonymity=True,
    ),
    CommandBindingSpec(
        "z",
        InputCommand.UNDO_LAST_GRADE,
        "Undo Grade",
        (InputContext.QA, InputContext.GRADE),
    ),
    CommandBindingSpec(
        "R",
        InputCommand.OPEN_REVIEW_QUEUE,
        "Review",
        (InputContext.QA, InputContext.GRADE),
    ),
    CommandBindingSpec(
        "H",
        InputCommand.OPEN_GRADE_HISTORY,
        "History",
        (InputContext.QA, InputContext.GRADE),
        requires_teacher_mode=True,
    ),
    CommandBindingSpec(
        "V",
        InputCommand.OPEN_GRADING_SESSION,
        "Sessions",
        (InputContext.QA, InputContext.GRADE),
        requires_teacher_mode=True,
    ),
    CommandBindingSpec(
        "I",
        InputCommand.OPEN_GRADER_AGREEMENT,
        "Graders",
        (InputContext.QA, InputContext.GRADE),
        requires_teacher_mode=True,
    ),
    CommandBindingSpec(
        "C",
        InputCommand.OPEN_CONSISTENCY_AUDIT,
        "Consistency",
        (InputContext.QA, InputContext.GRADE),
    ),
    CommandBindingSpec(
        "P",
        InputCommand.OPEN_SIMILARITY_AUDIT,
        "Similarity",
        (InputContext.QA, InputContext.GRADE),
    ),
    CommandBindingSpec(
        "G",
        InputCommand.OPEN_REGRADE_QUEUE,
        "Regrades",
        (InputContext.QA, InputContext.GRADE),
    ),
    CommandBindingSpec(
        "F",
        InputCommand.FILE_REGRADE,
        "File Regrade",
        (InputContext.QA, InputContext.GRADE),
        requires_teacher_mode=True,
    ),
    CommandBindingSpec(
        "D",
        InputCommand.OPEN_WORK_CLAIMS,
        "Work Claims",
        (InputContext.QA, InputContext.GRADE),
    ),
    # For a paper the splitter cut in two: join it with the submission
    # scanned just before or after it, read it again, keep its grades.
    CommandBindingSpec(
        "X",
        InputCommand.JOIN_SPLIT_SUBMISSIONS,
        "Join split",
        (InputContext.GRADE,),
    ),
    # A workspace shared with other accounts: changes graders asked for to
    # grades someone else gave, for an instructor to approve or dismiss.
    CommandBindingSpec(
        "Q",
        InputCommand.OPEN_CHANGE_REQUESTS,
        "Requests",
        (InputContext.QA, InputContext.GRADE),
    ),
    CommandBindingSpec(
        "S",
        InputCommand.OPEN_ITEM_ANALYSIS,
        "Stats",
        (InputContext.QA, InputContext.GRADE),
    ),
    CommandBindingSpec(
        "A",
        InputCommand.OPEN_LLM_EVAL,
        "AI Check",
        (InputContext.QA, InputContext.GRADE),
    ),
    CommandBindingSpec(
        "E",
        InputCommand.OPEN_GRADER_PACKET,
        "Grader copy",
        (InputContext.QA, InputContext.GRADE),
    ),
    CommandBindingSpec(
        "M",
        InputCommand.OPEN_MERGE,
        "Merge",
        (InputContext.QA, InputContext.GRADE),
    ),
    CommandBindingSpec(
        "B",
        InputCommand.OPEN_BACKUP,
        "Backup",
        (InputContext.QA, InputContext.GRADE),
    ),
    CommandBindingSpec(
        "L",
        InputCommand.OPEN_RELEASE,
        "Release",
        (InputContext.QA, InputContext.GRADE),
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
        "j,down",
        InputCommand.GRADE_NEXT_QUESTION,
        "Next Question",
        (InputContext.GRADE,),
        key_display="j/down",
    ),
    CommandBindingSpec(
        "k,up",
        InputCommand.GRADE_PREVIOUS_QUESTION,
        "Prev Question",
        (InputContext.GRADE,),
        key_display="k/up",
    ),
    CommandBindingSpec(
        "ctrl+j,right",
        InputCommand.GRADE_NEXT_STUDENT,
        "Next Student",
        (InputContext.GRADE,),
        key_display="ctrl+j/right",
    ),
    CommandBindingSpec(
        "ctrl+k,left",
        InputCommand.GRADE_PREVIOUS_STUDENT,
        "Prev Student",
        (InputContext.GRADE,),
        key_display="ctrl+k/left",
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
        "e",
        InputCommand.GRADE_EDIT_RUBRIC,
        "Edit Rubric",
        (InputContext.GRADE,),
        show=False,
    ),
    CommandBindingSpec(
        "c",
        InputCommand.GRADE_FOCUS_COMMENT,
        "Comment",
        (InputContext.GRADE,),
        show=False,
    ),
    CommandBindingSpec(
        "x",
        InputCommand.GRADE_EDIT_TRANSCRIPTION,
        "Fix Transcription",
        (InputContext.GRADE,),
        show=False,
    ),
    CommandBindingSpec(
        "l",
        InputCommand.OPEN_COMMENT_LIBRARY,
        "Comment Library",
        (InputContext.GRADE,),
        show=False,
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
        "U",
        InputCommand.OPEN_UNIFY_ANSWERS,
        "Unify Answers",
        (InputContext.GRADE,),
        show=False,
    ),
    CommandBindingSpec(
        "g",
        InputCommand.GRADE_APPLY_GROUP,
        "Grade Group",
        (InputContext.GRADE,),
        show=False,
    ),
    CommandBindingSpec(
        "b",
        InputCommand.GRADE_TOGGLE_GROUP_MODE,
        "By Answer",
        (InputContext.GRADE,),
    ),
    CommandBindingSpec(
        "v",
        InputCommand.GRADE_TOGGLE_AI_REVIEW,
        "Confirm AI",
        (InputContext.GRADE,),
    ),
    CommandBindingSpec(
        "y",
        InputCommand.GRADE_CONFIRM_AI,
        "Confirm AI Grade",
        (InputContext.GRADE,),
        show=False,
    ),
    CommandBindingSpec(
        "Y",
        InputCommand.GRADE_AI_DRAFT_GRADES,
        "AI Draft Grades",
        (InputContext.GRADE,),
    ),
    CommandBindingSpec(
        "o",
        InputCommand.GRADE_OPEN_EXTERNAL,
        "Open in Viewer",
        (InputContext.GRADE,),
        show=False,
    ),
    CommandBindingSpec(
        "w",
        InputCommand.GRADE_NEXT_PAGE_VIEW,
        "Whole Page",
        (InputContext.GRADE,),
        show=False,
    ),
    CommandBindingSpec(
        "W",
        InputCommand.GRADE_PREVIOUS_PAGE_VIEW,
        "Previous Page",
        (InputContext.GRADE,),
        show=False,
    ),
    CommandBindingSpec(
        "+,=",
        InputCommand.GRADE_ZOOM_IN,
        "Zoom In",
        (InputContext.GRADE,),
        key_display="+",
        show=False,
    ),
    CommandBindingSpec(
        "-,_",
        InputCommand.GRADE_ZOOM_OUT,
        "Zoom Out",
        (InputContext.GRADE,),
        key_display="-",
        show=False,
    ),
    CommandBindingSpec(
        "0",
        InputCommand.GRADE_RESET_ZOOM,
        "Reset Zoom",
        (InputContext.GRADE,),
        show=False,
    ),
    CommandBindingSpec(
        "comma",
        InputCommand.GRADE_ROTATE_LEFT,
        "Rotate Left",
        (InputContext.GRADE,),
        key_display=",",
        show=False,
    ),
    CommandBindingSpec(
        "full_stop",
        InputCommand.GRADE_ROTATE_RIGHT,
        "Rotate Right",
        (InputContext.GRADE,),
        key_display=".",
        show=False,
    ),
    CommandBindingSpec(
        "ctrl+v",
        InputCommand.GRADE_TOGGLE_LAYOUT,
        "Toggle Layout",
        (InputContext.GRADE,),
        show=False,
    ),
    CommandBindingSpec(
        "a",
        InputCommand.GRADE_FIRST_STUDENT,
        "First Student",
        (InputContext.GRADE,),
        show=False,
    ),
)


# Shifted letters are text input; Shift+Tab moves focus.
_NON_TYPEABLE_MODIFIERS = ("ctrl+", "alt+", "super+", "cmd+", "meta+")

_FUNCTION_KEY_RE = re.compile(r"f\d{1,2}$")


def key_is_typeable(key: str) -> bool:
    stripped = key.strip()
    if not stripped:
        return False
    if any(stripped.startswith(modifier) for modifier in _NON_TYPEABLE_MODIFIERS):
        return False
    return not _FUNCTION_KEY_RE.fullmatch(stripped)


def command_survives_text_focus(command: InputCommand) -> bool:
    """A typeable alias blocks the whole command because the triggering key is unknown here."""
    keys = [
        key
        for binding in INPUT_BINDINGS
        if binding.command == command
        for key in binding.keys.split(",")
    ]
    return bool(keys) and not any(key_is_typeable(key) for key in keys)


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
            if binding.requires_teacher_mode and not self._state.teacher_mode:
                continue
            if binding.requires_unlocked_anonymity and self._state.anonymity_locked:
                continue
            if binding.requires_open_session and not self._state.session_open:
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
        if self._state.current_page == PAGE_SETUP:
            return InputContext.SETUP
        return InputContext.GLOBAL

    @staticmethod
    def _coerce_command(command: InputCommand | str) -> InputCommand:
        if isinstance(command, InputCommand):
            return command
        try:
            return InputCommand(command)
        except ValueError as exc:
            raise ValueError(f"Unknown input command: {command}") from exc
