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
    # Only available while teacher mode is on. Used for commands that reveal
    # identity/accountability data (e.g. the grade-history audit view), so they
    # stay hidden during anonymous grading.
    requires_teacher_mode: bool = False
    # Unavailable when the assignment's policy locks anonymous grading. Used for
    # the teacher-mode toggle so a grader can't de-anonymize a multi-grader
    # folder the teacher marked anonymous-locked (`core/policy.py`).
    requires_unlocked_anonymity: bool = False

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
    # Start-up page only: show the copy-paste assignment-file format templates.
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
    # Back only applies where there is somewhere to go back to; the QA
    # picker is the root screen.
    CommandBindingSpec(
        "escape",
        InputCommand.APP_BACK,
        "Back",
        (InputContext.GRADE,),
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
        "t",
        InputCommand.APP_TOGGLE_TEACHER_MODE,
        "Teacher Mode",
        (InputContext.QA, InputContext.GRADE),
        # Hidden/inert when the assignment policy locks anonymous grading, so a
        # grader can't reveal names in a multi-grader folder.
        requires_unlocked_anonymity=True,
    ),
    # Undo the last persisted grade/comment change. Available both on the grade
    # screen and on the QA picker, where the grading log lists what will revert.
    CommandBindingSpec(
        "z",
        InputCommand.UNDO_LAST_GRADE,
        "Undo Grade",
        (InputContext.QA, InputContext.GRADE),
    ),
    # ----------------------------------------------------------------------- #
    # Reports, audits and queues — every one of them on **Shift + a letter**.
    #
    # These are the "show me something about the cohort" tools, and grouping
    # them behind one modifier is what makes them learnable: a grader who knows
    # Shift opens a report only has to remember the letter, instead of eight
    # unrelated single keys competing with the grading actions. Shift (rather
    # than Ctrl) because ctrl+h/i/m *are* backspace/tab/enter in a terminal and
    # ctrl+s/q are flow control, which would have cost exactly the mnemonics
    # these tools want; an uppercase letter is layout-independent and encodable
    # in every terminal.
    #
    # The one deliberate exception is W (previous page), which belongs to the
    # w/W forward-back pair rather than to this group — the same shift-for-the-
    # reverse-direction convention vim uses for n/N.
    # ----------------------------------------------------------------------- #
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
        # The audit log names graders and students; keep it to teacher mode so
        # anonymous grading stays anonymous.
        requires_teacher_mode=True,
    ),
    # Grading-session analytics: throughput/pace per grader, from the same audit
    # journal. It names graders, so — like grade history — it's teacher-mode only.
    # Keeps its historical letter (it was `v`) rather than taking a mnemonic T,
    # which would have sat one shift away from `t` (teacher mode) meaning
    # something unrelated.
    CommandBindingSpec(
        "V",
        InputCommand.OPEN_GRADING_SESSION,
        "Sessions",
        (InputContext.QA, InputContext.GRADE),
        requires_teacher_mode=True,
    ),
    # Inter-grader agreement: leniency, drift and how any two graders compare
    # on work they both marked. I for **I**nter-rater, the standard term for
    # what it measures — and the one mnemonic in this group with no lowercase
    # partner at all, so it cannot repeat the `t`/`T` trap of sitting one shift
    # away from an unrelated command. Teacher-mode only: it names graders, the
    # same gate as grade history (H) and the session analytics (V) it sits
    # beside.
    CommandBindingSpec(
        "I",
        InputCommand.OPEN_GRADER_AGREEMENT,
        "Graders",
        (InputContext.QA, InputContext.GRADE),
        requires_teacher_mode=True,
    ),
    # Grading-consistency audit: same answer, same grade? Anonymity-safe (it
    # shows student ids, not names, outside teacher mode), so available to every
    # grader in both contexts.
    CommandBindingSpec(
        "C",
        InputCommand.OPEN_CONSISTENCY_AUDIT,
        "Consistency",
        (InputContext.QA, InputContext.GRADE),
    ),
    # Answer-similarity audit: did two students hand in the same free response?
    # Anonymity-safe like the consistency audit (student ids, not names, outside
    # teacher mode), so available to every grader in both contexts. Deliberately
    # not teacher-gated: a TA can see that "s3 and s7 match" and act on it
    # without any name being revealed.
    CommandBindingSpec(
        "P",
        InputCommand.OPEN_SIMILARITY_AUDIT,
        "Similarity",
        (InputContext.QA, InputContext.GRADE),
    ),
    # Regrade queue: student grade disputes worked as first-class items. Routes
    # to the disputed question just like the review queue, so — like it — it's
    # available to every grader in both contexts (the modal is anonymity-aware).
    CommandBindingSpec(
        "G",
        InputCommand.OPEN_REGRADE_QUEUE,
        "Regrades",
        (InputContext.QA, InputContext.GRADE),
    ),
    # File a new regrade request (grade dispute). Teacher-mode only — filing a
    # dispute names a student — so it's hidden and inert during anonymous
    # grading, like the audit views. On the grade screen it pre-fills the current
    # student/question; on the QA picker the teacher picks them.
    CommandBindingSpec(
        "F",
        InputCommand.FILE_REGRADE,
        "File Regrade",
        (InputContext.QA, InputContext.GRADE),
        requires_teacher_mode=True,
    ),
    # Item analysis: per-question difficulty, separation, and MC distractors.
    # Every figure is a cohort aggregate (no student named), so it's available
    # to every grader in both contexts, no teacher-mode gate.
    # Who is grading what (`core/claims.py`). D for **D**ivide: W would have
    # been the obvious mnemonic but it is already the w/W page-view pair on the
    # grade screen. Anonymity-safe — it names graders, not students — so no
    # teacher-mode gate; a TA has to be able to see and release their own slice.
    CommandBindingSpec(
        "D",
        InputCommand.OPEN_WORK_CLAIMS,
        "Work Claims",
        (InputContext.QA, InputContext.GRADE),
    ),
    CommandBindingSpec(
        "S",
        InputCommand.OPEN_ITEM_ANALYSIS,
        "Stats",
        (InputContext.QA, InputContext.GRADE),
    ),
    # Calibrate AI grading: measure the LLM grader against answers this grader
    # already marked, and get the --llm-min-confidence the evidence supports.
    # A for **A**I. It belongs in this group because it is a cohort-level report
    # rather than an action on the answer in front of you — but unlike the rest
    # it is the one that *takes a while*, since every measured answer is a model
    # call. Reachable from the workspace (not just the setup screen's Helper
    # Tools) because of a chicken-and-egg: there is nothing to measure until a
    # human has graded a sample, so the moment you *can* calibrate is while
    # grading. No teacher-mode gate — every figure is an aggregate about the
    # model, and the disagreement list identifies students the same way the
    # consistency and similarity audits already do.
    CommandBindingSpec(
        "A",
        InputCommand.OPEN_LLM_EVAL,
        "AI Check",
        (InputContext.QA, InputContext.GRADE),
    ),
    # The two halves of the offline handoff round trip, E for **E**xport a copy
    # and M for **M**erge one back. They are on the workspace and not only in
    # the setup screen's Helper Tools for a plain reason: launching with
    # --qa-file/--submissions — which is how anyone actually grades — opens the
    # QA picker and never shows the setup screen at all, so a tool that lives
    # only there is invisible to every real session. The same chicken-and-egg
    # as the AI check above: deciding to hand half the pile to a TA, or getting
    # it back, happens while grading, not before.
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
    # B for **B**ackup. No teacher-mode gate on any of these three: a packet
    # names no student, the merge plan is anonymity-aware like the consistency
    # and similarity audits, and an integrity report is about files.
    CommandBindingSpec(
        "B",
        InputCommand.OPEN_BACKUP,
        "Backup",
        (InputContext.QA, InputContext.GRADE),
    ),
    # Hand the assignment back, and see what has moved since you last did
    # (`core/release.py`). On the workspace for the same reason as E/M/B: a
    # session launched the way anyone grades never shows the setup screen, and
    # "am I done?" is asked at the end of a grading session, not before one.
    #
    # **L** because R for Release is taken by the review queue and every other
    # letter of the word is too. `l` (comment library) is its lowercase partner,
    # which is fine here and was not for `t`/`T`: the trap that rule exists for
    # is a mis-shift silently flipping *state* — anonymity — under the grader,
    # whereas mis-shifting `l` opens the wrong modal and escape closes it.
    #
    # No teacher-mode gate: the readiness report counts and names student *ids*
    # only, and the change list is anonymity-aware like the consistency and
    # similarity audits — a TA has to be able to see that they are not finished.
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
    # Movement, in one direction convention throughout: **j/down = forward,
    # k/up = back**, matching the QA picker's list navigation and vim. The grade
    # screen used to invert it (k was *next* question), which meant the same
    # finger moved opposite ways on the two screens.
    #
    # The axis is the modifier: a bare key moves within the student's paper
    # (question to question), Ctrl moves between students. Arrow aliases follow
    # the same split — up/down for questions, left/right for students.
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
    # Skip to the next/previous student who still needs this question graded.
    # n/p rather than the old m/n: "n = next" is the near-universal convention
    # (vim search, less, browsers), and p is its natural partner — where m for
    # "next" was arbitrary and collided with nothing memorable.
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
    # The grade screen's always-visible Hotkeys card documents the
    # commands below; keeping them out of the status bar leaves room
    # for the navigation keys that change most often.
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
    # Correct an OCR/VLM transcription in place (opens the inline editor in the
    # Question card). Documented in the Hotkeys card + help screen; kept out of
    # the status bar so the bar stays focused on navigation keys.
    CommandBindingSpec(
        "x",
        InputCommand.GRADE_EDIT_TRANSCRIPTION,
        "Fix Transcription",
        (InputContext.GRADE,),
        show=False,
    ),
    # Open the reusable comment-snippet library and insert a saved phrase into
    # the answer's comment box. Documented in the Hotkeys card + help screen;
    # kept out of the status bar so the bar stays focused on navigation keys.
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
    # Batch-apply the current grade to everyone with the same answer. Kept
    # out of the status bar (documented in the Hotkeys card and help screen)
    # so the bar stays focused on navigation keys.
    # Review the merges proposed for this question's near-identical answers,
    # and accept one to widen what `g` covers. U for **U**nify.
    #
    # GRADE context only, and that is a design constraint rather than a spare
    # key: a proposal is scoped to the question in front of you, and accepting
    # one only means something while you have a rubric selection to apply. It
    # also keeps `U` clear of the QA picker's `u` (jump to ungraded), so the
    # two never coexist in one context meaning unrelated things.
    #
    # Not in the status bar, like the other grade-screen extras — the Hotkeys
    # card and help screen document it.
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
    # Grade the answer, not the student: navigation moves between this
    # question's distinct answers instead of its students.
    #
    # `b` for grading **b**y answer. GRADE context only — the same design
    # constraint as `U` above, since an answer group is scoped to the question
    # in front of you — which also keeps it clear of the QA picker's `b`
    # (return bundle), so the two never coexist in one context.
    #
    # Shown in the status bar, unlike `g` and `U`: it is a *mode*, and a mode
    # whose key is undiscoverable is a mode nobody finds. The grade screen's
    # answer-group indicator says when it is on.
    CommandBindingSpec(
        "b",
        InputCommand.GRADE_TOGGLE_GROUP_MODE,
        "By Answer",
        (InputContext.GRADE,),
    ),
    # Confirm the AI's grades. `v` for **v**erify — and the pairing with `V`
    # (grading-session analytics) is coincidental, not a family: these are the
    # letters left, and the mode is worth a memorable one.
    #
    # Shown in the status bar for the same reason as `b`: a mode nobody can
    # find is a mode nobody uses, and this one exists precisely because the
    # work it does had no fast path before. GRADE context only, since it is
    # scoped to the question in front of you.
    CommandBindingSpec(
        "v",
        InputCommand.GRADE_TOGGLE_AI_REVIEW,
        "Confirm AI",
        (InputContext.GRADE,),
    ),
    # The one key that acts. Hidden from the status bar (like `g`), since it is
    # an action rather than a mode and the mode's own indicator names it.
    # Works outside the mode too: a grader who lands on an AI grade while
    # marking normally can accept it without switching modes first.
    CommandBindingSpec(
        "y",
        InputCommand.GRADE_CONFIRM_AI,
        "Confirm AI Grade",
        (InputContext.GRADE,),
        show=False,
    ),
    # Escape hatch out of the terminal: open the answer crop in the OS image
    # viewer. Essential on terminals with no graphics protocol (where the
    # in-terminal preview is only colour blocks), useful everywhere else for a
    # full-resolution look. Documented in the Hotkeys card + help screen.
    CommandBindingSpec(
        "o",
        InputCommand.GRADE_OPEN_EXTERNAL,
        "Open in Viewer",
        (InputContext.GRADE,),
        show=False,
    ),
    # Whole-page context: step the response pane through the page(s) this answer
    # was cropped from and then the submission's extra work pages, so an answer
    # continued on the back — or on a scratch sheet — is one keypress away
    # instead of invisible. One key forward (w), shift for back (W); the whole
    # sequence wraps back to the crop. Documented in the Hotkeys card + help
    # screen, kept out of the status bar like the other grade-screen extras.
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
    # Turn the displayed scan a quarter turn — for the answer a student wrote
    # sideways up the margin, and for the occasional page the scan pre-flight
    # leaves alone because it was too sparse for OSD to orient. Display only:
    # nothing on disk changes, and the rotation clears with the student.
    # ``,``/``.`` are the unshifted keys under ``<``/``>``, the conventional
    # rotate pair, and both are free in the grade context ([ and ] already
    # resize the sidebar). Deliberately *not* in the Input-focus bypass list
    # below, so a comma typed into the comment box stays a comma.
    #
    # Spelled as Textual's Unicode key *names* rather than the literal
    # characters: Textual splits a binding string on commas, so a literal ","
    # is unbindable (it parses as two empty keys). Same reason ``?`` is bound
    # as ``question_mark`` above.
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


# Modifier prefixes that make a key impossible to type into a text box. Shift is
# deliberately absent: shift+letter *is* a typeable uppercase letter, and
# shift+tab is how you move focus backwards out of a field.
_NON_TYPEABLE_MODIFIERS = ("ctrl+", "alt+", "super+", "cmd+", "meta+")

_FUNCTION_KEY_RE = re.compile(r"f\d{1,2}$")


def key_is_typeable(key: str) -> bool:
    """Whether a key press could be meant for a focused text box.

    True for anything a person can type *and* for the keys a text box needs to
    edit with — arrows, home/end, backspace, delete, tab, escape. Only a
    modifier chord or a function key is genuinely unavailable to a text field.
    """
    stripped = key.strip()
    if not stripped:
        return False
    if any(stripped.startswith(modifier) for modifier in _NON_TYPEABLE_MODIFIERS):
        return False
    return not _FUNCTION_KEY_RE.fullmatch(stripped)


def command_survives_text_focus(command: InputCommand) -> bool:
    """Whether a command may still fire while a text box holds focus.

    Only when **every** key it is bound to is unavailable to a text field. The
    rule is derived from the binding table rather than kept as a hand-written
    allow-list, because the hand-written version is what broke: it had drifted
    into two divergent copies and had listed printable keys (``a``, ``j``,
    ``k``, ``n``, ``m``, ``0``, ``[``, ``+``) plus the arrow keys, so typing
    "a" in a comment jumped to the first student and pressing ← to fix a typo
    jumped to the previous one. Deriving it means a new binding can never
    reintroduce that class of bug: add a printable key and it is blocked while
    typing, automatically.

    A command bound to a chord *and* a typeable alias (next student is
    ``ctrl+j`` or ``→``) does not survive: we are asked about the command, not
    the key that triggered it, so the safe answer is the one that keeps the
    arrow free for the cursor.
    """
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
