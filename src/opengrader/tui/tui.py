from collections.abc import Callable
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import ContentSwitcher, Input, TextArea

from opengrader.core.claims import ClaimConflictError
from opengrader.core.policy import read_policy
from opengrader.core.submissions.watch import ExtractionWatcher
from opengrader.tui.controllers import GradeController, InputController
from opengrader.tui.controllers.input_controller import (
    CommandBindingSpec,
    InputCommand,
    command_survives_text_focus,
)
from opengrader.tui.data import Provider
from opengrader.tui.data.submission_files_provider import SubmissionFilesProvider
from opengrader.tui.data.stub_data_provider import StubDataProvider
from opengrader.tui.formatting import format_question_label
from opengrader.tui.state import (
    PAGE_GRADE,
    PAGE_QA_PICKER,
    PAGE_SETUP,
    TuiWorkflowState,
)
from opengrader.tui.terminal_graphics import active_graphics
from opengrader.tui.ui.screens import (
    AssignIdentityScreen,
    BackupScreen,
    ClaimAction,
    ClaimScreen,
    CommentLibraryScreen,
    ConsistencyScreen,
    FileRegradeResult,
    FileRegradeScreen,
    FormatGuideScreen,
    GradeHistoryScreen,
    GraderPacketScreen,
    GradeScreen,
    GradingSessionScreen,
    HelpScreen,
    ItemAnalysisScreen,
    LlmEvalScreen,
    MergeScreen,
    QAPickerScreen,
    RegradeChoice,
    RegradeQueueScreen,
    ReleaseScreen,
    ReviewQueueScreen,
    SetupScreen,
    GraderAgreementScreen,
    SimilarityScreen,
    UnifyAnswersScreen,
)
from opengrader.tui.ui.widgets import StatusBar, StatusBarBinding
from opengrader.tui.ui.widgets.grading_log_pane import GradingLogPane

# How often the background extraction fold-in checks the manifest for newly
# completed submissions (`_poll_extraction`). A few seconds is frequent enough
# that a grader who just finished a student sees new arrivals promptly, and
# infrequent enough that polling a folder nobody is extracting into anymore
# costs nothing worth measuring before `_poll_extraction` notices and stops it.
_EXTRACTION_POLL_SECONDS = 3.0

# How long the manifest must go completely untouched before a watch that has no
# authoritative answer gives up for good. Much longer than
# `watch._ACTIVE_RECENCY_SECONDS`, which only decides what the status line says:
# mislabelling a finished run costs a stale sentence, whereas stopping the loop
# early strands every submission still to come (see
# `_stop_extraction_watch_if_finished`). Ten minutes of total silence means the
# run is over or was abandoned; until then the watch costs one `stat()` a tick.
_EXTRACTION_GIVE_UP_SECONDS = 600.0


class TuiApp(App):
    CSS_PATH = Path(__file__).parent / "tui.tcss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = InputController.all_bindings()  # pyright: ignore[reportAssignmentType]

    DEFAULT_CSS = """
    Screen {
        padding: 0;
        margin: 0;
    }

    #workspace-switcher {
        height: 1fr;
        width: 100%;
        padding: 0;
        margin: 0;
    }

    .hidden {
        display: none;
    }

    #setup {
        width: 100%;
        height: 100%;
        background: $background;
    }

    #setup-container {
        width: 100%;
        height: 100%;
        scrollbar-gutter: stable;
    }
    """

    def __init__(
        self,
        *,
        default_image_path: str | None = None,
        data_provider: Provider | None = None,
        qa_file_path: str | Path | None = None,
        submissions_path: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.default_image_path = default_image_path or str(Path("images/Grading.jpg"))
        self.workflow_state = TuiWorkflowState()
        self.data_provider = data_provider or self._build_data_provider(
            qa_file_path=qa_file_path,
            submissions_path=submissions_path,
        )
        if isinstance(self.data_provider, StubDataProvider):
            # The app opens on the setup page; track it so page-scoped
            # hotkeys (and the status bar) don't act as if the QA picker
            # were showing.
            self.workflow_state.current_page = PAGE_SETUP
        self.grade_controller = GradeController(
            self,
            self.workflow_state,
            self.data_provider,
        )
        self.input_controller = InputController(self.workflow_state)
        self._register_input_handlers()

    def compose(self) -> ComposeResult:
        initial_page = PAGE_SETUP if isinstance(self.data_provider, StubDataProvider) else PAGE_QA_PICKER
        with ContentSwitcher(initial=initial_page, id="workspace-switcher"):
            yield SetupScreen(id=PAGE_SETUP)
            yield QAPickerScreen(id=PAGE_QA_PICKER)
            yield GradeScreen(self.default_image_path, id=PAGE_GRADE)
        yield StatusBar(id="app-status-bar")

    def on_mount(self) -> None:
        # Only auto-load the queue if we aren't using the setup stub
        if not isinstance(self.data_provider, StubDataProvider):
            self._apply_grading_policy()
            self._load_work_claims()
            self._load_grading_queue()
            # A folder passed straight in (CLI launch, or a restarted app
            # reopening where it left off) may belong to an extraction this
            # process did not start — no certainty is available, so this
            # falls back to the manifest-recency guess, same as the setup
            # screen's plain "Launch Grading" path.
            submissions_path = getattr(self.data_provider, "submissions_path", None)
            if submissions_path is not None:
                self._start_extraction_watch(submissions_path, known_active=None)
        self.refresh_bindings()
        self._announce_graphics_capability()

    def _announce_graphics_capability(self) -> None:
        """Tell the grader up front when crops won't render in the terminal.

        Without this the app looks broken rather than degraded: the scan area
        shows colour mush and there is no hint that a working escape hatch
        exists. Said once, at startup, with the fix in the same breath.
        """
        capability = active_graphics()
        if capability.pixel_accurate:
            return
        where = capability.terminal or "This terminal"
        self.notify(
            f"{where} can't display images, so answer crops show as a "
            "low-fidelity preview. Press 'o' while grading to open a crop in "
            "your image viewer. Run 'tui --check-graphics' for details.",
            title="Terminal graphics unavailable",
            severity="warning",
            timeout=12,
        )

    def switch_to(self, page_id: str) -> None:
        self.workflow_state.current_page = page_id
        self.query_one(ContentSwitcher).current = page_id
        self.refresh_bindings()

    def get_grade_screen(self) -> GradeScreen:
        return self.query_one(f"#{PAGE_GRADE}", GradeScreen)

    def get_qa_picker_screen(self) -> QAPickerScreen:
        return self.query_one(f"#{PAGE_QA_PICKER}", QAPickerScreen)

    def get_setup_screen(self) -> SetupScreen:
        return self.query_one(f"#{PAGE_SETUP}", SetupScreen)

    def action_dispatch_input(self, command: str) -> None:
        if not self._input_command_available(command):
            return
        self.input_controller.dispatch(command)

    def refresh_bindings(self) -> None:
        super().refresh_bindings()
        self._refresh_status_bar_bindings()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action != "dispatch_input":
            return True

        if len(parameters) != 1:
            return False
        command = parameters[0]
        if not isinstance(command, str):
            return False

        # The transcription editor is a multi-line TextArea (not an Input), so
        # every printable key — including the letters bound to app hotkeys —
        # must reach it while it holds focus.
        if isinstance(self.focused, TextArea):
            return False

        if isinstance(self.focused, Input):
            # A focused text box owns every key it could possibly receive. Which
            # commands survive that is derived from the binding table (see
            # `command_survives_text_focus`) rather than listed here, because the
            # list this replaced had drifted into two divergent copies and let
            # printable keys through: typing "a" in a comment jumped to the first
            # student, and ← jumped to the previous one instead of moving the
            # cursor.
            if not command_survives_text_focus(command):
                return False

        if len(self.screen_stack) > 1:
            return False

        return self._input_command_available(command)

    def on_qa_picker_screen_qa_highlighted(
        self, event: QAPickerScreen.QAHighlighted
    ) -> None:
        self.grade_controller.on_qa_highlighted(event.item)

    def on_qa_picker_screen_qa_selected(self, event: QAPickerScreen.QASelected) -> None:
        self.grade_controller.on_qa_selected(event.item)

    def on_grading_log_pane_entry_selected(
        self, event: GradingLogPane.EntrySelected
    ) -> None:
        self.grade_controller.on_grading_log_entry_selected(event.entry)

    def on_status_bar_search_changed(self, event: StatusBar.SearchChanged) -> None:
        if self.workflow_state.current_page != PAGE_QA_PICKER:
            return
        status = self.get_qa_picker_screen().update_filter(event.value)
        self.query_one(StatusBar).update_search_status(status)

    def on_status_bar_search_submitted(
        self, _event: StatusBar.SearchSubmitted
    ) -> None:
        self._finish_qa_filter()

    def on_status_bar_search_cancelled(
        self, _event: StatusBar.SearchCancelled
    ) -> None:
        self._finish_qa_filter()

    def on_grade_screen_rubric_selected(
        self, event: GradeScreen.RubricSelected
    ) -> None:
        self._handle_rubric_selection(event)

    def on_grade_rubric_selected(self, event: GradeScreen.RubricSelected) -> None:
        """ID-based handler fallback for the grade screen."""
        self._handle_rubric_selection(event)

    def _handle_rubric_selection(self, event: GradeScreen.RubricSelected) -> None:
        self.grade_controller.on_rubric_selected(
            event.option_index, finalize=event.finalize
        )

    def on_grade_screen_comment_updated(
        self, event: GradeScreen.CommentUpdated
    ) -> None:
        self.grade_controller.on_comment_updated(event.comment)

    def on_grade_screen_comment_committed(
        self, _event: GradeScreen.CommentCommitted
    ) -> None:
        self.grade_controller.commit_pending_comment()

    def on_grade_screen_transcription_updated(
        self, event: GradeScreen.TranscriptionUpdated
    ) -> None:
        self.grade_controller.on_transcription_updated(event.response_text)

    def on_grade_screen_return_to_home_requested(
        self, _event: GradeScreen.ReturnToHomeRequested
    ) -> None:
        self.grade_controller.return_to_qa_picker()

    def on_setup_screen_launch_requested(self, event: SetupScreen.LaunchRequested) -> None:
        """Re-initialize the app with real data from the setup screen."""
        try:
            # 1. Reset the workflow state to clear any stale stub data
            self.workflow_state = TuiWorkflowState()

            # 2. Initialize the real data provider and controller
            self.data_provider = SubmissionFilesProvider(
                qa_file_path=event.qa_path,
                submissions_path=event.submissions_path,
                default_image_path=self.default_image_path,
            )
            self.grade_controller = GradeController(
                self, self.workflow_state, self.data_provider
            )

            # 3. Re-register input handlers to link them to the new controller
            self.input_controller = InputController(self.workflow_state)
            self._register_input_handlers()

            # 4. Apply the folder's anonymity policy, load the queue, transition
            self._apply_grading_policy()
            self._load_grading_queue()
            self.switch_to(PAGE_QA_PICKER)
            # This folder may belong to someone else's still-running extraction
            # (or one this app abandoned and reopened) — there is no certainty
            # here the way there is for the button below, so the watcher falls
            # back to its manifest-recency guess (`known_active=None`).
            self._start_extraction_watch(event.submissions_path, known_active=None)
            self.notify("TUI Loaded Successfully")
        except Exception as e:
            self.notify(f"Error loading files: {e}", severity="error")

    def on_setup_screen_grade_while_extracting_requested(
        self, event: SetupScreen.GradeWhileExtractingRequested
    ) -> None:
        """Open the workspace on a folder this screen's own extraction worker
        is still writing into.

        Mirrors ``on_setup_screen_launch_requested`` almost exactly — the one
        real difference is *why* the fold-in watcher trusts that extraction is
        live: the setup screen just started this run in the same process, so
        that certainty is threaded straight through instead of falling back
        to the manifest-recency guess (see ``core/submissions/watch.py``).
        If nothing has finished extracting yet, ``SubmissionFilesProvider``
        raises with a message that says so — see its ``_empty_folder_message``
        — rather than the plain "no submissions found" a bad path gets.
        """
        try:
            self.workflow_state = TuiWorkflowState()
            self.data_provider = SubmissionFilesProvider(
                qa_file_path=event.qa_path,
                submissions_path=event.submissions_path,
                default_image_path=self.default_image_path,
            )
            self.grade_controller = GradeController(
                self, self.workflow_state, self.data_provider
            )
            self.input_controller = InputController(self.workflow_state)
            self._register_input_handlers()
            self._apply_grading_policy()
            self._load_grading_queue()
            self.switch_to(PAGE_QA_PICKER)
            self._start_extraction_watch(
                event.submissions_path,
                known_active=self._setup_screen_extraction_active,
            )
            self.notify(
                "Grading workspace opened — extraction is still running in "
                "the background. Submissions will appear as they finish."
            )
        except Exception as e:
            self.notify(f"Error loading files: {e}", severity="error")

    def _setup_screen_extraction_active(self) -> bool:
        """Whether the setup screen's own extractor worker is still running.

        Read fresh on every poll (not captured once at launch) since the
        worker can finish at any time — the moment it does, the watcher
        should stop trusting "active" rather than keep believing it for
        another `_ACTIVE_RECENCY_SECONDS` of manifest-recency guesswork.
        Falls back to ``False`` if the setup screen is ever unreachable: with
        no certainty available, "assume finished" is the safer wrong guess —
        it stops qualifying messages instead of qualifying them forever.
        """
        try:
            return self.get_setup_screen().extraction_in_progress
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # Background extraction fold-in (see `core/submissions/watch.py` and
    # `GradeController`'s "Background extraction fold-in" section)
    # ------------------------------------------------------------------ #

    def _start_extraction_watch(
        self,
        submissions_path: "str | Path",
        *,
        known_active: "Callable[[], bool] | None",
    ) -> None:
        """Begin polling ``submissions_path`` for newly-completed submissions.

        A plain Textual timer, not a worker thread: `ExtractionWatcher.poll`
        only stats/reads files and is cheap (see its docstring), and running
        it on the UI thread means the provider/controller calls it triggers
        need no `call_from_thread` marshalling. Replaces any previous watch
        rather than stacking timers, so re-launching from the setup screen
        never leaves an old poll running against an abandoned folder.
        """
        self._stop_extraction_watch()
        self._extraction_watcher = ExtractionWatcher(submissions_path)
        self._extraction_known_active = known_active
        self._extraction_watch_timer = self.set_interval(
            _EXTRACTION_POLL_SECONDS, self._poll_extraction
        )

    def _stop_extraction_watch(self) -> None:
        timer = getattr(self, "_extraction_watch_timer", None)
        if timer is not None:
            timer.stop()
        self._extraction_watch_timer = None
        self._extraction_watcher = None
        self._extraction_known_active = None

    def _poll_extraction(self) -> None:
        """One tick of the fold-in loop: ask the watcher, tell the provider,
        tell the controller.

        Kept intentionally dumb — all three of "what's new", "load it" and
        "what does the grader see about it" are owned by the objects that
        already know how to do them (`ExtractionWatcher`,
        `SubmissionFilesProvider.load_new_submissions`,
        `GradeController.note_extraction_progress`); this just wires them
        together on a timer.
        """
        watcher = getattr(self, "_extraction_watcher", None)
        load_new = getattr(self.data_provider, "load_new_submissions", None)
        if watcher is None or not callable(load_new):
            self._stop_extraction_watch()
            return

        known_active_probe = getattr(self, "_extraction_known_active", None)
        known_active = known_active_probe() if known_active_probe is not None else None
        result = watcher.poll(known_active=known_active)

        added = 0
        skipped = 0
        if result.new_keys:
            try:
                load_result = load_new(result.new_keys)
                added = len(load_result.added)
                skipped = len(load_result.skipped)
            except Exception as exc:  # a folder-level failure, not a per-file one
                self.notify(f"Could not load new submissions: {exc}", severity="error")

        self.grade_controller.note_extraction_progress(
            added=added, appears_active=result.appears_active
        )
        if added:
            noun = "submission" if added == 1 else "submissions"
            self.notify(f"{added} more {noun} finished extracting.")
        if skipped:
            noun = "submission" if skipped == 1 else "submissions"
            self.notify(
                f"{skipped} newly-extracted {noun} skipped (question set "
                "doesn't match this assignment).",
                severity="warning",
            )

        self._stop_extraction_watch_if_finished(
            known_active=known_active, idle_seconds=result.idle_seconds
        )

    def _stop_extraction_watch_if_finished(
        self, *, known_active: bool | None, idle_seconds: float | None
    ) -> None:
        """End the fold-in loop, but only on evidence strong enough to justify it.

        Stopping is a one-way door: nothing restarts the watch, so a watch that
        ends while extraction is merely *between* two submissions strands every
        submission still to come — and, worse, lets the "you're done" messages
        go back to being unqualified while the cohort is still filling in. That
        is precisely the failure this whole feature exists to prevent, so the
        bar for stopping is deliberately much higher than the bar for saying
        "extraction still running" in the status line.

        Two things clear it. A caller with **certainty** (the setup screen's own
        extractor worker reporting it has finished) is authoritative and stops
        the loop at once. Otherwise only a *sustained* silence counts —
        `_EXTRACTION_GIVE_UP_SECONDS` of nothing written at all, far longer than
        any single submission takes even through a vision model. Until then the
        cost of being wrong is one `stat()` every few seconds, which is nothing
        next to silently losing half a cohort.
        """
        if known_active is False:
            self._stop_extraction_watch()
            return
        if known_active is None and (
            idle_seconds is None or idle_seconds >= _EXTRACTION_GIVE_UP_SECONDS
        ):
            self._stop_extraction_watch()

    def _register_input_handlers(self) -> None:
        self.input_controller.register(InputCommand.APP_QUIT, self.exit)
        self.input_controller.register(InputCommand.APP_HELP, self._show_help)
        self.input_controller.register(InputCommand.APP_BACK, self._handle_back)
        self.input_controller.register(InputCommand.QA_FILTER, self._handle_qa_filter)
        self.input_controller.register(
            InputCommand.QA_JUMP_UNGRADED,
            self.grade_controller.qa_jump_ungraded_question,
        )
        self.input_controller.register(
            InputCommand.QA_MOVE_UP,
            self._handle_qa_move_up,
        )
        self.input_controller.register(
            InputCommand.QA_MOVE_DOWN,
            self._handle_qa_move_down,
        )
        self.input_controller.register(
            InputCommand.QA_MOVE_STUDENT_UP,
            self._handle_student_move_up,
        )
        self.input_controller.register(
            InputCommand.QA_MOVE_STUDENT_DOWN,
            self._handle_student_move_down,
        )
        self.input_controller.register(
            InputCommand.QA_SWITCH_PANE,
            self._handle_qa_switch_pane,
        )
        self.input_controller.register(
            InputCommand.QA_TOGGLE_STUDENT_DISPLAY,
            lambda: self.get_qa_picker_screen().toggle_student_display_mode(),
        )
        self.input_controller.register(
            InputCommand.QA_CONFIRM,
            self.grade_controller.qa_confirm,
        )
        self.input_controller.register(
            InputCommand.GRADE_NEXT_STUDENT,
            self.grade_controller.next_student,
        )
        self.input_controller.register(
            InputCommand.GRADE_PREVIOUS_STUDENT,
            self.grade_controller.previous_student,
        )
        self.input_controller.register(
            InputCommand.GRADE_NEXT_UNGRADED_STUDENT,
            self.grade_controller.next_ungraded_student,
        )
        self.input_controller.register(
            InputCommand.GRADE_PREVIOUS_UNGRADED_STUDENT,
            self.grade_controller.previous_ungraded_student,
        )
        self.input_controller.register(
            InputCommand.GRADE_NEXT_QUESTION,
            self.grade_controller.next_question,
        )
        self.input_controller.register(
            InputCommand.GRADE_PREVIOUS_QUESTION,
            self.grade_controller.previous_question,
        )
        self.input_controller.register(
            InputCommand.GRADE_FINALIZE_STUDENT,
            self.grade_controller.finalize_student,
        )
        self.input_controller.register(
            InputCommand.GRADE_APPLY_GROUP,
            self.grade_controller.apply_grade_to_answer_group,
        )
        self.input_controller.register(
            InputCommand.GRADE_TOGGLE_GROUP_MODE,
            self.grade_controller.toggle_group_mode,
        )
        self.input_controller.register(
            InputCommand.GRADE_TOGGLE_AI_REVIEW,
            self.grade_controller.toggle_ai_review_mode,
        )
        self.input_controller.register(
            InputCommand.GRADE_CONFIRM_AI,
            self.grade_controller.confirm_ai_grade,
        )
        self.input_controller.register(
            InputCommand.GRADE_EDIT_RUBRIC,
            self.grade_controller.open_rubric_editor,
        )
        self.input_controller.register(
            InputCommand.GRADE_FOCUS_COMMENT,
            lambda: self.get_grade_screen().focus_question_comment(),
        )
        self.input_controller.register(
            InputCommand.GRADE_EDIT_TRANSCRIPTION,
            lambda: self.get_grade_screen().toggle_transcription_editor(),
        )
        self.input_controller.register(
            InputCommand.GRADE_SHRINK_SIDEBAR,
            self._handle_grade_shrink_sidebar,
        )
        self.input_controller.register(
            InputCommand.GRADE_WIDEN_SIDEBAR,
            self._handle_grade_widen_sidebar,
        )
        self.input_controller.register(
            InputCommand.GRADE_OPEN_EXTERNAL,
            self.grade_controller.grade_open_external,
        )
        self.input_controller.register(
            InputCommand.GRADE_NEXT_PAGE_VIEW,
            lambda: self.get_grade_screen().cycle_page_view(1),
        )
        self.input_controller.register(
            InputCommand.GRADE_PREVIOUS_PAGE_VIEW,
            lambda: self.get_grade_screen().cycle_page_view(-1),
        )
        self.input_controller.register(
            InputCommand.GRADE_ZOOM_IN,
            self.grade_controller.grade_zoom_in,
        )
        self.input_controller.register(
            InputCommand.GRADE_ZOOM_OUT,
            self.grade_controller.grade_zoom_out,
        )
        self.input_controller.register(
            InputCommand.GRADE_RESET_ZOOM,
            self.grade_controller.grade_reset_zoom,
        )
        # Pure view state, like the page view above — straight to the screen
        # rather than through the grade controller, which owns grading truth.
        self.input_controller.register(
            InputCommand.GRADE_ROTATE_LEFT,
            lambda: self.get_grade_screen().rotate_response(-90),
        )
        self.input_controller.register(
            InputCommand.GRADE_ROTATE_RIGHT,
            lambda: self.get_grade_screen().rotate_response(90),
        )
        self.input_controller.register(
            InputCommand.GRADE_TOGGLE_LAYOUT,
            self.grade_controller.grade_toggle_layout,
        )
        self.input_controller.register(
            InputCommand.GRADE_FIRST_STUDENT,
            self.grade_controller.first_student_for_question,
        )
        self.input_controller.register(
            InputCommand.OPEN_WORK_CLAIMS,
            self._show_claims,
        )
        self.input_controller.register(
            InputCommand.APP_TOGGLE_TEACHER_MODE,
            self._toggle_teacher_mode,
        )
        self.input_controller.register(
            InputCommand.UNDO_LAST_GRADE,
            self.grade_controller.undo_last_grade_change,
        )
        self.input_controller.register(
            InputCommand.OPEN_REVIEW_QUEUE,
            self._show_review_queue,
        )
        self.input_controller.register(
            InputCommand.OPEN_GRADE_HISTORY,
            self._show_grade_history,
        )
        self.input_controller.register(
            InputCommand.OPEN_GRADING_SESSION,
            self._show_grading_session,
        )
        self.input_controller.register(
            InputCommand.OPEN_CONSISTENCY_AUDIT,
            self._show_consistency_audit,
        )
        self.input_controller.register(
            InputCommand.OPEN_SIMILARITY_AUDIT,
            self._show_similarity_audit,
        )
        self.input_controller.register(
            InputCommand.OPEN_UNIFY_ANSWERS,
            self._show_unify_answers,
        )
        self.input_controller.register(
            InputCommand.OPEN_GRADER_AGREEMENT,
            self._show_grader_agreement,
        )
        self.input_controller.register(
            InputCommand.OPEN_REGRADE_QUEUE,
            self._show_regrade_queue,
        )
        self.input_controller.register(
            InputCommand.FILE_REGRADE,
            self._show_file_regrade,
        )
        self.input_controller.register(
            InputCommand.OPEN_ITEM_ANALYSIS,
            self._show_item_analysis,
        )
        self.input_controller.register(
            InputCommand.OPEN_LLM_EVAL,
            self._show_llm_eval,
        )
        self.input_controller.register(
            InputCommand.OPEN_GRADER_PACKET,
            self._show_grader_packet,
        )
        self.input_controller.register(
            InputCommand.OPEN_MERGE,
            self._show_merge,
        )
        self.input_controller.register(
            InputCommand.OPEN_BACKUP,
            self._show_backup,
        )
        self.input_controller.register(
            InputCommand.OPEN_RELEASE,
            self._show_release,
        )
        self.input_controller.register(
            InputCommand.OPEN_COMMENT_LIBRARY,
            self._show_comment_library,
        )
        self.input_controller.register(
            InputCommand.OPEN_FORMAT_GUIDE,
            self._show_format_guide,
        )

    def _show_help(self) -> None:
        self.push_screen(HelpScreen())

    def _show_format_guide(self) -> None:
        """Start-up page: show the assignment-file format + copy-paste templates."""
        self.push_screen(FormatGuideScreen())

    def _show_review_queue(self) -> None:
        """Open the needs-review triage modal; jump to a chosen flag on close."""
        build_items = getattr(self.data_provider, "build_review_items", None)
        items = build_items() if callable(build_items) else []
        self.push_screen(
            ReviewQueueScreen(items, teacher_mode=self.workflow_state.teacher_mode),
            self._on_review_target_chosen,
        )

    def _on_review_target_chosen(self, item: object) -> None:
        if item is None:
            return
        # A missing/suspect id resolves in place (pick the student) rather than
        # jumping to the grade screen, which has no id entry.
        if getattr(item, "is_identity_flag", False) and self._can_assign_identity():
            self._resolve_identity(item)
            return
        student_item_id = getattr(item, "student_item_id", "")
        question_id = getattr(item, "question_id", "")
        if student_item_id:
            self.grade_controller.open_review_target(student_item_id, question_id)

    def _can_assign_identity(self) -> bool:
        return callable(getattr(self.data_provider, "assign_student_identity", None))

    def _resolve_identity(self, item: object) -> None:
        """Open the id picker for a duplicate-name/missing-id flag, then persist."""

        def _after(result: object) -> None:
            if result is not None:
                student_id, student_name = result
                try:
                    self.data_provider.assign_student_identity(
                        student_item_id=getattr(item, "student_item_id", ""),
                        student_id=student_id,
                        student_name=student_name,
                    )
                    self.notify(f"Assigned student ID {student_id}.")
                except Exception as exc:  # surface a save failure, keep triaging
                    self.notify(f"Could not assign ID: {exc}", severity="error")
            # Return to the (now refreshed) review queue to keep triaging.
            self._show_review_queue()

        self.push_screen(AssignIdentityScreen(item), _after)

    def _toggle_teacher_mode(self) -> None:
        # Refresh bindings so teacher-mode-only commands (grade history)
        # appear in / vanish from the status bar as the mode flips.
        self.grade_controller.toggle_teacher_mode()
        self.refresh_bindings()

    def _show_grade_history(self) -> None:
        """Open the durable audit-history modal (read-only)."""
        read_history = getattr(self.data_provider, "read_grade_history", None)
        entries = read_history() if callable(read_history) else []
        self.push_screen(
            GradeHistoryScreen(
                entries, teacher_mode=self.workflow_state.teacher_mode
            )
        )

    def _show_grading_session(self) -> None:
        """Open the grading-session analytics modal (read-only, teacher mode)."""
        build_report = getattr(
            self.data_provider, "build_grading_session_report", None
        )
        if not callable(build_report):
            return
        self.push_screen(GradingSessionScreen(build_report()))

    def _show_consistency_audit(self) -> None:
        """Open the grading-consistency audit; jump to a chosen student on close."""
        build_report = getattr(self.data_provider, "build_consistency_report", None)
        items = build_report() if callable(build_report) else []
        self.push_screen(
            ConsistencyScreen(items, teacher_mode=self.workflow_state.teacher_mode),
            self._on_consistency_target_chosen,
        )

    def _on_consistency_target_chosen(self, target: object) -> None:
        """Route an audit selection to the grade screen at that student/question."""
        if not target:
            return
        student_item_id, question_id = target
        if student_item_id:
            self.grade_controller.open_review_target(student_item_id, question_id)

    def _show_similarity_audit(self) -> None:
        """Open the answer-integrity audit; jump to a chosen student on close.

        Both halves are built here: near-identical free responses, and — where
        the provider can supply it — the objective answer-pattern report the
        modal shows on ``o``. A provider without an answer key (the stub) simply
        offers no second view rather than an empty one.
        """
        build_report = getattr(self.data_provider, "build_similarity_report", None)
        items = build_report() if callable(build_report) else []
        build_patterns = getattr(self.data_provider, "build_answer_pattern_report", None)
        patterns = build_patterns() if callable(build_patterns) else None
        self.push_screen(
            SimilarityScreen(
                items,
                teacher_mode=self.workflow_state.teacher_mode,
                patterns=patterns,
            ),
            self._on_similarity_target_chosen,
        )

    def _on_similarity_target_chosen(self, target: object) -> None:
        """Route a similarity selection to the grade screen at that student/question."""
        if not target:
            return
        student_item_id, question_id = target
        if student_item_id:
            self.grade_controller.open_review_target(student_item_id, question_id)

    def _show_grader_agreement(self) -> None:
        """Open the inter-grader agreement report (leniency, drift, pairs)."""
        build_report = getattr(
            self.data_provider, "build_grader_agreement_report", None
        )
        if not callable(build_report):
            return
        self.push_screen(GraderAgreementScreen(build_report()))

    def _show_unify_answers(self) -> None:
        """Offer the merges proposed for the current question's similar answers.

        Scoped to the question being graded, so it needs a current question —
        without one there is nothing to propose merges *for*.
        """
        question = self.workflow_state.grading_run.current_question
        if question is None:
            return
        self.push_screen(
            UnifyAnswersScreen(
                self.grade_controller.suggested_merges_for_question(question.question_id),
                question_label=format_question_label(question.question_id),
                accepted_indices=self.grade_controller.accepted_merge_indices(
                    question.question_id
                ),
            ),
            self._on_merge_accepted,
        )

    def _on_merge_accepted(self, index: object) -> None:
        """Record an accepted merge and redraw, so the widened count shows."""
        if not isinstance(index, int):
            return
        question = self.workflow_state.grading_run.current_question
        if question is None:
            return
        status = self.grade_controller.accept_suggested_merge(
            question.question_id, index
        )
        self.grade_controller.refresh_grade_screen(status=status)

    def _show_regrade_queue(self) -> None:
        """Open the regrade-request queue; act on the grader's choice on close."""
        build_items = getattr(self.data_provider, "build_regrade_items", None)
        items = build_items() if callable(build_items) else []
        self.push_screen(
            RegradeQueueScreen(items, teacher_mode=self.workflow_state.teacher_mode),
            self._on_regrade_choice,
        )

    def _on_regrade_choice(self, choice: object) -> None:
        """Perform an open/resolve/deny chosen in the regrade modal.

        Opening routes to the disputed question (the student's first question for
        a whole-assignment dispute, handled by the grade controller). Resolving
        or denying persists to the append-only log and reopens the refreshed
        queue so triage continues, mirroring the identity-resolution flow.
        """
        if not isinstance(choice, RegradeChoice):
            return
        if choice.action == "open":
            if choice.student_item_id:
                self.grade_controller.open_review_target(
                    choice.student_item_id, choice.question_id
                )
            return
        try:
            if choice.action == "resolve":
                self.data_provider.resolve_regrade(choice.request_id)
                self.notify("Regrade request resolved.")
            elif choice.action == "deny":
                self.data_provider.deny_regrade(choice.request_id)
                self.notify("Regrade request denied.")
        except Exception as exc:  # surface a save failure, keep triaging
            self.notify(f"Could not update request: {exc}", severity="error")
        # Reopen the (now-refreshed) queue to keep working through disputes.
        self._show_regrade_queue()

    def _show_file_regrade(self) -> None:
        """Open the file-a-regrade dialog, pre-filled from the current focus.

        Teacher-mode only (the binding gates it). On the grade screen the student
        and question in focus pre-fill the form; on the QA picker the teacher
        picks them (a dropdown of the cohort, plus a manual-id escape hatch).
        """
        provider = self.data_provider
        student_choices_fn = getattr(provider, "regrade_student_choices", None)
        question_choices_fn = getattr(provider, "regrade_question_choices", None)
        file_regrade_fn = getattr(provider, "file_regrade", None)
        if not (
            callable(student_choices_fn)
            and callable(question_choices_fn)
            and callable(file_regrade_fn)
        ):
            return

        selection = self.workflow_state.selection
        initial_student_id = ""
        initial_question_id: str | None = None
        # Pre-fill from what's in focus. On the grade screen both are set; on the
        # QA picker they're filled once the teacher has drilled into a student.
        identity_fn = getattr(provider, "student_identity_for_item", None)
        if callable(identity_fn):
            identity = identity_fn(selection.student_item_id)
            if identity is not None:
                initial_student_id = identity[0]
        if self.workflow_state.current_page == PAGE_GRADE:
            initial_question_id = selection.question_id
        elif selection.question_id is not None:
            initial_question_id = selection.question_id

        self.push_screen(
            FileRegradeScreen(
                student_choices=student_choices_fn(),
                question_choices=question_choices_fn(),
                initial_student_id=initial_student_id,
                initial_question_id=initial_question_id,
            ),
            self._on_file_regrade_result,
        )

    def _on_file_regrade_result(self, result: object) -> None:
        """Persist a filed regrade request, or do nothing on cancel."""
        if not isinstance(result, FileRegradeResult):
            return
        try:
            self.data_provider.file_regrade(
                student_id=result.student_id,
                reason=result.reason,
                question_id=result.question_id,
            )
        except Exception as exc:  # surface a write failure, keep grading
            self.notify(f"Could not file regrade request: {exc}", severity="error")
            return
        where = "the whole assignment" if not result.question_id else result.question_id
        self.notify(f"Regrade request filed for {result.student_id} ({where}).")

    def _show_item_analysis(self) -> None:
        """Open the read-only per-question item-analysis modal.

        Also carries the learning-outcome view (``o`` inside the modal) when the
        answer key tags outcomes; ``build_outcome_mastery_report`` returns
        ``None`` when it doesn't, and the modal then hides the toggle entirely.
        """
        build_report = getattr(self.data_provider, "build_item_analysis_report", None)
        if not callable(build_report):
            return
        # Offer a per-section view when the assignment was graded across sections.
        list_sections = getattr(self.data_provider, "available_sections", None)
        sections = tuple(list_sections()) if callable(list_sections) else ()

        build_outcomes = getattr(
            self.data_provider, "build_outcome_mastery_report", None
        )
        outcome_report = build_outcomes() if callable(build_outcomes) else None
        self.push_screen(
            ItemAnalysisScreen(
                build_report(),
                report_builder=build_report if sections else None,
                sections=sections,
                outcome_report=outcome_report,
                # Only worth rebuilding per section when both exist.
                outcome_builder=(
                    build_outcomes
                    if (outcome_report is not None and sections)
                    else None
                ),
            )
        )

    def _show_llm_eval(self) -> None:
        """Open the AI-grading calibration modal for the loaded assignment.

        Prefilled with this session's answer key and submissions, so a grader
        who has just marked a batch by hand can measure the model against that
        work without leaving the workspace. Read-only — the harness never writes
        a grade, and there is no Auto-grade field to fill in from here, so the
        modal's recommendation is reported and nothing is changed.
        """
        provider = self.data_provider
        solutions = str(getattr(provider, "qa_file_path", "") or "")
        submissions = str(getattr(provider, "submissions_path", "") or "")
        self.push_screen(
            LlmEvalScreen(solutions_path=solutions, submissions_path=submissions)
        )

    def _show_grader_packet(self) -> None:
        """Open the create-a-grader-copy modal for the loaded assignment.

        Prefilled with this session's submissions and answer key, so handing a
        class to a TA is one keystroke from wherever the grader already is.
        """
        provider = self.data_provider
        self.push_screen(
            GraderPacketScreen(
                submissions_path=str(getattr(provider, "submissions_path", "") or ""),
                qa_file_path=str(getattr(provider, "qa_file_path", "") or ""),
            )
        )

    def _show_merge(self) -> None:
        """Open the merge-a-copy-back modal for the loaded assignment.

        ``teacher_mode`` is passed rather than gating the whole screen: the plan
        lists students, so outside teacher mode it shows their display ids —
        the same anonymity-aware treatment the consistency and similarity
        audits get, and for the same reason. Hiding the screen entirely would
        withdraw it from exactly the people most likely to be merging.
        """
        provider = self.data_provider
        screen = MergeScreen(
            submissions_path=str(getattr(provider, "submissions_path", "") or ""),
            teacher_mode=self.workflow_state.teacher_mode,
        )
        self.push_screen(screen, self._after_merge)

    def _after_merge(self, merged: bool | None) -> None:
        """Re-read the cohort if the merge wrote anything.

        A merge rewrites ``.submission.md`` files the session already has open,
        so without this the workspace would show stale grades *and* the mtime
        guard would refuse the grader's very next save — a "changed on disk"
        error caused by their own merge, which is the least explicable failure
        the app could produce.
        """
        if not merged:
            return
        reload_all = getattr(self.data_provider, "reload_all", None)
        if callable(reload_all):
            reload_all()
        self.grade_controller.refresh_after_external_change()

    def _show_backup(self) -> None:
        """Open the backup & integrity modal for the loaded assignment."""
        provider = self.data_provider
        self.push_screen(
            BackupScreen(
                submissions_path=str(getattr(provider, "submissions_path", "") or "")
            )
        )

    def _show_release(self) -> None:
        """Open the release modal: readiness, and what moved since the last one."""
        provider = self.data_provider
        build_report = getattr(provider, "build_readiness_report", None)
        if not callable(build_report):
            return
        previous = None
        comparison = None
        latest = getattr(provider, "latest_release", None)
        if callable(latest):
            previous = latest()
        compare = getattr(provider, "compare_to_release", None)
        if previous is not None and callable(compare):
            comparison = compare(previous)
        read_releases = getattr(provider, "read_releases", None)
        self.push_screen(
            ReleaseScreen(
                build_report(),
                previous=previous,
                comparison=comparison,
                releases=read_releases() if callable(read_releases) else [],
                teacher_mode=self.workflow_state.teacher_mode,
                on_release=getattr(provider, "create_release", None)
                and (lambda label: provider.create_release(label=label)),
            ),
            self._on_released,
        )

    def _on_released(self, release_id: object) -> None:
        """Confirm a recorded release in the status line.

        Nothing is reloaded afterwards, and that is the point: a release writes
        only into `.opengrader/releases/`, never into a `.submission.md`, so the
        session's view of every grade is still exactly right.
        """
        if not release_id:
            return
        self.notify(
            f"Recorded release {release_id}. Grades are unchanged — press L "
            "later to see what has moved since.",
        )

    def _show_comment_library(self) -> None:
        """Open the reusable comment-snippet library; insert a pick on close."""
        read_library = getattr(self.data_provider, "read_comment_library", None)
        if not callable(read_library):
            return
        # Offer to save whatever's already in the comment box as a new snippet.
        current_comment = ""
        try:
            current_comment = self.get_grade_screen().current_comment_text()
        except Exception:
            pass
        # The current question powers the per-question ordering toggle.
        question_id = ""
        try:
            current_question = self.workflow_state.grading_run.current_question
            if current_question is not None:
                question_id = current_question.question_id or ""
        except Exception:
            pass
        question_label = format_question_label(question_id) if question_id else ""
        self.push_screen(
            CommentLibraryScreen(
                read_library(),
                on_change=self._save_comment_library,
                current_comment=current_comment,
                question_id=question_id,
                question_label=question_label,
                scope=getattr(self, "_comment_library_scope", "global"),
                on_scope_change=self._set_comment_library_scope,
            ),
            self._on_comment_snippet_chosen,
        )

    def _set_comment_library_scope(self, scope: str) -> None:
        """Remember the grader's global/per-question ordering choice this session."""
        self._comment_library_scope = scope

    def _save_comment_library(self, library: object) -> None:
        """Persist add/delete/use edits made in the comment-library modal."""
        save = getattr(self.data_provider, "save_comment_library", None)
        if not callable(save):
            return
        try:
            save(library)
        except Exception as exc:  # a read-only folder shouldn't crash grading
            self.notify(
                f"Could not save comment library: {exc}", severity="error"
            )

    def _on_comment_snippet_chosen(self, text: object) -> None:
        """Drop the chosen snippet into the current answer's comment box."""
        if not text or not isinstance(text, str):
            return
        try:
            self.get_grade_screen().insert_comment_snippet(text)
        except Exception:
            pass

    def _handle_back(self) -> None:
        # Back is only bound on the grade screen; the QA picker is the root.
        if self.workflow_state.current_page == PAGE_GRADE:
            self.grade_controller.return_to_qa_picker()

    def _handle_qa_move_down(self) -> None:
        qa_screen = self.get_qa_picker_screen()
        if qa_screen.students_have_focus():
            qa_screen.move_student_cursor(1)
            return
        self.grade_controller.qa_move_down()

    def _handle_qa_move_up(self) -> None:
        qa_screen = self.get_qa_picker_screen()
        if qa_screen.students_have_focus():
            qa_screen.move_student_cursor(-1)
            return
        self.grade_controller.qa_move_up()

    def _handle_student_move_down(self) -> None:
        self.get_qa_picker_screen().move_student_cursor(1)

    def _handle_student_move_up(self) -> None:
        self.get_qa_picker_screen().move_student_cursor(-1)

    def _handle_qa_switch_pane(self) -> None:
        self.get_qa_picker_screen().switch_active_pane()
        self.refresh_bindings()

    def _handle_qa_filter(self) -> None:
        qa_screen = self.get_qa_picker_screen()
        prompt, value = qa_screen.start_filter()
        status = qa_screen.update_filter(value)
        self.query_one(StatusBar).enter_search(
            prompt=prompt,
            value=value,
            status=status,
        )
        self.refresh_bindings()

    def _finish_qa_filter(self) -> None:
        self.query_one(StatusBar).exit_search()
        if self.workflow_state.current_page == PAGE_QA_PICKER:
            self.get_qa_picker_screen().finish_filter()
        self.call_after_refresh(self.refresh_bindings)

    def _handle_grade_shrink_sidebar(self) -> None:
        grade_screen = self.get_grade_screen()
        if grade_screen.query_one("#grade-workspace").has_class("-layout-top-bottom"):
            grade_screen.adjust_sidebar_height(-grade_screen.SIDEBAR_RESIZE_STEP)
        else:
            grade_screen.adjust_sidebar_width(-grade_screen.SIDEBAR_RESIZE_STEP)

    def _handle_grade_widen_sidebar(self) -> None:
        grade_screen = self.get_grade_screen()
        if grade_screen.query_one("#grade-workspace").has_class("-layout-top-bottom"):
            grade_screen.adjust_sidebar_height(grade_screen.SIDEBAR_RESIZE_STEP)
        else:
            grade_screen.adjust_sidebar_width(grade_screen.SIDEBAR_RESIZE_STEP)

    def _input_command_available(self, command: InputCommand | str) -> bool:
        if isinstance(self.focused, Input):
            # Same derived rule as `check_action` above — one source of truth,
            # so the two can never drift apart again (they had).
            if not command_survives_text_focus(
                InputController._coerce_command(command)
            ):
                return False

        if not self.input_controller.command_available(command):
            return False

        normalized_command = InputController._coerce_command(command)

        # Disable all input commands when editing the rubric and
        # prioritize GradeScreen on_key.
        if (
            self.workflow_state.current_page == PAGE_GRADE
            and self.get_grade_screen().rubric_edit_active()
        ):
            return False

        if self.workflow_state.current_page != PAGE_QA_PICKER:
            return True

        if normalized_command in (
            InputCommand.QA_MOVE_STUDENT_DOWN,
            InputCommand.QA_MOVE_STUDENT_UP,
        ):
            return self.get_qa_picker_screen().students_have_focus()

        if normalized_command in (InputCommand.QA_MOVE_DOWN, InputCommand.QA_MOVE_UP):
            return not self.get_qa_picker_screen().students_have_focus()

        return True

    def _refresh_status_bar_bindings(self) -> None:
        try:
            status_bar = self.query_one(StatusBar)
        except Exception:
            return
        if status_bar.search_active():
            return

        visible_bindings = [
            self._status_bar_binding(binding)
            for binding in self.input_controller.visible_bindings()
            if self._input_command_available(binding.command)
        ]
        status_bar.show_bindings(visible_bindings)

    @staticmethod
    def _status_bar_binding(binding: CommandBindingSpec) -> StatusBarBinding:
        key = binding.key_display or binding.keys
        if key == "enter":
            key = "↩"
        if key == "escape":
            key = "esc"
        return StatusBarBinding(key=key, description=binding.description)

    def _build_data_provider(
        self,
        *,
        qa_file_path: str | Path | None,
        submissions_path: str | Path | None,
    ) -> Provider:
        if qa_file_path is not None and submissions_path is not None:
            return SubmissionFilesProvider(
                qa_file_path=qa_file_path,
                submissions_path=submissions_path,
                default_image_path=self.default_image_path,
            )
        return StubDataProvider(self.default_image_path)

    def _load_work_claims(self) -> None:
        """Load the multi-grader claims and this app's grader identity.

        Best-effort, exactly like the policy read above: a folder with no claims
        (or an unreadable log) leaves navigation showing everything, which is
        the behaviour that predates claims entirely.
        """
        state = self.workflow_state
        try:
            state.grader_actor = self.data_provider.grader_actor()
            state.active_claims = tuple(self.data_provider.read_work_claims())
        except Exception:
            state.active_claims = ()

    def _show_claims(self) -> None:
        """Open the work-claims modal (Shift+D)."""
        questions, students = self.data_provider.cohort_ids()
        screen = ClaimScreen(
            claims=list(self.data_provider.read_work_claims()),
            actor=self.workflow_state.grader_actor,
            question_ids=questions,
            student_ids=students,
            filter_enabled=self.workflow_state.claim_filter_enabled,
        )
        self.push_screen(screen, self._on_claim_action)

    def _on_claim_action(self, result: ClaimAction | None) -> None:
        """Perform whatever the claims modal asked for, then reload state."""
        if result is None:
            return
        state = self.workflow_state
        if result.kind == "toggle_filter":
            state.claim_filter_enabled = not state.claim_filter_enabled
            self.grade_controller.refresh_after_claim_change()
            self._show_claims()
            return
        try:
            if result.kind == "claim" and result.work is not None:
                self.data_provider.claim_work_slice(result.work, note=result.note)
            elif result.kind == "release":
                self.data_provider.release_work_claim(result.claim_id)
            elif result.kind == "complete":
                self.data_provider.complete_work_claim(result.claim_id)
        except ClaimConflictError as exc:
            self.notify(str(exc), title="Already claimed", severity="warning", timeout=10)
        except Exception as exc:  # a read-only folder must not kill the session
            self.notify(f"Could not update claims: {exc}", severity="error")

        self._load_work_claims()
        self.grade_controller.refresh_after_claim_change()
        # Reopen so the grader sees the result of what they just did, mirroring
        # the review-queue and regrade modals.
        self._show_claims()

    def _apply_grading_policy(self) -> None:
        """Read the assignment's grading policy and apply the anonymity lock.

        A locked folder disables the teacher-mode toggle (and forces anonymity
        on), so a grader can't reveal names in a multi-grader assignment. Absent
        or unreadable policy leaves grading unlocked (solo default).
        """
        submissions_path = getattr(self.data_provider, "submissions_path", None)
        if submissions_path is None:
            return
        try:
            policy = read_policy(submissions_path)
        except Exception:
            return
        self.workflow_state.anonymity_locked = policy.anonymity_locked
        if policy.anonymity_locked:
            self.workflow_state.teacher_mode = False

    def _load_grading_queue(self) -> None:
        selection = self.workflow_state.selection
        selection.course_item_id = "c1"
        selection.course_id = 1
        selection.assignment_item_id = "a1"
        selection.assignment_id = 1
        selection.question_id = None
        selection.student_item_id = None
        selection.student_id = None

        course_label = getattr(self.data_provider, "course_label", "CMPE 195")
        assignment_label = getattr(self.data_provider, "assignment_label", "HW 1")
        self.workflow_state.selection_labels.update(
            {
                "course": str(course_label or "Course"),
                "assignment": str(assignment_label or "Assignment"),
                "question": "",
                "student": "",
            }
        )
        self.grade_controller.open_question_picker_for_assignment()
