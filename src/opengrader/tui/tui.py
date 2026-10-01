from collections.abc import Callable
from pathlib import Path
import logging
import time
from typing import Literal

from textual.app import App, ComposeResult
from textual.widgets import ContentSwitcher, Input, TextArea

from opengrader.core import app_config
from opengrader.core.claims import ClaimConflictError
from opengrader.core.policy import read_policy
from opengrader.core.workspace.models import WorkspaceEvent
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
from opengrader.tui.ui.widgets.ai_egress_banner import AIEgressBanner
from opengrader.tui.ui.widgets.grading_log_pane import GradingLogPane

_EXTRACTION_POLL_SECONDS = 3.0

_EXTRACTION_GIVE_UP_SECONDS = 600.0
logger = logging.getLogger(__name__)


def _is_workspace_provider(provider: Provider) -> bool:
    """True for a `WorkspaceProvider`, without importing it for folder mode.

    The module can only be in `sys.modules` if something (the workspace
    launcher) already imported it, so folder mode never pays for the import.
    """
    import sys

    module = sys.modules.get("opengrader.tui.data.workspace_provider")
    return module is not None and isinstance(provider, module.WorkspaceProvider)


class TuiApp(App[Literal["switch_assignment"]]):
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
        """Two apps in one class, chosen by what it is handed.

        **Folder mode** (`opengrader classic`, and anything that is not a
        workspace): no arguments opens Tools — the Start grading card, every
        pipeline step and helper tool — and `qa_file_path` + `submissions_path`
        grade a folder in place. F2 comes back to the Start grading card and
        F3 moves between Tools and grading. Never touches the workspace service.

        **Workspace mode** (`opengrader tui`): `data_provider` is a
        `WorkspaceProvider` handed in by the launcher. Grading only — there is
        no Tools page to reach, and F2 exits so the launcher can show the
        workspace's assignment list again.
        """
        super().__init__()
        self.default_image_path = default_image_path or str(Path("images/Grading.jpg"))
        self.workflow_state = TuiWorkflowState()
        self.data_provider = data_provider or self._build_data_provider(
            qa_file_path=qa_file_path,
            submissions_path=submissions_path,
        )
        # Decided once: a session never changes mode. The Tools page (and so
        # F3) exists only in folder mode; a workspace session is grading only.
        self.has_tools_page = not _is_workspace_provider(self.data_provider)
        if isinstance(self.data_provider, StubDataProvider):
            self.workflow_state.current_page = PAGE_SETUP
        else:
            self.workflow_state.session_open = True
        self._workspace_events: dict[tuple[str, int, str], WorkspaceEvent] = {}
        self._workspace_retry_at = 0.0
        self.grade_controller = GradeController(
            self,
            self.workflow_state,
            self.data_provider,
        )
        self.input_controller = InputController(self.workflow_state)
        self._register_input_handlers()

    def compose(self) -> ComposeResult:
        initial_page = (
            PAGE_SETUP
            if isinstance(self.data_provider, StubDataProvider)
            else PAGE_QA_PICKER
        )
        # Above every page, not inside any one of them, so it is one banner
        # that stays put across Tools, the picker and grading, rather than a
        # copy each screen would have to remember to mount and refresh.
        yield AIEgressBanner(id="ai-egress-banner")
        with ContentSwitcher(initial=initial_page, id="workspace-switcher"):
            # In folder mode Tools is always composed, so the helper tools stay
            # one key away (F3) from grading. A workspace session never has it.
            if self.has_tools_page:
                yield SetupScreen(id=PAGE_SETUP)
            yield QAPickerScreen(id=PAGE_QA_PICKER)
            yield GradeScreen(self.default_image_path, id=PAGE_GRADE)
        yield StatusBar(id="app-status-bar")

    def on_mount(self) -> None:
        if self.has_tools_page and self.workflow_state.session_open:
            self._remember_folder()  # opened with --qa-file/--submissions
        if not isinstance(self.data_provider, StubDataProvider):
            self._apply_grading_policy()
            self._load_work_claims()
            self._load_grading_queue()
            submissions_path = getattr(self.data_provider, "submissions_path", None)
            if submissions_path is not None:
                self._start_extraction_watch(submissions_path, known_active=None)
        if not self.has_tools_page:  # a workspace session: follow its events
            provider = self.data_provider

            def changed(event: WorkspaceEvent) -> None:
                if (
                    event.assignment_id == provider.assignment.id
                    and event.kind != "feedback"
                ):
                    self.call_from_thread(self._workspace_changed, event)

            self._stop_workspace_events = provider.client.observe(
                provider.cursor, changed
            )
            self.set_interval(0.1, self._refresh_workspace)
        self._show_session_on_tools_page()
        if self.workflow_state.current_page == PAGE_SETUP:
            # Classic with nothing open: land on the recent folders (or the
            # class-folder box), so a returning user can start with ↩.
            self.get_setup_screen().focus_launch_card(scroll=False)
        self.refresh_bindings()
        self._announce_graphics_capability()
        self.refresh_ai_banner()

    def refresh_ai_banner(self) -> None:
        """Re-read AI setup and show/hide the egress banner to match.

        Call this whenever the saved AI configuration might have changed —
        AI setup or the providers screen closing, most notably — so the
        banner never lags behind what will actually happen on the next call.
        """
        try:
            banner = self.query_one("#ai-egress-banner", AIEgressBanner)
        except Exception:  # noqa: BLE001 - the banner is a courtesy
            return
        banner.update_for(app_config.resolve())

    def _announce_graphics_capability(self) -> None:
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
        if page_id == PAGE_SETUP:
            self._show_session_on_tools_page()
        self.refresh_bindings()

    def _show_session_on_tools_page(self) -> None:
        """Tell the Start grading card what is open, so it can say so."""
        if not self.has_tools_page:
            return
        provider = self.data_provider
        qa = getattr(provider, "qa_file_path", None)
        subs = getattr(provider, "submissions_path", None)
        try:
            self.get_setup_screen().show_session(
                qa if self.workflow_state.session_open else None,
                subs if self.workflow_state.session_open else None,
            )
        except Exception:  # noqa: BLE001 - the banner is a courtesy
            pass

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

        if isinstance(self.focused, TextArea):
            return False

        if isinstance(self.focused, Input):
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

    def on_grade_screen_rubric_draft_requested(
        self, _event: GradeScreen.RubricDraftRequested
    ) -> None:
        self.grade_controller.draft_rubric_with_ai()

    def on_grade_screen_transcription_updated(
        self, event: GradeScreen.TranscriptionUpdated
    ) -> None:
        self.grade_controller.on_transcription_updated(event.response_text)

    def on_grade_screen_return_to_home_requested(
        self, _event: GradeScreen.ReturnToHomeRequested
    ) -> None:
        self.grade_controller.return_to_qa_picker()

    def on_setup_screen_launch_requested(self, event: SetupScreen.LaunchRequested) -> None:
        self._open_folder(event.qa_path, event.submissions_path, known_active=None)

    def on_setup_screen_grade_while_extracting_requested(
        self, event: SetupScreen.GradeWhileExtractingRequested
    ) -> None:
        if self._open_folder(
            event.qa_path,
            event.submissions_path,
            known_active=self._setup_screen_extraction_active,
        ):
            self.notify(
                "Grading opened while extraction is still running in the "
                "background. Submissions will appear as they finish."
            )

    def _open_folder(
        self,
        qa_path: str | Path,
        submissions_path: str | Path,
        *,
        known_active: Callable[[], bool] | None,
    ) -> bool:
        """Grade a folder from the Start grading card or the pipeline, with no service."""
        try:
            provider = SubmissionFilesProvider(
                qa_file_path=qa_path,
                submissions_path=submissions_path,
                default_image_path=self.default_image_path,
            )
        except Exception as exc:  # noqa: BLE001 - shown to the user, app stays up
            self.notify(f"Error loading files: {exc}", severity="error")
            return False
        self.workflow_state = TuiWorkflowState(session_open=True)
        self.data_provider = provider
        self.grade_controller = GradeController(self, self.workflow_state, provider)
        self.input_controller = InputController(self.workflow_state)
        self._register_input_handlers()
        self._apply_grading_policy()
        self._load_work_claims()
        self._load_grading_queue()
        self.switch_to(PAGE_QA_PICKER)
        self._start_extraction_watch(submissions_path, known_active=known_active)
        self._remember_folder()
        self._warn_if_shared_workspace(submissions_path)
        return True

    def _warn_if_shared_workspace(self, submissions_path: str | Path) -> None:
        """Say so when browser grades for this folder may not be in its files.

        A folder session writes every browser grade into the files, so no
        warning then; a normal workspace keeps them until written back.
        """
        from opengrader.core.workspace.folder_session import shared_workspace_for

        if shared_workspace_for(submissions_path) is None:
            return
        self.notify(
            "This folder is also in a workspace. Grades entered in the browser "
            "for it live in the workspace until written back "
            "(opengrader tui, open it, then W).",
            severity="warning",
            timeout=15,
        )

    def _remember_folder(self) -> None:
        """List this class folder on the Start grading card next time.

        The entry points at the class folder — the common parent of answer
        key and submissions — and keeps both paths, so choosing it later
        reopens exactly this pair. Best effort, like everything in recents.
        """
        import os

        from opengrader.core.recents import record_recent

        qa = getattr(self.data_provider, "qa_file_path", None)
        submissions = getattr(self.data_provider, "submissions_path", None)
        if qa is None or submissions is None:
            return
        qa, submissions = Path(qa), Path(submissions)
        try:
            from opengrader.core.qa_file import load_qa_markdown

            title = load_qa_markdown(qa).title or ""
        except Exception:  # noqa: BLE001 - a title is only a label
            title = ""
        try:
            folder = Path(os.path.commonpath([qa.parent, submissions]))
        except ValueError:  # different drives (Windows): no common parent
            folder = submissions.parent
        record_recent(
            "folder", folder, title=title, qa_file=qa, submissions=submissions
        )

    def _toggle_start_page(self) -> None:
        """F3 (folder mode only): Tools and its helper tools, and back again."""
        if not self.has_tools_page:
            return
        if self.workflow_state.current_page != PAGE_SETUP:
            if self.grade_controller.commit_pending_comment():
                return
            self.switch_to(PAGE_SETUP)
            return
        if self.workflow_state.session_open:
            self.switch_to(PAGE_QA_PICKER)

    def _setup_screen_extraction_active(self) -> bool:
        try:
            return self.get_setup_screen().extraction_in_progress
        except Exception:
            return False


    def _start_extraction_watch(
        self,
        submissions_path: "str | Path",
        *,
        known_active: "Callable[[], bool] | None",
    ) -> None:
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
            except Exception as exc:
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
        if known_active is False:
            self._stop_extraction_watch()
            return
        if known_active is None and (
            idle_seconds is None or idle_seconds >= _EXTRACTION_GIVE_UP_SECONDS
        ):
            self._stop_extraction_watch()

    def _register_input_handlers(self) -> None:
        self.input_controller.register(InputCommand.APP_QUIT, self.exit)
        self.input_controller.register(
            InputCommand.SWITCH_ASSIGNMENT, self._switch_assignment
        )
        self.input_controller.register(InputCommand.OPEN_START_PAGE, self._toggle_start_page)
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
            InputCommand.GRADE_AI_DRAFT_GRADES,
            self.grade_controller.draft_grades_with_ai,
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
        self.push_screen(FormatGuideScreen())

    def _show_review_queue(self) -> None:
        build_items = getattr(self.data_provider, "build_review_items", None)
        items = build_items() if callable(build_items) else []
        self.push_screen(
            ReviewQueueScreen(items, teacher_mode=self.workflow_state.teacher_mode),
            self._on_review_target_chosen,
        )

    def _on_review_target_chosen(self, item: object) -> None:
        if item is None:
            return
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
                except Exception as exc:
                    self.notify(f"Could not assign ID: {exc}", severity="error")
            self._show_review_queue()

        self.push_screen(AssignIdentityScreen(item), _after)

    def _toggle_teacher_mode(self) -> None:
        self.grade_controller.toggle_teacher_mode()
        self.refresh_bindings()

    def _show_grade_history(self) -> None:
        read_history = getattr(self.data_provider, "read_grade_history", None)
        entries = read_history() if callable(read_history) else []
        self.push_screen(
            GradeHistoryScreen(
                entries, teacher_mode=self.workflow_state.teacher_mode
            )
        )

    def _show_grading_session(self) -> None:
        build_report = getattr(
            self.data_provider, "build_grading_session_report", None
        )
        if not callable(build_report):
            return
        self.push_screen(GradingSessionScreen(build_report()))

    def _show_consistency_audit(self) -> None:
        build_report = getattr(self.data_provider, "build_consistency_report", None)
        items = build_report() if callable(build_report) else []
        self.push_screen(
            ConsistencyScreen(items, teacher_mode=self.workflow_state.teacher_mode),
            self._on_consistency_target_chosen,
        )

    def _on_consistency_target_chosen(self, target: object) -> None:
        if not target:
            return
        student_item_id, question_id = target
        if student_item_id:
            self.grade_controller.open_review_target(student_item_id, question_id)

    def _show_similarity_audit(self) -> None:
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
        if not target:
            return
        student_item_id, question_id = target
        if student_item_id:
            self.grade_controller.open_review_target(student_item_id, question_id)

    def _show_grader_agreement(self) -> None:
        build_report = getattr(
            self.data_provider, "build_grader_agreement_report", None
        )
        if not callable(build_report):
            return
        self.push_screen(GraderAgreementScreen(build_report()))

    def _show_unify_answers(self) -> None:
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
        build_items = getattr(self.data_provider, "build_regrade_items", None)
        items = build_items() if callable(build_items) else []
        self.push_screen(
            RegradeQueueScreen(items, teacher_mode=self.workflow_state.teacher_mode),
            self._on_regrade_choice,
        )

    def _on_regrade_choice(self, choice: object) -> None:
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
        except Exception as exc:
            self.notify(f"Could not update request: {exc}", severity="error")
        self._show_regrade_queue()

    def _show_file_regrade(self) -> None:
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
        if not isinstance(result, FileRegradeResult):
            return
        try:
            self.data_provider.file_regrade(
                student_id=result.student_id,
                reason=result.reason,
                question_id=result.question_id,
            )
        except Exception as exc:
            self.notify(f"Could not file regrade request: {exc}", severity="error")
            return
        where = "the whole assignment" if not result.question_id else result.question_id
        self.notify(f"Regrade request filed for {result.student_id} ({where}).")

    def _show_item_analysis(self) -> None:
        build_report = getattr(self.data_provider, "build_item_analysis_report", None)
        if not callable(build_report):
            return
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
                outcome_builder=(
                    build_outcomes
                    if (outcome_report is not None and sections)
                    else None
                ),
            )
        )

    def _show_llm_eval(self) -> None:
        provider = self.data_provider
        solutions = str(getattr(provider, "qa_file_path", "") or "")
        submissions = str(getattr(provider, "submissions_path", "") or "")
        self.push_screen(
            LlmEvalScreen(solutions_path=solutions, submissions_path=submissions)
        )

    def _show_grader_packet(self) -> None:
        provider = self.data_provider
        self.push_screen(
            GraderPacketScreen(
                submissions_path=str(getattr(provider, "submissions_path", "") or ""),
                qa_file_path=str(getattr(provider, "qa_file_path", "") or ""),
            )
        )

    def _show_merge(self) -> None:
        provider = self.data_provider
        screen = MergeScreen(
            submissions_path=str(getattr(provider, "submissions_path", "") or ""),
            teacher_mode=self.workflow_state.teacher_mode,
        )
        self.push_screen(screen, self._after_merge)

    def _after_merge(self, merged: bool | None) -> None:
        if not merged:
            return
        reload_all = getattr(self.data_provider, "reload_all", None)
        if callable(reload_all):
            reload_all()
        self.grade_controller.refresh_after_external_change()

    def _show_backup(self) -> None:
        provider = self.data_provider
        self.push_screen(
            BackupScreen(
                submissions_path=str(getattr(provider, "submissions_path", "") or "")
            )
        )

    def _show_release(self) -> None:
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
        if not release_id:
            return
        self.notify(
            f"Recorded release {release_id}. Grades are unchanged — press L "
            "later to see what has moved since.",
        )

    def _show_comment_library(self) -> None:
        read_library = getattr(self.data_provider, "read_comment_library", None)
        if not callable(read_library):
            return
        current_comment = ""
        try:
            current_comment = self.get_grade_screen().current_comment_text()
        except Exception:
            pass
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
        self._comment_library_scope = scope

    def _save_comment_library(self, library: object) -> None:
        save = getattr(self.data_provider, "save_comment_library", None)
        if not callable(save):
            return
        try:
            save(library)
        except Exception as exc:
            self.notify(
                f"Could not save comment library: {exc}", severity="error"
            )

    def _on_comment_snippet_chosen(self, text: object) -> None:
        if not text or not isinstance(text, str):
            return
        try:
            self.get_grade_screen().insert_comment_snippet(text)
        except Exception:
            pass

    def _handle_back(self) -> None:
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

    def _switch_assignment(self) -> None:
        # Save a half-typed comment first; if that fails, stay put.
        if self.grade_controller.commit_pending_comment():
            return
        if self.has_tools_page:
            # Folder mode: choosing another folder is the Start grading card's
            # job, and it lives in this app — no need to exit and relaunch.
            self.switch_to(PAGE_SETUP)
            self.get_setup_screen().focus_launch_card()
            return
        # Workspace mode: the launcher shows the assignment list again.
        self.exit("switch_assignment")

    def _input_command_available(self, command: InputCommand | str) -> bool:
        if isinstance(self.focused, Input):
            if not command_survives_text_focus(
                InputController._coerce_command(command)
            ):
                return False

        if not self.input_controller.command_available(command):
            return False

        normalized_command = InputController._coerce_command(command)

        if (
            normalized_command is InputCommand.OPEN_START_PAGE
            and not self.has_tools_page
        ):
            return False

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
        state = self.workflow_state
        try:
            state.grader_actor = self.data_provider.grader_actor()
            state.active_claims = tuple(self.data_provider.read_work_claims())
        except Exception:
            state.active_claims = ()

    def on_unmount(self) -> None:
        self._stop_following_workspace()

    def _stop_following_workspace(self) -> None:
        stop = getattr(self, "_stop_workspace_events", None)
        self._stop_workspace_events = None
        self._workspace_events.clear()
        if stop:
            stop()

    def _workspace_changed(self, event: WorkspaceEvent) -> None:
        self._workspace_events[(event.kind, event.submission_id, event.question_id)] = (
            event
        )

    def _refresh_workspace(self) -> None:
        from opengrader.tui.data.workspace_provider import WorkspaceProvider

        if (
            not self._workspace_events
            or len(self.screen_stack) > 1
            or self.get_grade_screen().rubric_edit_active()
            or self.get_grade_screen().transcription_editor_active()
            or time.monotonic() < self._workspace_retry_at
        ):
            return
        provider = self.data_provider
        if not isinstance(provider, WorkspaceProvider):
            return
        pending = list(self._workspace_events.values())
        try:
            provider.invalidate_summaries()
            self.grade_controller.refresh_workspace(pending)
        except Exception as exc:
            logger.exception("Could not refresh the workspace")
            if not self._workspace_retry_at:
                self.notify(
                    f"Could not refresh the workspace: {exc}. Retrying.",
                    severity="error",
                )
            self._workspace_retry_at = time.monotonic() + 1
            return
        self._workspace_events.clear()
        self._workspace_retry_at = 0.0

    def show_grade_conflict(self) -> None:
        from opengrader.tui.data.workspace_provider import WorkspaceProvider
        from opengrader.tui.ui.screens.grade_conflict_screen import GradeConflictScreen

        provider = self.data_provider
        if not isinstance(provider, WorkspaceProvider) or provider.conflict is None:
            return
        key, current = provider.conflict

        def resolve(keep_draft: bool | None) -> None:
            if keep_draft is None:
                return
            try:
                provider.resolve_conflict(keep_draft=keep_draft)
                self.grade_controller.reset_after_recovery()
            except Exception as exc:
                self.notify(str(exc), severity="error", timeout=20)
                self.show_grade_conflict()

        self.push_screen(GradeConflictScreen(provider.journal.value.answers[key], current), resolve)

    def _show_claims(self) -> None:
        if not callable(getattr(self.data_provider, "cohort_ids", None)):
            self.notify("Work claims are not available yet.")
            return
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
        except Exception as exc:
            self.notify(f"Could not update claims: {exc}", severity="error")

        self._load_work_claims()
        self.grade_controller.refresh_after_claim_change()
        self._show_claims()

    def _apply_grading_policy(self) -> None:
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
