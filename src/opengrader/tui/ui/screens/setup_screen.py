from __future__ import annotations
import contextlib
import io
import os
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from dataclasses import replace

from textual import events, on, work
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.message import Message
from textual.widgets import (
    Button,
    Checkbox,
    Input,
    ProgressBar,
    Static,
    TextArea,
    Collapsible,
)

from opengrader.core import app_config, browser_app
from opengrader.tui.ui import button_roles
from opengrader.tui.ui.button_roles import OutlineButtons
from opengrader.core.pipeline import (
    PIPELINE_STAGES,
    STAGE_AUTO_GRADE,
    STAGE_DRAFT_RUBRICS,
    STAGE_EXTRACT,
    STAGE_SCAN_CHECK,
    STAGE_SPLIT,
    LlmOptions,
    PipelineProgress,
    PipelineReport,
    PipelineRequest,
    StageOutcome,
    StageStatus,
    auto_grade_pending,
    plan_pipeline,
    refine_argv,
    section_requests,
    run_pipeline,
)
from opengrader.core.pipeline_settings import (
    FolderPipelineSettings,
    load_folder_settings,
    load_machine_settings,
    save_folder_settings,
    save_machine_settings,
)
from opengrader.core.policy import GradingPolicy, read_policy, write_policy
from opengrader.core.qa_file.standard_rubric import DEFAULT_QUESTION_POINTS, format_points
from opengrader.core.submissions import ExtractionProgress
from opengrader.core.submissions.watch import ExtractionWatcher
from opengrader.core.submissions.photo_intake import (
    PhotoIntakeError,
    PhotoIntakeReport,
    import_photos,
)
from opengrader.cli.submission_extractor import run_cli as run_extractor_cli
from opengrader.cli.answer_key import default_points_note
from opengrader.cli.answer_key import run_cli as run_answer_key_cli
from opengrader.cli import run_command
from opengrader.cli.qa_file import command as qa_file_command
from opengrader.core.cloud_preflight import CloudConfirmationNeeded
from opengrader.core.class_folder import (
    GRADING_ROLES,
    ROLE_QA,
    ROLE_ROSTER,
    ROLE_SCAN,
    ROLE_SUBMISSIONS,
    ROLE_TEMPLATE,
    AssignmentFolders,
    ClassFolderScan,
    describe_class_folder,
    find_answer_key_source,
    find_assignment_folders,
    is_workspace,
    scan_class_folder,
)
from opengrader.core.course import COURSE_FILENAME
from opengrader.core.roster import parse_roster_arg
from opengrader.core.recents import load_recents
from opengrader.tui.ui.screens.ai_settings_screen import AISettingsScreen
from opengrader.tui.ui.screens.answer_fill_screen import AnswerFillScreen
from opengrader.tui.ui.screens.backup_screen import BackupScreen
from opengrader.tui.ui.screens.cloud_consent_screen import CloudPreflightConfirmScreen
from opengrader.tui.ui.screens.doctor_screen import DoctorScreen
from opengrader.tui.ui.screens.grade_without_ai_screen import GradeWithoutAIScreen
from opengrader.tui.ui.screens.course_grades_screen import CourseGradesScreen
from opengrader.tui.ui.screens.assistant_screen import AnswerKeyAssistantScreen, AssistantScreen
from opengrader.tui.ui.screens.handoff_screen import GraderPacketScreen, MergeScreen
from opengrader.tui.ui.screens.llm_eval_screen import LlmEvalScreen
from opengrader.tui.ui.screens.provider_screen import ProviderScreen
from opengrader.tui.ui.screens.page_order_screen import PageOrderScreen
from opengrader.tui.ui.screens.split_review_screen import SplitReviewScreen
from opengrader.tui.ui.screens.path_picker_screen import PathPickerScreen, PathPickMode
from opengrader.tui.ui.screens.prepare_folder_screen import (
    ACTION_AUTO_GRADE,
    ACTION_CREATE_KEY,
    ACTION_OPEN,
    ACTION_PREPARE,
    ACTION_SETTINGS,
    ACTION_SKIP,
    ACTION_WORKSPACE,
    PrepareFolderScreen,
)
from opengrader.tui.ui.screens.region_map_screen import RegionMapScreen
from opengrader.tui.ui.widgets.browser_health import BrowserHealth
from opengrader.tui.ui.widgets.browser_launcher import BrowserLauncher
from opengrader.tui.ui.widgets.recent_list import RecentList

_CPU_COUNT = os.cpu_count() or 1
# How many recent class folders the Start grading card lists (newest first).
RECENT_FOLDERS_SHOWN = 5


_PIPE_STATUS_GLYPH = {
    StageStatus.RAN: "✓",
    StageStatus.REUSED: "↺",
    StageStatus.SKIPPED: "·",
    StageStatus.BLOCKED: "●",
    StageStatus.FAILED: "✗",
    StageStatus.PAUSED: "‖",
}


# What a stage is doing, in the words the progress line uses.
_PIPE_STAGE_LABELS = {
    STAGE_SCAN_CHECK: "Checking the scan",
    STAGE_SPLIT: "Splitting it into one PDF per student",
    STAGE_EXTRACT: "Reading the answers",
    STAGE_DRAFT_RUBRICS: "Drafting rubric items",
    STAGE_AUTO_GRADE: "Auto-grading",
}

# Names tried, in order, for an answer key Create answer key is about to write.
_NEW_ANSWER_KEY_NAMES = ("qa.md", "answer-key.md", "solutions.md")


_COURSE_SEARCH_DEPTH = 4


def _find_course_file(start: Path) -> Path | None:
    try:
        current = start.expanduser().resolve()
    except OSError:
        return None
    for _ in range(_COURSE_SEARCH_DEPTH):
        candidate = current / COURSE_FILENAME
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            return None
        if current.parent == current:
            break
        current = current.parent
    return None


class SetupScreen(OutlineButtons, Vertical):
    DEFAULT_CSS = """
    SetupScreen #setup-container {
        align-horizontal: center;
        scrollbar-size: 1 1;
    }
    SetupScreen #setup-content {
        width: 100%;
        max-width: 104;
        height: auto;
        padding: 1 2 2 2;
    }

    SetupScreen #setup-hero {
        height: auto;
        background: $boost;
        padding: 1 3;
        margin-bottom: 1;
    }

    SetupScreen #setup-title-row {
        width: 100%;
        height: auto;
    }

    SetupScreen #setup-title {
        width: auto;
        height: auto;
        color: $accent;
        text-style: bold;
    }

    SetupScreen #setup-version {
        width: auto;
        height: auto;
        color: $text-muted;
        margin-left: 2;
    }

    SetupScreen #setup-subtitle {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    SetupScreen #launch-section {
        height: auto;
        border: round $accent;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 0 2 1 2;
        margin-bottom: 1;
    }

    SetupScreen #launch-recents {
        height: auto;
        margin-bottom: 1;
    }

    SetupScreen #launch-recent-list {
        height: auto;
        max-height: 15;
        background: transparent;
    }

    SetupScreen .card-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
        margin-top: 1;
    }

    SetupScreen .field-label {
        width: 100%;
        height: auto;
        margin-top: 1;
        color: $text;
    }

    SetupScreen #browser-open-button,
    SetupScreen #answers-button {
        margin-right: 1;
    }

    SetupScreen .card-actions {
        width: 100%;
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    SetupScreen #pipe-progress,
    SetupScreen #launch-progress {
        height: auto;
    }

    SetupScreen #pipe-pages,
    SetupScreen #ak-default-points {
        width: 24;
    }

    SetupScreen .section-heading {
        width: 100%;
        height: auto;
        margin: 1 0 0 0;
        color: $text-muted;
    }

    SetupScreen .section-caption {
        width: 100%;
        height: auto;
        color: $text-muted;
        margin-bottom: 1;
    }

    SetupScreen Collapsible {
        height: auto;
        background: transparent;
        border: round $surface-lighten-2;
        padding: 0 1;
        margin-bottom: 1;
    }

    SetupScreen Collapsible > CollapsibleTitle {
        width: 100%;
        padding: 0 1;
        color: $text;
        background: transparent;
    }

    SetupScreen Collapsible > CollapsibleTitle:hover {
        background: $boost;
        color: $text;
    }

    SetupScreen Collapsible > CollapsibleTitle:focus {
        background: $accent 20%;
        color: $text;
    }

    SetupScreen Collapsible > Contents {
        width: 100%;
        height: auto;
        padding: 0 1 1 1;
    }

    SetupScreen .path-row {
        height: auto;
    }

    SetupScreen .path-row Input {
        width: 1fr;
    }

    SetupScreen .path-row .browse-button {
        min-width: 10;
        width: auto;
        margin-left: 1;
    }

    SetupScreen #pipe-parallel-options,
    SetupScreen #pipe-region-map-area,
    SetupScreen #pipe-rosters-area,
    SetupScreen #pipe-scans-area,
    SetupScreen #pipe-preview-output,
    SetupScreen #tmpl-bubble-note,
    SetupScreen #tmpl-batch-area,
    SetupScreen #tmpl-copies-area,
    SetupScreen #tmpl-roster-area {
        height: auto;
    }

    SetupScreen #pipe-roster-list,
    SetupScreen #pipe-scan-list {
        height: 6;
        margin-top: 1;
    }

    SetupScreen Checkbox {
        background: transparent;
        border: none;
        padding: 0;
        margin-top: 1;
    }

    SetupScreen Checkbox:focus {
        background: $boost;
    }
    """

    _PATH_FIELDS: dict[str, tuple[PathPickMode, str]] = {
        "photo-input": ("any", "Choose the folder of photos (or one photo)"),
        "photo-output": ("directory", "Choose the directory for the per-student PDFs"),
        "region-pdf": ("file", "Choose the blank assignment PDF"),
        "region-map-path": ("any", "Choose an existing region map (.regions.json)"),
        "ak-input": ("file", "Choose the answer-key source (.md/.txt/.docx/.pdf)"),
        "ak-output": ("any", "Choose the output QA markdown file"),
        "ak-template-path": ("file", "Choose the blank template PDF"),
        "tmpl-qa-input": ("file", "Choose the QA markdown file"),
        "tmpl-output": ("any", "Choose the output template PDF"),
        "policy-submissions": ("directory", "Choose the submissions directory"),
        "handoff-submissions": ("directory", "Choose the submissions directory"),
        "assistant-folder": ("directory", "Choose the class folder"),
        "backup-submissions": ("directory", "Choose the submissions directory"),
        "tui-class-folder": ("directory", "Choose the class folder"),
        "tui-qa-path": ("file", "Choose the QA markdown file"),
        "tui-sub-path": ("any", "Choose the submissions file or directory"),
        "pipe-scan": ("any", "Choose the scanned bundle PDF, or a folder of split PDFs"),
        "pipe-qa": ("file", "Choose the answer-key markdown file"),
        "pipe-out": ("directory", "Choose the class folder"),
        "pipe-roster": ("file", "Choose the roster/ID file (.tsv or .csv)"),
        "pipe-template": ("file", "Choose the blank template PDF"),
        "pipe-region-map": ("file", "Choose the region map (.regions.json)"),
    }

    class LaunchRequested(Message):
        def __init__(self, qa_path: str, submissions_path: str) -> None:
            super().__init__()
            self.qa_path = qa_path
            self.submissions_path = submissions_path

    class GradeWhileExtractingRequested(Message):
        def __init__(self, qa_path: str, submissions_path: str) -> None:
            super().__init__()
            self.qa_path = qa_path
            self.submissions_path = submissions_path

    def _path_row(
        self,
        placeholder: str,
        input_id: str,
        *,
        hidden: bool = False,
    ) -> Horizontal:
        classes = "path-row hidden" if hidden else "path-row"
        return Horizontal(
            Input(placeholder=placeholder, id=input_id, classes="path-input"),
            Button(
                "Browse",
                id=f"browse-{input_id}",
                classes=f"browse-button {button_roles.OPEN}",
                compact=True,
            ),
            classes=classes,
            id=f"{input_id}-row",
        )

    @staticmethod
    def _field_label(text: str, detail: str = "") -> Static:
        markup = f"[b]{text}[/b]"
        if detail:
            markup = f"{markup}  [dim]{detail}[/dim]"
        return Static(markup, classes="field-label")

    @staticmethod
    def _card_actions(*buttons: Button) -> Horizontal:
        return Horizontal(*buttons, classes="card-actions")

    @staticmethod
    def _ai() -> app_config.ResolvedAI:
        return app_config.resolve()

    def _pipe_jobs(self) -> int | None:
        """The parallel-jobs override: None for automatic, -1 for a bad entry."""
        if not self.query_one("#pipe-parallel", Checkbox).value:
            return None
        jobs = self.query_one("#pipe-jobs", Input).value.strip()
        if not jobs:
            return None
        if not jobs.isdigit() or int(jobs) < 1:
            return -1
        return int(jobs)

    @staticmethod
    def _app_version() -> str:
        try:
            return f"v{package_version('opengrader')}"
        except PackageNotFoundError:
            return ""

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="setup-container"):
            with Vertical(id="setup-content"):
                with Vertical(id="setup-hero"):
                    with Horizontal(id="setup-title-row"):
                        yield Static("OPENGRADER", id="setup-title")
                        yield Static(self._app_version(), id="setup-version")
                    yield Static(
                        "Offline-first assignment grading — split, extract, grade, export.",
                        id="setup-subtitle",
                    )

                with Vertical(id="launch-section") as launch_card:
                    # Choosing what to grade: `opengrader classic` (and the
                    # desktop icon) start here. F3 (Home) while grading comes back.
                    launch_card.border_title = "▸ Start grading"
                    yield Static(
                        "Choose a class folder (or a grader packet) — its answer "
                        "key and submissions are found for you. A folder of new "
                        "scans is prepared first: you are asked, then it runs "
                        "with the settings saved for that folder.",
                        classes="card-hint",
                    )
                    # What is open right now; hidden while nothing is.
                    yield Static(
                        "", id="setup-session", classes="card-hint hidden", markup=False
                    )
                    # The last few class folders graded here; hidden until there
                    # is one. Filled by `refresh_recents`.
                    with Vertical(id="launch-recents", classes="hidden"):
                        yield self._field_label(
                            "Recent class folders", "↩ opens one · d forgets it"
                        )
                        yield RecentList(id="launch-recent-list")
                    yield self._field_label(
                        "Class folder", "holds the answer key and the graded submissions"
                    )
                    yield self._path_row("Path to the class folder…", "tui-class-folder")
                    yield Static(
                        "",
                        id="tui-folder-summary",
                        classes="card-hint hidden",
                        markup=False,
                    )
                    with Collapsible(
                        title="Answer key and submissions — filled in from the folder",
                        id="launch-files",
                        collapsed=True,
                    ):
                        yield self._field_label(
                            "Answer key", "QA markdown file, e.g. solutions.md"
                        )
                        yield self._path_row("Path to QA markdown file…", "tui-qa-path")
                        yield self._field_label(
                            "Submissions", "directory containing .submission.md files"
                        )
                        yield self._path_row(
                            "Path to submissions directory…", "tui-sub-path"
                        )
                    # A run started from this card (`_run_pipeline(from_launch=True)`)
                    # shows here what the pipeline card shows further down.
                    with Vertical(id="launch-progress", classes="hidden"):
                        yield ProgressBar(
                            id="launch-progress-bar", total=None, show_eta=True
                        )
                        yield Static(
                            "",
                            id="launch-progress-status",
                            classes="card-hint",
                            markup=False,
                        )
                    # Opt-in (Tools → Browser app): hidden until the user ticks it.
                    yield self._card_actions(
                        # The answer key's answers as a form: no file to open.
                        # Shown only while the card's key has a question with
                        # no answer (`_refresh_answers_button`).
                        Button(
                            "Add answers…",
                            id="answers-button",
                            compact=True,
                            classes=f"{button_roles.OPEN} hidden",
                        ),
                        # A folder of several assignments: offered when one is
                        # chosen (`_scan_launch_folder`), hidden otherwise.
                        Button(
                            "Make this a workspace…",
                            id="workspace-button",
                            compact=True,
                            classes=f"{button_roles.OPEN} hidden",
                        ),
                        Button(
                            "Open in browser",
                            id="browser-open-button",
                            compact=True,
                            classes=button_roles.BROWSER
                            if browser_app.load_browser_preferences().enabled
                            else f"{button_roles.BROWSER} hidden",
                        ),
                        Button(
                            "Launch Grading",
                            id="launch-button",
                            classes=button_roles.LAUNCH,
                            compact=True,
                        ),
                    )
                    # Where the browser app is running, with Open and Stop.
                    yield BrowserLauncher(id="browser-launcher")

                yield Static(
                    "[b]PREPARE AN ASSIGNMENT[/b]", id="pipeline-heading",
                    classes="section-heading",
                )
                yield Static(
                    "New scans start here. One scanned bundle in, graded work "
                    "out: the scan is checked, split, extracted and auto-graded "
                    "in order. Run it again any time — after an interruption, a "
                    "changed answer key, or late submissions — and it works out "
                    "what is left to do and reuses the rest.",
                    id="pipeline-caption",
                    classes="section-caption",
                )

                with Collapsible(
                    title="Run it — one scan in, graded work out",
                    id="section-pipeline",
                    collapsed=True,
                ):
                    yield Static(
                        "Choose the class folder and press Run Pipeline: the "
                        "scanned bundle, answer key, roster and blank template "
                        "inside it are found for you, and student-pdfs/, submissions/ "
                        "and the run record are built there too. Preview shows "
                        "the plan without writing anything. What you set here is "
                        "remembered for this folder.",
                        classes="card-hint",
                    )
                    yield self._field_label(
                        "Class folder",
                        "holds the scan, answer key and roster; results are built here",
                    )
                    yield self._path_row("Path to the class folder…", "pipe-out")
                    yield Static(
                        "",
                        id="pipe-folder-summary",
                        classes="card-hint hidden",
                        markup=False,
                    )
                    yield Static(
                        "No answer key in this folder yet. [b]Create answer "
                        "key[/b] in Helper Tools below writes one from a "
                        "plain-text assignment or a scanned key.",
                        id="pipe-qa-hint",
                        classes="card-hint hidden",
                    )

                    with Collapsible(
                        title="Settings — pages, roster, template, how it runs",
                        id="pipe-settings",
                        collapsed=True,
                    ):
                        yield self._field_label(
                            "Pages per submission", "leave blank to auto-detect"
                        )
                        yield Input(
                            placeholder="e.g. 6", id="pipe-pages"
                        )
                        yield Static(
                            "This is also what lets the scan pre-flight catch a "
                            "wrong page count or a swapped pair before anything is "
                            "graded — without it, that check can't run at all.",
                            classes="card-hint",
                        )

                        yield Checkbox(
                            "Match student names against a roster", id="pipe-use-roster"
                        )
                        yield self._path_row(
                            "Path to roster file (.tsv or .csv)…", "pipe-roster", hidden=True
                        )

                        yield Checkbox(
                            "Align with a blank template PDF", id="pipe-use-template"
                        )
                        yield self._path_row(
                            "Path to blank template PDF…", "pipe-template", hidden=True
                        )

                        yield Checkbox(
                            "Start grading as submissions arrive",
                            id="pipe-grade-asap",
                            value=True,
                        )
                        yield Static(
                            "On: opens the grading workspace the moment the first "
                            "submission is ready, while the rest keep extracting "
                            "behind it. Off: waits for the whole run to finish "
                            "first. Either way, opening "
                            "automatically needs an answer key.",
                            classes="card-hint",
                        )

                        yield Checkbox("Sort out-of-order pages", id="pipe-reorder")
                        yield Static(
                            "Off, pages are never moved without asking: when a "
                            "submission looks out of order the run pauses before "
                            "extraction and you choose which to sort, keep them as "
                            "scanned, or stop and rearrange the PDFs yourself. Tick "
                            "this to sort them without being asked. With a blank "
                            "template above, the template supplies the order.",
                            classes="card-hint",
                        )

                        yield Checkbox(
                            "Draft missing rubric items with AI first",
                            id="pipe-draft-rubrics",
                        )
                        yield Static(
                            "Before auto-grading, the AI drafts partial-credit "
                            "rubric items for written questions that have none and "
                            "writes them into the answer key. The run then stops "
                            "and opens grading so you can check them (press e on a "
                            "question); press Y there to have the AI grade with "
                            "them, or run the pipeline again.",
                            classes="card-hint",
                        )

                    with Collapsible(
                        title="Advanced settings — overrides, your own assignment, sections, speed",
                        id="pipe-advanced",
                        collapsed=True,
                    ):
                        yield Static(
                            "[b]Override what was found[/b] — only when the "
                            "folder holds more than one candidate, or the scan "
                            "or answer key lives somewhere else. Left blank, "
                            "the class folder's own files are used.",
                            classes="card-hint",
                        )
                        yield self._field_label(
                            "Scanned bundle",
                            "bulk-scan PDF, or a folder of already-split PDFs",
                        )
                        yield self._path_row(
                            "Path to the scanned PDF or split-PDF folder…", "pipe-scan"
                        )
                        yield self._field_label(
                            "Answer key",
                            "optional — without it, the run stops after extraction",
                        )
                        yield self._path_row("Path to QA markdown file…", "pipe-qa")

                        yield Checkbox(
                            "Use a region map (your own assignment)",
                            id="pipe-use-region-map",
                        )
                        with Vertical(id="pipe-region-map-area", classes="hidden"):
                            yield self._path_row(
                                "Path to the region map (.regions.json)…",
                                "pipe-region-map",
                            )
                            yield Static(
                                "For an assignment Opengrader didn't generate: the "
                                "boxes you drew on a blank copy are where the answers "
                                "are, so it needs no text layer of its own. Draw or fix "
                                "one with [b]Draw answer regions[/b] in Helper Tools "
                                "below. This replaces the blank template in Settings — they "
                                "both answer the same question, so only one is used.",
                                classes="card-hint",
                            )

                        yield Checkbox(
                            "One scan per class section",
                            id="pipe-many-scans",
                        )
                        with Vertical(id="pipe-scans-area", classes="hidden"):
                            yield Static(
                                "One scanned PDF per line, in the form "
                                "[b]path/to/section-01.pdf=Section 01[/b]. The "
                                "'=Section' part is optional: a file named after "
                                "its section (section-01.pdf, Sec B.pdf) is filled "
                                "in for you when the folder holds them. Each scan "
                                "is checked, split and read on its own; every "
                                "submission is tagged with its section and all of "
                                "them are graded together, against one answer key. "
                                "A section's roster is the line naming the same "
                                "section under the box below, else the roster in "
                                "Settings. These replace the scanned bundle above.",
                                classes="card-hint",
                            )
                            yield TextArea(id="pipe-scan-list")
                            yield self._card_actions(
                                Button(
                                    "Browse & add scan",
                                    id="browse-pipe-scan-list",
                                    classes=f"browse-button {button_roles.OPEN}",
                                    compact=True,
                                )
                            )

                        yield Checkbox(
                            "This stack mixes several class sections",
                            id="pipe-many-rosters",
                        )
                        with Vertical(id="pipe-rosters-area", classes="hidden"):
                            yield Static(
                                "One roster per line, in the form "
                                "[b]path/to/roster.tsv=SectionName[/b]. The "
                                "'=SectionName' is optional — omit it to name the "
                                "section after the roster filename. Each student is "
                                "routed to the section whose roster holds their ID; "
                                "anyone in no roster is tagged 'unassigned'. These "
                                "are used instead of the single roster in Settings.",
                                classes="card-hint",
                            )
                            yield TextArea(id="pipe-roster-list")
                            yield self._card_actions(
                                Button(
                                    "Browse & add roster",
                                    id="browse-pipe-roster-list",
                                    classes=f"browse-button {button_roles.OPEN}",
                                    compact=True,
                                )
                            )

                        yield Checkbox(
                            "Set how many to extract at once",
                            id="pipe-parallel",
                        )
                        yield Static(
                            "Extraction already runs on several CPU cores at once. "
                            "Left unchecked, Opengrader picks a safe number for "
                            "this machine — tick this only to override it.",
                            classes="card-hint",
                        )
                        with Vertical(id="pipe-parallel-options", classes="hidden"):
                            yield self._field_label(
                                "Parallel jobs",
                                f"blank = automatic (this machine has {_CPU_COUNT} cores)",
                            )
                            yield Input(
                                placeholder=f"1–{_CPU_COUNT}",
                                id="pipe-jobs",
                                classes="path-input",
                            )
                            yield Static(
                                "Lower this if the machine runs short on memory during "
                                "a big batch, or set 1 to extract one submission at a "
                                "time. Going higher than the automatic number rarely "
                                "helps: with the vision model on, the limit is how many "
                                "requests Ollama serves at once, not the CPU.",
                                classes="card-hint",
                            )

                        yield Checkbox(
                            "Grade it anyway (ignore scan warnings)", id="pipe-force"
                        )
                        yield Static(
                            "The scan pre-flight can stop the run over a "
                            "high-severity finding — pages upside down, wildly out "
                            "of order, unreadable. Tick this only after reading "
                            "what it found (it still gets reported) and deciding "
                            "to grade the scan as it is.",
                            classes="card-hint",
                        )

                        yield Checkbox(
                            "Read the scans without an answer key", id="pipe-no-key"
                        )
                        yield Static(
                            "Scans of your own assignment — no blank template, "
                            "no points printed beside the question numbers — "
                            "are split and then wait for the answer key: it is "
                            "what says which questions and parts to look for. "
                            "Tick this to read them now anyway, by whatever "
                            "numbers the pages print.",
                            classes="card-hint",
                        )

                        yield Checkbox(
                            "Start over (ignore the earlier run)", id="pipe-fresh"
                        )
                        yield Static(
                            "A run normally reuses every stage whose inputs haven't "
                            "changed. Tick this to check and split the scan again "
                            "and auto-grade again regardless. Submissions already "
                            "extracted from an unchanged PDF are still kept, so "
                            "grades you have entered are never extracted over.",
                            classes="card-hint",
                        )

                    yield Static(
                        self._ai_status_text("pipe"),
                        id="pipe-ai-status",
                        classes="card-hint",
                    )

                    yield self._card_actions(
                        Button("Preview", id="pipe-preview", compact=True),
                        Button(
                            "Run Pipeline",
                            id="pipe-run",
                            classes=button_roles.LAUNCH,
                            compact=True,
                        ),
                    )
                    # Preview: what a run would do in plain words first, then
                    # the tools' own plan for anyone who wants it.
                    with Vertical(id="pipe-preview-output", classes="hidden"):
                        yield Static("", id="pipe-preview-summary", markup=False)
                        with Collapsible(
                            title="Technical details — each step and its command",
                            id="pipe-preview-technical",
                            collapsed=True,
                        ):
                            yield Static(
                                "",
                                id="pipe-preview-details",
                                classes="card-hint",
                                markup=False,
                            )
                    with Vertical(id="pipe-progress", classes="hidden"):
                        yield ProgressBar(
                            id="pipe-progress-bar", total=None, show_eta=True
                        )
                        yield Static("", id="pipe-progress-status", classes="card-hint")
                        yield Static("", id="pipe-stage-log", classes="card-hint")

                yield Static(
                    "[b]HELPER TOOLS[/b]", id="helpers-heading",
                    classes="section-heading",
                )
                yield Static(
                    "Optional utilities — getting scans in, printing QA files, "
                    "sharing and checking work. Use them as needed, in any order.",
                    id="helpers-caption",
                    classes="section-caption",
                )

                with Collapsible(
                    title="Check this computer — is everything installed?",
                    id="section-doctor",
                    collapsed=True,
                ):
                    yield Static(
                        "Opengrader needs a few things Python cannot install for "
                        "itself: the text recognition that reads scans, and "
                        "(optionally) the local AI that transcribes handwriting "
                        "and helps grade written answers. This checks each one "
                        "and prints the exact command to fix anything missing.",
                        classes="card-hint",
                    )
                    yield Static(
                        "Run it first on a new computer, and any time something "
                        "misbehaves — empty answers after extraction, answer "
                        "images showing as coloured blocks, or auto-grading "
                        "refusing to start. It changes nothing unless you press "
                        "its Install Tesseract button (Mac with Homebrew).",
                        classes="card-hint",
                    )
                    yield self._card_actions(
                        Button(
                            "Check this computer",
                            id="run-doctor",
                            classes=button_roles.OPEN,
                            compact=True,
                        )
                    )

                with Collapsible(
                    title="AI assistant — Claude, ChatGPT, … with no names or IDs",
                    id="section-assistant",
                    collapsed=True,
                ):
                    yield self._field_label(
                        "Class folder", "blank = the one on the Start grading card"
                    )
                    yield self._path_row("Path to the class folder…", "assistant-folder")
                    yield Static("", id="assistant-state", classes="card-hint")
                    yield Static(
                        "Lets the AI assistant you already use do the AI work. "
                        "Only the jobs that fit the folder are offered: the "
                        "answer key while there is none or it has blank "
                        "answers (no student data involved), then grading and "
                        "drafting partial credit once there are submissions. "
                        "Scanning, splitting and reading scans stay on this "
                        "computer.",
                        classes="card-hint",
                    )
                    yield Static(
                        "The assistant always works in its own folder beside "
                        "the class folder, never in it. For grading that is a "
                        "copy where every student is a code: names, IDs and "
                        "scans stay here, every answer image is checked for "
                        "the student's name first, and the copy refuses to "
                        "exist if a name or ID is still in it. Its grades come "
                        "back as AI grades for you to confirm, and a grade you "
                        "made is never changed.",
                        classes="card-hint",
                    )
                    with Horizontal(classes="card-actions", id="assistant-key-actions"):
                        yield Button(
                            "Draft a standard key",
                            id="run-assistant-draft-key",
                            classes=button_roles.LAUNCH,
                            compact=True,
                        )
                        yield Button(
                            "Type the answers…",
                            id="run-assistant-type-answers",
                            classes=button_roles.OPEN,
                            compact=True,
                        )
                        yield Button(
                            "Answers from a list…",
                            id="run-assistant-list-answers",
                            classes=button_roles.OPEN,
                            compact=True,
                        )
                        yield Button(
                            "Write the key with my assistant…",
                            id="run-assistant-key",
                            classes=button_roles.OPEN,
                            compact=True,
                        )
                    with Horizontal(classes="card-actions", id="assistant-grade-actions"):
                        yield Button(
                            "Draft rubrics…",
                            id="run-assistant-rubrics",
                            classes=button_roles.OPEN,
                            compact=True,
                        )
                        yield Button(
                            "Grade…",
                            id="run-assistant",
                            classes=button_roles.OPEN,
                            compact=True,
                        )

                with Collapsible(
                    title="AI models & providers — Opengrader's own AI (optional, advanced)",
                    id="section-ai",
                    collapsed=True,
                ):
                    yield Static(
                        "Most teachers do not need this: the AI assistant tool "
                        "above lets the assistant you already use do the AI "
                        "work. This is for having Opengrader call a model "
                        "itself while it reads scans and auto-grades: a local "
                        "model you run in Ollama ('opengrader ai-readme "
                        "--local' has the steps), or a cloud service.",
                        classes="card-hint",
                    )
                    yield Static(
                        "AI setup chooses the provider and models and what the "
                        "AI does (read handwriting, suggest grades). It applies "
                        "everywhere: Prepare an assignment above, the tools "
                        "below, and the same commands in a terminal. Providers "
                        "adds a service your department runs: pick it, paste "
                        "its key (kept in this computer's password store, never "
                        "in an Opengrader file) and test it.",
                        classes="card-hint",
                    )
                    yield Static(
                        "A service that is not on this computer or your local "
                        "network receives student work: answer images and "
                        "handwriting. Check with your institution before you "
                        "choose one.",
                        classes="card-hint",
                    )
                    yield Static(
                        self._ai_summary_text(),
                        id="ai-current-summary",
                        classes="card-hint",
                    )
                    with Horizontal(classes="card-actions"):
                        yield Button(
                            "Open AI setup",
                            id="run-ai-settings",
                            classes=button_roles.OPEN,
                            compact=True,
                        )
                        yield Button(
                            "Manage providers",
                            id="run-providers",
                            classes=button_roles.OPEN,
                            compact=True,
                        )

                with Collapsible(
                    title="Browser app (optional) — grade a class folder in a web browser",
                    id="section-browser",
                    collapsed=True,
                ):
                    yield Static(
                        "Off by default: everything here works without it. Turn "
                        "it on and the Start grading card gets an Open in "
                        "browser button that opens the chosen class folder in "
                        "your web browser. It still runs only on this computer.",
                        classes="card-hint",
                    )
                    yield Checkbox(
                        "Show Open in browser on the Start grading card",
                        value=browser_app.load_browser_preferences().enabled,
                        id="browser-enabled",
                    )
                    yield Static(
                        "The browser grades a copy of the folder kept in a "
                        "workspace (.opengrader/ inside the class folder), so "
                        "grades entered there are not in the .submission.md "
                        "files until you write them back: `opengrader tui`, "
                        "open the assignment, press W. Grades already in the "
                        "files come along the first time you open a folder.",
                        classes="card-hint",
                    )
                    yield BrowserHealth(
                        id="browser-health",
                        classes=""
                        if browser_app.load_browser_preferences().enabled
                        else "hidden",
                    )

                with Collapsible(
                    title="Import phone photos — per-student PDFs from a folder of photos",
                    id="section-photo-import",
                    collapsed=True,
                ):
                    yield Static(
                        "For a class that hands in photos instead of a scanned "
                        "bundle. Each photo is straightened — by the corner "
                        "registration marks when the sheet has them, by the "
                        "page's own edges otherwise — and the photos belonging "
                        "to one student are bound into a single PDF. Your photos "
                        "are only ever read; everything written goes to the "
                        "output directory.",
                        classes="card-hint",
                    )
                    yield Static(
                        "What comes out is a folder of per-student PDFs, which "
                        "[b]Prepare an assignment[/b] above takes as its scanned "
                        "bundle — it is filled in there for you. iPhone photos "
                        "in HEIC format need one extra install; if yours are, "
                        "the exact command is named.",
                        classes="card-hint",
                    )
                    yield self._field_label(
                        "Photos", "a folder of photos, or one subfolder per student"
                    )
                    yield self._path_row("Path to the photos…", "photo-input")
                    yield self._field_label("Output directory")
                    yield self._path_row(
                        "Where to write per-student PDFs…", "photo-output"
                    )
                    yield self._field_label(
                        "Photos per student", "leave blank to group automatically"
                    )
                    yield Input(
                        placeholder="e.g. 3 (blank = group automatically)",
                        id="photo-pages",
                    )
                    yield Static(
                        "Left blank: one subfolder per student is used if that's "
                        "how the photos are arranged, otherwise the leading part "
                        "of each filename (s1234-p1.jpg, s1234-p2.jpg → one "
                        "student), otherwise one photo per student. Fill it in "
                        "for a flat folder of, say, three pages each in order.",
                        classes="card-hint",
                    )
                    yield Checkbox("Pages are A4, not US Letter", id="photo-a4")
                    yield Checkbox(
                        "Straighten only, no corner correction",
                        id="photo-no-perspective",
                    )
                    yield Static(
                        "Rotates a tilted photo upright without pulling its "
                        "corners back to square. Worth trying when full "
                        "correction makes a photo worse, which usually means the "
                        "page was curled or folded rather than lying flat.",
                        classes="card-hint",
                    )
                    yield self._card_actions(
                        Button(
                            "Import Photos",
                            id="run-photo-import",
                            classes=button_roles.LAUNCH,
                            compact=True,
                        )
                    )
                    with Vertical(id="photo-progress", classes="hidden"):
                        yield Static("", id="photo-progress-status", classes="card-hint")

                with Collapsible(
                    title="Draw answer regions — an assignment Opengrader didn't make",
                    id="section-region-map",
                    collapsed=True,
                ):
                    yield Static(
                        "Opens a blank copy of your own assignment and lets you "
                        "draw a box around each answer area, saved as a region map "
                        "next to the PDF. Extraction then crops those boxes "
                        "directly, so it needs no text layer and no Opengrader "
                        "template — a flat scan of a paper you wrote years ago "
                        "works. Do it once per assignment.",
                        classes="card-hint",
                    )
                    yield Static(
                        "Every action has a key: [b]j/k[/b] select, arrows move, "
                        "shift+arrows resize, and the panel on the right sets each "
                        "question's number, points and type. Saving is refused "
                        "while anything is actually wrong.",
                        classes="card-hint",
                    )
                    yield self._field_label(
                        "Blank assignment PDF",
                        "an unmarked copy of the assignment itself",
                    )
                    yield self._path_row(
                        "Path to the blank assignment PDF…", "region-pdf"
                    )
                    yield self._field_label(
                        "Existing region map",
                        "optional — leave blank to start a new one",
                    )
                    yield self._path_row(
                        "Path to an existing .regions.json…", "region-map-path"
                    )
                    yield self._card_actions(
                        Button(
                            "Draw answer regions",
                            id="run-region-map",
                            classes=button_roles.OPEN,
                            compact=True,
                        )
                    )

                with Collapsible(
                    title="Create answer key — from a plain-text assignment or a scanned PDF",
                    id="section-answer-key",
                    collapsed=True,
                ):
                    yield self._field_label(
                        "Answer-key source",
                        "a text file (.md/.txt), a Word document (.docx) or an answer-key PDF",
                    )
                    yield self._path_row("Path to answer-key source…", "ak-input")
                    yield self._field_label("Output QA markdown")
                    yield self._path_row("Where to write the answer key…", "ak-output")
                    yield self._field_label("Title", "optional")
                    yield Input(placeholder="e.g. CS 146 Quiz 1", id="ak-title")
                    yield self._field_label(
                        "Points when none are given",
                        f"blank = {format_points(DEFAULT_QUESTION_POINTS)} · "
                        "partial credit is half",
                    )
                    yield Input(
                        placeholder=format_points(DEFAULT_QUESTION_POINTS),
                        id="ak-default-points",
                    )
                    yield Static(
                        "Only for a question whose source states no points; "
                        "points it does state are kept. Remembered on this "
                        "computer.",
                        classes="card-hint",
                    )
                    yield Checkbox(
                        "Scanned PDF: align with a blank template", id="ak-use-template"
                    )
                    yield self._path_row(
                        "Path to blank template PDF…", "ak-template-path", hidden=True
                    )
                    yield Static(
                        self._ai_status_text("ak"),
                        id="ak-ai-status",
                        classes="card-hint",
                    )
                    yield Static(
                        "A text or Word file needs only numbered questions and "
                        "their answers: [b]1. What is the capital of France?[/b] "
                        "then [b]Answer: Paris[/b], with one Answer: line for "
                        "each answer you accept. Number questions 1. or 1) and "
                        "parts 1a) 1b); points are optional, anywhere on the "
                        "question's line: [b](10 points)[/b]. A PDF saved from "
                        "Word or Google Docs is read the same way. "
                        "Plain-text files are read exactly. A born-digital PDF is "
                        "read directly from its text layer (fast, exact); a scanned "
                        "or image PDF is read with OCR, and with the handwriting "
                        "model from [b]AI setup[/b] when it is turned on, into a "
                        "draft to review. New to the format? "
                        "Press [b]t[/b] for copy-paste templates.",
                        classes="card-hint",
                    )
                    yield self._card_actions(
                        Button(
                            "Create answer key",
                            id="run-answer-key",
                            classes=button_roles.LAUNCH,
                            compact=True,
                        )
                    )
                    with Vertical(id="ak-progress", classes="hidden"):
                        yield ProgressBar(
                            id="ak-progress-bar", total=None, show_eta=True
                        )
                        yield Static("", id="ak-progress-status", classes="card-hint")

                with Collapsible(
                    title="Generate template — printable blank PDF from a QA file",
                    id="section-template",
                    collapsed=True,
                ):
                    yield self._field_label("QA markdown", "the answer key to print from")
                    yield self._path_row("Path to QA markdown file…", "tmpl-qa-input")
                    yield self._field_label("Output template PDF")
                    yield self._path_row("Where to write the template PDF…", "tmpl-output")
                    yield self._field_label("Title", "optional — required if the QA file has none")
                    yield Input(placeholder="e.g. CS 146 Quiz 1", id="tmpl-title")
                    yield Checkbox(
                        "Generate every version",
                        id="tmpl-all-versions",
                    )
                    yield Static(
                        "One template PDF per version section in the QA file, "
                        "rather than only the first.",
                        classes="card-hint",
                    )
                    yield Checkbox("Add QR codes to every page", id="tmpl-qr")
                    yield Static(
                        "Prints the version, page number and page count on each "
                        "sheet in machine-readable form, so splitting and page "
                        "ordering are read exactly instead of inferred. The sheets "
                        "stay identical, so they can be handed out in any order.",
                        classes="card-hint",
                    )
                    yield Checkbox(
                        "Bubble answer sheet instead",
                        id="tmpl-bubble-sheet",
                    )
                    with Vertical(id="tmpl-bubble-note", classes="hidden"):
                        yield Static(
                            "A scantron-style sheet: one row of circles per "
                            "multiple-choice question. Marks are measured, not "
                            "read, so extraction needs no Tesseract and no AI "
                            "model and is far faster for a large class — pair it "
                            "with a roster below for one QR-identified sheet per "
                            "student. Questions that are not multiple choice "
                            "cannot be bubbled; any that get left off are named "
                            "when the sheet is generated.",
                            classes="card-hint",
                        )
                    with Vertical(id="tmpl-roster-area", classes="hidden"):
                        yield self._field_label(
                            "Personalize per student (optional)",
                            "roster TSV 'ID<TAB>NAME' (name-only rows get a 6-digit id)",
                        )
                        yield self._path_row(
                            "Path to roster file… — one QR sheet per student + master key",
                            "tmpl-roster",
                        )
                        yield Static(
                            "With a roster: output is one PDF whose first page is the "
                            "teacher's master key (id → name); each sheet is anonymous "
                            "(prints the id only) and identity is read from its QR — no "
                            "handwriting OCR, no duplicate-name guessing.",
                            classes="card-hint",
                        )
                    with Vertical(id="tmpl-batch-area", classes="hidden"):
                        yield Checkbox(
                            "Scramble the question order on every sheet",
                            id="tmpl-shuffle",
                        )
                        with Vertical(id="tmpl-copies-area", classes="hidden"):
                            yield self._field_label(
                                "How many sheets",
                                "leave blank if you chose a roster above — it sets the count",
                            )
                            yield Input(placeholder="e.g. 120", id="tmpl-copies")
                            yield Static(
                                "Scrambling gives each sheet its own question order so "
                                "neighbours cannot copy by position. Each sheet prints a "
                                "short form code (also in its QR) and extraction re-derives "
                                "that sheet's order from it — you keep nothing and can "
                                "reprint at any time. Scan the whole page: crop the header "
                                "off and the order is lost.",
                                classes="card-hint",
                            )
                    yield self._card_actions(
                        Button(
                            "Generate Template",
                            id="run-template",
                            classes=button_roles.LAUNCH,
                            compact=True,
                        )
                    )

                with Collapsible(
                    title="Re-read unclear answers — a second pass over what was extracted",
                    id="section-refine",
                    collapsed=True,
                ):
                    yield Static(
                        "For a class already prepared above: only the "
                        "submissions holding a low-confidence transcription are "
                        "re-read, and the new reading is kept only where it "
                        "beats the old one. Grades, comments and every answer "
                        "that was already clear are left alone. Give it "
                        "something the first pass didn't have — a higher DPI is "
                        "the cheapest thing that helps.",
                        classes="card-hint",
                    )
                    yield Static(
                        "It reads the class folder, template, region map and "
                        "rosters chosen in [b]Prepare an assignment[/b] above, "
                        "so the second pass crops exactly where the first did.",
                        classes="card-hint",
                    )
                    yield self._field_label(
                        "Re-read at DPI",
                        "blank = the same resolution as the first pass",
                    )
                    yield Input(
                        placeholder="e.g. 450",
                        id="refine-dpi",
                        classes="path-input",
                    )
                    yield Static(
                        self._ai_status_text("ext"),
                        id="refine-ai-status",
                        classes="card-hint",
                    )
                    yield self._card_actions(
                        Button(
                            "Re-read unclear answers",
                            id="run-refine",
                            classes=button_roles.LAUNCH,
                            compact=True,
                        )
                    )
                    with Vertical(id="refine-progress", classes="hidden"):
                        yield ProgressBar(
                            id="refine-progress-bar", total=None, show_eta=True
                        )
                        yield Static("", id="refine-progress-status", classes="card-hint")

                with Collapsible(
                    title="Calibrate AI grading — how far can you trust the model?",
                    id="section-llm-eval",
                    collapsed=True,
                ):
                    yield Static(
                        "Auto-grade writes an AI suggestion when the model is "
                        "confident enough — but how confident is confident "
                        "enough? Grade some free responses by hand, then run "
                        "this: it re-grades those same answers with the model, "
                        "reports how often it agreed with you (per model and per "
                        "confidence level), and names the minimum confidence the "
                        "evidence supports. It never writes a grade — it only "
                        "measures. Measure a past assignment you finished "
                        "marking for a bigger, better sample.",
                        classes="card-hint",
                    )
                    yield self._card_actions(
                        Button(
                            "Open calibration",
                            id="run-llm-eval",
                            classes=button_roles.OPEN,
                            compact=True,
                        )
                    )

                with Collapsible(
                    title="Course gradebook — a whole semester's grades in one table",
                    id="section-course",
                    collapsed=True,
                ):
                    yield Static(
                        "One folder per assignment is right for grading and "
                        "wrong for a semester. Point this at a course.toml "
                        "listing your assignment folders and their weights to "
                        "get each student's weighted course grade and letter, "
                        "who is at risk, how each student is trending, and — "
                        "if your answer keys tag learning outcomes — mastery "
                        "across the whole course. It reads your assignment "
                        "folders and never writes into them.",
                        classes="card-hint",
                    )
                    yield self._card_actions(
                        Button(
                            "Open course gradebook",
                            id="run-course-grades",
                            classes=button_roles.OPEN,
                            compact=True,
                        )
                    )

                with Collapsible(
                    title="Set grading policy — lock/unlock anonymity for an existing folder",
                    id="section-policy",
                    collapsed=True,
                ):
                    yield self._field_label(
                        "Submissions", "directory of .submission.md files"
                    )
                    yield self._path_row(
                        "Path to submissions directory…", "policy-submissions"
                    )
                    yield Checkbox(
                        "Lock anonymous grading for TAs",
                        id="policy-lock-anon",
                    )
                    yield Static(
                        "Checked = graders can't reveal names or view grade "
                        "history. Unchecked = solo (toggle freely). Applies to "
                        "everyone who opens this folder; set it as the teacher.",
                        classes="card-hint",
                    )
                    yield Checkbox(
                        "Randomize student order per grader",
                        id="policy-shuffle",
                    )
                    yield Static(
                        "Each grader walks the class in their own fixed order "
                        "instead of alphabetically, so tiredness late in a "
                        "session doesn't always land on the same students. The "
                        "order is stable per grader — leaving and reopening "
                        "returns you to the same sequence — and nothing else "
                        "changes: the gradebook, exports and reports stay in "
                        "their usual order.",
                        classes="card-hint",
                    )
                    yield self._card_actions(
                        Button(
                            "Apply Policy",
                            id="run-policy",
                            classes=button_roles.LAUNCH,
                            compact=True,
                        )
                    )

                with Collapsible(
                    title="Share with another grader — hand out a copy, merge it back",
                    id="section-handoff",
                    collapsed=True,
                ):
                    yield self._field_label(
                        "Submissions", "the class to share, or to merge into"
                    )
                    yield self._path_row(
                        "Path to submissions directory…", "handoff-submissions"
                    )
                    yield Static(
                        "Work claims divide a class everyone opens in one "
                        "shared folder. This is for the other arrangement: a TA "
                        "takes a copy away and grades it on their own machine. "
                        "The copy they get replaces every name and student id "
                        "with a code and leaves the scanned assignments behind, so "
                        "handing it over gives nothing away — only you hold the "
                        "file that turns the codes back into students, and "
                        "merging their work back needs it.",
                        classes="card-hint",
                    )
                    yield Static(
                        "Merging is a real three-way merge: answers only they "
                        "graded are taken, answers only you graded are kept, "
                        "and anything you both changed is shown for you to "
                        "decide, one row at a time. Nothing is written until "
                        "you say so, and an applied merge can be undone in one "
                        "command.",
                        classes="card-hint",
                    )
                    yield Static(
                        "The copy is written to a grader-copies/ folder beside "
                        "your submissions directory; tick the box in the dialog "
                        "to put it somewhere else. Both are also on hotkeys "
                        "while you grade: shift+E makes a copy, shift+M merges "
                        "one back.",
                        classes="card-hint",
                    )
                    with Horizontal(classes="card-actions"):
                        yield Button(
                            "Create grader copy…",
                            id="run-handoff-pack",
                            classes=button_roles.OPEN,
                            compact=True,
                        )
                        yield Button(
                            "Merge a copy back…",
                            id="run-handoff-merge",
                            classes=button_roles.OPEN,
                            compact=True,
                        )

                with Collapsible(
                    title="Backup & integrity — checksum, verify, archive",
                    id="section-backup",
                    collapsed=True,
                ):
                    yield self._field_label(
                        "Submissions", "the folder to fingerprint or archive"
                    )
                    yield self._path_row(
                        "Path to submissions directory…", "backup-submissions"
                    )
                    yield Static(
                        "Grades, crops and the audit journal are files in one "
                        "folder, with no server holding a second copy. Record a "
                        "checksum of every file now and you can ask later what "
                        "moved — and a changed grade file is reported as "
                        "explained (grading was recorded for that student) or "
                        "unexplained (nothing in the audit trail says why), "
                        "which is the one worth chasing. The archive it writes "
                        "is read back and verified before it claims success.",
                        classes="card-hint",
                    )
                    yield Static(
                        "Checksums live inside the folder they describe "
                        "(.opengrader/integrity.json) — they are a fingerprint "
                        "of it and only work beside it. The archive goes next "
                        "to the folder instead, never inside it, since a backup "
                        "kept there is lost with what it protects; tick the box "
                        "in the dialog to choose somewhere else. Also on "
                        "shift+B while you grade.",
                        classes="card-hint",
                    )
                    yield self._card_actions(
                        Button(
                            "Open backup tools",
                            id="run-backup",
                            classes=button_roles.OPEN,
                            compact=True,
                        )
                    )

    def on_mount(self) -> None:
        self._restore_machine_settings()
        self._refresh_assistant_card()

    def on_descendant_blur(self, event: events.DescendantBlur) -> None:
        # Leaving the class-folder box counts as choosing that folder.
        if event.widget.id == "pipe-out":
            self._load_class_folder(only_if_changed=True)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id in ("tmpl-qr", "tmpl-bubble-sheet", "tmpl-shuffle"):
            self._sync_template_options()
        elif event.checkbox.id == "ak-use-template":
            self.query_one("#ak-template-path-row").set_class(not event.value, "hidden")
        elif event.checkbox.id == "pipe-use-roster":
            self.query_one("#pipe-roster-row").set_class(not event.value, "hidden")
        elif event.checkbox.id == "pipe-use-template":
            # The box as it is now, not as it was when this was posted: a
            # folder scan ticks it and the saved settings may untick it again
            # before either message arrives, and the first must not then
            # untick a saved region map.
            ticked = event.checkbox.value
            self.query_one("#pipe-template-row").set_class(not ticked, "hidden")
            # A template and a region map answer the same question.
            if ticked:
                self.query_one("#pipe-use-region-map", Checkbox).value = False
        elif event.checkbox.id == "pipe-use-region-map":
            ticked = event.checkbox.value
            self.query_one("#pipe-region-map-area").set_class(not ticked, "hidden")
            if ticked:
                self.query_one("#pipe-use-template", Checkbox).value = False
        elif event.checkbox.id == "pipe-many-rosters":
            self.query_one("#pipe-rosters-area").set_class(not event.value, "hidden")
        elif event.checkbox.id == "pipe-many-scans":
            self.query_one("#pipe-scans-area").set_class(not event.value, "hidden")
        elif event.checkbox.id == "pipe-parallel":
            self.query_one("#pipe-parallel-options").set_class(not event.value, "hidden")
        elif event.checkbox.id == "browser-enabled":
            self._set_browser_enabled(event.value)

    def _open_path_picker(self, input_id: str) -> None:
        mode, title = self._PATH_FIELDS[input_id]
        input_widget = self.query_one(f"#{input_id}", Input)

        start_path: Path | None = None
        current = self._clean_path(input_widget.value)
        if current:
            candidate = Path(current)
            if candidate.is_dir():
                start_path = candidate
            elif candidate.parent.is_dir():
                start_path = candidate.parent

        def _apply(path: Path | None) -> None:
            if path is None:
                return
            input_widget.value = str(path)
            input_widget.cursor_position = len(input_widget.value)
            if input_id == "policy-submissions":
                self._sync_policy_checkbox()
            elif input_id == "pipe-out":
                self._load_class_folder()
            elif input_id == "tui-class-folder":
                self._scan_launch_folder()

        self.app.push_screen(
            PathPickerScreen(title=title, mode=mode, start_path=start_path),
            _apply,
        )

    def _append_roster_path(
        self,
        list_id: str = "pipe-roster-list",
        title: str = "Choose a roster file (.tsv or .csv)",
    ) -> None:
        text_area = self.query_one(f"#{list_id}", TextArea)

        def _apply(path: Path | None) -> None:
            if path is None:
                return
            existing = text_area.text
            if existing and not existing.endswith("\n"):
                existing += "\n"
            text_area.text = f"{existing}{path}\n"

        self.app.push_screen(PathPickerScreen(title=title, mode="file"), _apply)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "browse-pipe-roster-list":
            event.stop()
            self._append_roster_path()
            return
        if button_id == "browse-pipe-scan-list":
            event.stop()
            self._append_roster_path(
                "pipe-scan-list", "Choose one section's scanned PDF"
            )
            return
        if button_id.startswith("browse-"):
            input_id = button_id.removeprefix("browse-")
            if input_id in self._PATH_FIELDS:
                event.stop()
                self._open_path_picker(input_id)
                return
        if event.button.id == "launch-button":
            self._launch_grading()
        elif event.button.id == "browser-open-button":
            self._open_in_browser()
        elif event.button.id == "answers-button":
            self._add_answers()
        elif event.button.id == "workspace-button":
            self._on_workspace_button()
        elif event.button.id == "run-answer-key":
            self._run_answer_key()
        elif event.button.id == "run-refine":
            self._run_refine()
        elif event.button.id == "run-template":
            self._run_template()
        elif event.button.id == "run-llm-eval":
            self._open_llm_eval()
        elif event.button.id == "run-course-grades":
            self._open_course_grades()
        elif event.button.id == "run-policy":
            self._run_policy()
        elif event.button.id == "run-assistant":
            self._open_assistant()
        elif event.button.id == "run-assistant-key":
            self._open_assistant_key()
        elif event.button.id == "run-assistant-rubrics":
            self._open_assistant(purpose="rubrics")
        elif event.button.id == "run-assistant-draft-key":
            self._assistant_draft_key()
        elif event.button.id == "run-assistant-type-answers":
            self._assistant_type_answers()
        elif event.button.id == "run-assistant-list-answers":
            self._assistant_list_answers()
        elif event.button.id == "run-handoff-pack":
            self._open_grader_packet()
        elif event.button.id == "run-handoff-merge":
            self._open_merge()
        elif event.button.id == "run-backup":
            self._open_backup()
        elif event.button.id == "run-doctor":
            self._open_doctor()
        elif event.button.id == "run-ai-settings":
            self._open_ai_settings()
        elif event.button.id == "run-providers":
            self._open_providers()
        elif event.button.id == "run-photo-import":
            self._run_photo_import()
        elif event.button.id == "run-region-map":
            self._open_region_map()
        elif event.button.id == "pipe-run":
            self._run_pipeline()
        elif event.button.id == "pipe-preview":
            self._run_pipeline_preview()

    def _handoff_submissions(self, input_id: str) -> str:
        own = self._clean_path(self.query_one(f"#{input_id}", Input).value)
        if own:
            return own
        return self._session_paths()[1]

    # -- AI assistant card ----------------------------------------------------

    def _assistant_folder(self) -> str:
        return self._clean_path(self.query_one("#assistant-folder", Input).value) or self._clean_path(
            self.query_one("#tui-class-folder", Input).value
        )

    def _assistant_state(self):  # noqa: ANN202 - ClassState | None
        from opengrader.core.agent.class_state import read_class_state

        folder = self._assistant_folder()
        if not folder or not Path(folder).is_dir():
            return None
        try:
            return read_class_state(folder)
        except (OSError, ValueError):
            return None

    def _refresh_assistant_card(self) -> None:
        """Say what the folder has, and offer only the jobs that fit it."""
        state = self._assistant_state()
        shown = {
            "run-assistant-draft-key": bool(state and state.can_draft_standard_key),
            "run-assistant-type-answers": bool(state and state.qa_file and state.unanswered and not state.key_problem),
            "run-assistant-list-answers": bool(state and state.qa_file and state.unanswered and not state.key_problem),
            "run-assistant-key": bool(state and state.needs_key),
            "run-assistant-rubrics": bool(state and state.can_draft_rubrics),
            "run-assistant": bool(state and state.can_grade),
        }
        for button_id, visible in shown.items():
            self.query_one(f"#{button_id}", Button).display = visible
        self.query_one("#assistant-key-actions").display = any(
            shown[b] for b in ("run-assistant-draft-key", "run-assistant-type-answers", "run-assistant-list-answers", "run-assistant-key")
        )
        self.query_one("#assistant-grade-actions").display = shown["run-assistant-rubrics"] or shown["run-assistant"]
        if state is None:
            text = "Choose a class folder to see what your assistant can do with it."
        else:
            text = f"Found: {state.describe()}."
            if state.can_draft_standard_key:
                text += " Draft a standard key from the blank assignment, then add its answers."
            elif state.qa_file is None:
                text += " Write the key with your assistant from your own answer key."
            elif not state.submission_count:
                text += " Grading and rubrics appear once the scans are read."
        self.query_one("#assistant-state", Static).update(text)

    @on(Input.Changed, "#assistant-folder")
    def _on_assistant_folder_changed(self, event: Input.Changed) -> None:
        self._refresh_assistant_card()

    @on(Collapsible.Expanded, "#section-assistant")
    def _on_assistant_card_opened(self, event: Collapsible.Expanded) -> None:
        self._refresh_assistant_card()

    def _open_assistant(self, purpose: str = "grading") -> None:
        state = self._assistant_state()
        if state is None or not state.can_grade:
            self.app.notify("Choose a class folder with an answer key and submissions.", severity="error")
            return
        self.app.push_screen(
            AssistantScreen(
                submissions_path=str(state.submissions),
                qa_file_path=str(state.qa_file),
                purpose=purpose,
            ),
            lambda _result: self._refresh_assistant_card(),
        )

    def _open_assistant_key(self) -> None:
        state = self._assistant_state()
        if state is None:
            self.app.notify("Choose the class folder first.", severity="error")
            return
        self.app.push_screen(
            AnswerKeyAssistantScreen(
                class_folder=str(state.folder),
                qa_file=str(state.qa_file) if state.qa_file else "",
                submissions=str(state.submissions) if state.submissions else "",
            ),
            lambda _result: self._refresh_assistant_card(),
        )

    def _assistant_draft_key(self) -> None:
        from opengrader.core.qa_file.draft_key import draft_from_blank_template

        state = self._assistant_state()
        if state is None or not state.can_draft_standard_key:
            return
        points = load_machine_settings().answer_key_points or DEFAULT_QUESTION_POINTS
        key = draft_from_blank_template(state.blank_template, default_points=points)
        if key is None:
            self.app.notify(
                f"{state.blank_template.name} has no text to read questions from (a scanned "
                "template?). Write the key with your assistant instead.",
                severity="warning",
            )
            return
        output = state.folder / "qa.md"
        try:
            with output.open("x", encoding="utf-8") as handle:
                handle.write(key.markdown)
        except OSError as exc:
            self.app.notify(f"Could not write {output.name}: {exc}", severity="error")
            return
        defaulted = f", {key.defaulted} given the standard points" if key.defaulted else ""
        self.app.notify(
            f"Drafted {output.name}: {key.graded} question(s){defaulted}, every answer blank. "
            "Add the answers next."
        )
        self._refresh_assistant_card()

    def _assistant_type_answers(self) -> None:
        state = self._assistant_state()
        if state is None or state.qa_file is None:
            return
        self.app.push_screen(
            AnswerFillScreen(state.qa_file), lambda _saved: self._refresh_assistant_card()
        )

    def _assistant_list_answers(self) -> None:
        state = self._assistant_state()
        if state is None or state.qa_file is None:
            return

        def chosen(path: Path | None) -> None:
            if path is not None:
                self._apply_answer_list(state.qa_file, path)

        self.app.push_screen(
            PathPickerScreen(
                title="Choose the answers list (1: H2O; Water)", mode="file", start_path=state.folder
            ),
            chosen,
        )

    def _apply_answer_list(self, qa_file: Path, answers: Path) -> None:
        from opengrader.core.qa_file.answer_list import apply_answer_list

        try:
            result = apply_answer_list(
                qa_file.read_text(encoding="utf-8"), answers.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            self.app.notify(f"Nothing was changed: {exc}", severity="error")
            return
        if result.filled:
            qa_file.write_text(result.markdown, encoding="utf-8")
        message = f"Filled {len(result.filled)} answer(s) in {qa_file.name}."
        if result.problems:
            message += f" {len(result.problems)} line(s) skipped: " + "; ".join(result.problems[:3])
        self.app.notify(message, severity="warning" if result.problems else "information", timeout=12)
        self._refresh_assistant_card()

    def _open_grader_packet(self) -> None:
        submissions = self._handoff_submissions("handoff-submissions")
        if not submissions:
            self.app.notify(
                "Choose the submissions folder to share.", severity="error"
            )
            return
        self.app.push_screen(
            GraderPacketScreen(
                submissions_path=submissions,
                qa_file_path=self._session_paths()[0],
            )
        )

    def _open_merge(self) -> None:
        submissions = self._handoff_submissions("handoff-submissions")
        if not submissions:
            self.app.notify(
                "Choose the submissions folder to merge into.", severity="error"
            )
            return
        self.app.push_screen(MergeScreen(submissions_path=submissions))

    def _open_backup(self) -> None:
        submissions = self._handoff_submissions("backup-submissions")
        if not submissions:
            self.app.notify(
                "Choose the submissions folder to back up.", severity="error"
            )
            return
        self.app.push_screen(BackupScreen(submissions_path=submissions))

    def _open_region_map(self) -> None:
        pdf_path = self._clean_path(self.query_one("#region-pdf", Input).value)
        if not pdf_path:
            self.app.notify(
                "Choose the blank assignment PDF to draw regions on.",
                severity="error",
            )
            return
        existing = self._clean_path(self.query_one("#region-map-path", Input).value)

        def _apply(saved: Path | None) -> None:
            if saved is None:
                return
            self.query_one("#region-map-path", Input).value = str(saved)
            # Hand it to the pipeline card, and open the block it lives in.
            self.query_one("#pipe-region-map", Input).value = str(saved)
            self.query_one("#pipe-use-region-map", Checkbox).value = True
            self.query_one("#pipe-advanced", Collapsible).collapsed = False

        self.app.push_screen(
            RegionMapScreen(pdf_path, existing or None),
            _apply,
        )

    def _open_doctor(self) -> None:
        current = self._ai()
        self.app.push_screen(
            DoctorScreen(
                vision_model=current.vision_model,
                text_model=current.text_model,
                ollama_host=current.host,
            )
        )

    def _ai_summary_text(self) -> str:
        current = self._ai()
        handwriting = (
            f"reads handwriting with {current.vision_model}"
            if current.read_handwriting
            else "does not read handwriting"
        )
        grading = (
            f"grades written answers with {current.text_model} at "
            f"{current.min_confidence:g}+ confidence"
            if current.grade_written
            else "does not grade written answers"
        )
        return (
            f"[dim]Now: {current.provider.label} — {handwriting}; "
            f"{grading}.[/dim]"
        )

    def _ai_status_text(self, step: str) -> str:
        current = self._ai()
        change_here = "Change this in [b]AI setup[/b] under Helper Tools below."

        if step == "ext":
            if current.read_handwriting:
                scope = (
                    "only the answers OCR read badly"
                    if current.escalate_only
                    else "every answer"
                )
                body = f"AI: {scope} is transcribed with the handwriting model"
                body += (
                    "; handwritten maths is read as LaTeX."
                    if current.transcribe_math
                    else "."
                )
            else:
                body = (
                    "AI: off — answers are transcribed by text recognition "
                    "only, and left empty where a blank template gives no "
                    "text layer."
                )
        elif step == "ak":
            if current.read_handwriting:
                body = (
                    "AI: a scanned answer key is read with the handwriting "
                    "model."
                )
            else:
                body = (
                    "AI: off — a scanned answer key is read by text "
                    "recognition only."
                )
        elif step == "pipe":
            handwriting = (
                "reads hard-to-read handwriting"
                if current.read_handwriting
                else "does not read handwriting"
            )
            grading = (
                "suggests grades for written answers"
                if current.grade_written
                else "does not grade written answers"
            )
            auto_grade = (
                "auto-grade runs automatically once extraction finishes"
                if current.auto_grade
                else "auto-grade is turned off — turn it on in AI setup and "
                "run again to score what your rubric settles outright"
            )
            body = f"AI: {handwriting}, {grading}, and {auto_grade}."
        else:
            raise ValueError(f"unknown AI status step {step!r}")

        if current.kind == "cloud":
            body = f"{body} {app_config.consent.egress_statement(current)}"
        return f"{body} {change_here}"

    def _refresh_ai_displays(self) -> None:
        self.query_one("#ai-current-summary", Static).update(self._ai_summary_text())
        self.query_one("#refine-ai-status", Static).update(self._ai_status_text("ext"))
        self.query_one("#ak-ai-status", Static).update(self._ai_status_text("ak"))
        self.query_one("#pipe-ai-status", Static).update(self._ai_status_text("pipe"))
        # Avoid importing TuiApp here — it would import this module back in.
        refresh_banner = getattr(self.app, "refresh_ai_banner", None)
        if refresh_banner is not None:
            refresh_banner()

    def _open_ai_settings(self) -> None:
        def _refresh(saved: app_config.AISettings | None) -> None:
            if saved is None:
                return
            self._refresh_ai_displays()

        self.app.push_screen(AISettingsScreen(), _refresh)

    def _open_providers(self) -> None:
        def _refresh(changed: bool | None) -> None:
            if changed:
                self._refresh_ai_displays()

        self.app.push_screen(ProviderScreen(), _refresh)

    def _open_course_grades(self) -> None:
        prefill = ""
        submissions = self._session_paths()[1]
        if submissions:
            prefill = str(_find_course_file(Path(submissions)) or "")
        self.app.push_screen(CourseGradesScreen(course_path=prefill))

    def _open_llm_eval(self) -> None:
        # The folder being graded, else the one being prepared above.
        solutions, submissions = self._session_paths()
        if not solutions:
            solutions = self._clean_path(self.query_one("#pipe-qa", Input).value)
        if not submissions:
            class_folder = self._clean_path(self.query_one("#pipe-out", Input).value)
            if class_folder:
                submissions = str(PipelineRequest(Path(), Path(class_folder)).submissions_dir)

        def _apply(recommended: float | None) -> None:
            if recommended is None:
                return
            settings = replace(
                app_config.load_settings(),
                min_confidence=round(recommended, 2),
                min_confidence_measured=True,
                grade_written=True,
            )
            try:
                app_config.save_settings(settings)
            except OSError as exc:
                self.app.notify(
                    f"Could not save the measured confidence: {exc}",
                    severity="error",
                )
                return
            self._refresh_ai_displays()
            self.app.notify(
                f"Minimum confidence set to {recommended:.2f} — saved to AI "
                "setup and used by every tool."
            )

        self.app.push_screen(
            LlmEvalScreen(
                solutions_path=solutions,
                submissions_path=submissions,
                apply_label="Save as my AI setting",
            ),
            _apply,
        )

    def _run_photo_import(self) -> None:
        input_path = self._clean_path(self.query_one("#photo-input", Input).value)
        output_dir = self._clean_path(self.query_one("#photo-output", Input).value)
        if not input_path or not output_dir:
            self.app.notify(
                "Photos path and output directory are required.", severity="error"
            )
            return

        pages_raw = self.query_one("#photo-pages", Input).value.strip()
        pages_per_photo_set: int | None = None
        if pages_raw:
            try:
                pages_per_photo_set = int(pages_raw)
            except ValueError:
                self.app.notify(
                    "Photos per student must be a whole number.", severity="error"
                )
                return
            if pages_per_photo_set < 1:
                self.app.notify(
                    "Photos per student must be at least 1.", severity="error"
                )
                return

        paper = "a4" if self.query_one("#photo-a4", Checkbox).value else "letter"
        perspective = not self.query_one("#photo-no-perspective", Checkbox).value

        self.query_one("#photo-progress-status", Static).update("Importing photos…")
        self.query_one("#photo-progress").remove_class("hidden")
        self.query_one("#run-photo-import", Button).disabled = True
        self._photo_import_worker(
            input_path,
            output_dir,
            pages_per_photo_set=pages_per_photo_set,
            paper=paper,
            perspective=perspective,
        )

    @work(thread=True, exclusive=True, group="photo-import")
    def _photo_import_worker(
        self,
        input_path: str,
        output_dir: str,
        *,
        pages_per_photo_set: int | None,
        paper: str,
        perspective: bool,
    ) -> None:
        app = self.app

        def on_status(message: str) -> None:
            app.call_from_thread(self._photo_set_status, message)

        try:
            report = import_photos(
                input_path,
                output_dir,
                pages_per_photo_set=pages_per_photo_set,
                paper=paper,
                perspective=perspective,
                progress_callback=on_status,
            )
        except PhotoIntakeError as exc:
            app.call_from_thread(self._photo_finish, str(exc), True, None)
            return
        except Exception as exc:
            app.call_from_thread(self._photo_finish, f"Photo import error: {exc}", True, None)
            return
        app.call_from_thread(
            self._photo_finish, self._photo_summary(report), False, report
        )

    @staticmethod
    def _photo_summary(report: PhotoIntakeReport) -> str:
        count = len(report.submissions)
        noun = "submission" if count == 1 else "submissions"
        message = f"Imported {count} {noun} to {report.output_dir}."
        unreadable = sum(1 for outcome in report.outcomes if outcome.method == "unreadable")
        if unreadable:
            photo_noun = "photo" if unreadable == 1 else "photos"
            message += f" {unreadable} {photo_noun} could not be read and were skipped."
        return message

    def _photo_set_status(self, message: str) -> None:
        self.query_one("#photo-progress-status", Static).update(message.strip())

    def _photo_finish(
        self, message: str, is_error: bool, report: PhotoIntakeReport | None
    ) -> None:
        self.query_one("#run-photo-import", Button).disabled = False
        self.query_one("#photo-progress").add_class("hidden")
        self.app.notify(message, severity="error" if is_error else "information")
        if report is not None and report.submissions:
            scan_input = self.query_one("#pipe-scan", Input)
            if not scan_input.value.strip():
                scan_input.value = str(report.output_dir)

    def _section_rosters(self) -> tuple[str, ...]:
        """The `TSV[=SECTION]` lines of a mixed stack, or () when not in use."""
        if not self.query_one("#pipe-many-rosters", Checkbox).value:
            return ()
        raw = self.query_one("#pipe-roster-list", TextArea).text
        return tuple(line.strip() for line in raw.splitlines() if line.strip())

    def _section_scans(self) -> tuple[str, ...]:
        """The `PDF[=SECTION]` lines, one scan per section, or () when not in use."""
        if not self.query_one("#pipe-many-scans", Checkbox).value:
            return ()
        raw = self.query_one("#pipe-scan-list", TextArea).text
        return tuple(line.strip() for line in raw.splitlines() if line.strip())

    def _autofill_lines(
        self, list_id: str, checkbox_id: str, lines: tuple[str, ...]
    ) -> None:
        """`_autofill` for a one-per-line box: what the user typed survives a
        re-scan; what a scan filled is replaced, and its box ticked to match."""
        autofilled: dict[str, str] = getattr(self, "_autofilled", {})
        self._autofilled = autofilled
        area = self.query_one(f"#{list_id}", TextArea)
        current = area.text.strip()
        if current and current != autofilled.get(list_id, "").strip():
            return
        text = "".join(f"{line}\n" for line in lines)
        area.text = text
        autofilled[list_id] = text
        self.query_one(f"#{checkbox_id}", Checkbox).value = bool(lines)

    def _run_refine(self) -> None:
        request = self._build_pipeline_request()
        if request is None:
            return
        if not any(request.submissions_dir.rglob("*.submission.md")):
            self.app.notify(
                f"Nothing has been extracted in {request.output_dir} yet — run "
                "Prepare an assignment first.",
                severity="error",
            )
            return

        refine_dpi = self.query_one("#refine-dpi", Input).value.strip()
        if refine_dpi and (not refine_dpi.isdigit() or int(refine_dpi) < 1):
            self.app.notify(
                "Re-read DPI must be a whole number (or blank to reuse "
                "the first pass's resolution).",
                severity="error",
            )
            return

        # [1:] drops the command name; run_cli takes only its arguments.
        argv = list(refine_argv(request, dpi=int(refine_dpi) if refine_dpi else None)[1:])

        self._refine_total = 0
        bar = self.query_one("#refine-progress-bar", ProgressBar)
        bar.update(total=None, progress=0)
        self.query_one("#refine-progress-status", Static).update("Starting…")
        self.query_one("#refine-progress").remove_class("hidden")
        self.query_one("#run-refine", Button).disabled = True
        self.extraction_in_progress = True

        self._refine_worker(argv)

    @work(thread=True, exclusive=True, group="refine")
    def _refine_worker(self, argv: list[str]) -> None:
        app = self.app

        def on_file_start(_pdf_name: str, count: int) -> None:
            app.call_from_thread(self._refine_add_total, count)

        def on_response(pdf_name: str, question_id: str) -> None:
            app.call_from_thread(self._refine_advance, pdf_name, question_id)

        def on_status(message: str) -> None:
            app.call_from_thread(self._refine_set_status, message)

        progress = ExtractionProgress(on_file_start=on_file_start, on_response=on_response)
        try:
            run_extractor_cli(
                argv,
                progress=progress,
                status_callback=on_status,
                prompt_cloud_send=False,
            )
        except CloudConfirmationNeeded as needed:
            app.call_from_thread(
                self._confirm_tool_cloud_send,
                needed,
                "#refine-progress-status",
                lambda: self._refine_worker([*argv, "--yes-send-to-cloud"]),
                lambda message: self._refine_finish(message, False),
            )
            return
        except SystemExit as exc:
            app.call_from_thread(self._refine_finish, f"Re-read failed (exit {exc.code}).", True)
            return
        except Exception as exc:
            app.call_from_thread(self._refine_finish, f"Re-read error: {exc}", True)
            return
        app.call_from_thread(self._refine_finish, "Re-read complete.", False)

    def _refine_add_total(self, count: int) -> None:
        self._refine_total = getattr(self, "_refine_total", 0) + count
        self.query_one("#refine-progress-bar", ProgressBar).update(total=self._refine_total)

    def _refine_advance(self, pdf_name: str, question_id: str) -> None:
        self.query_one("#refine-progress-bar", ProgressBar).advance(1)
        self.query_one("#refine-progress-status", Static).update(
            f"Re-reading {pdf_name} · {question_id}"
        )

    def _refine_set_status(self, message: str) -> None:
        self.query_one("#refine-progress-status", Static).update(message.strip())

    def _refine_finish(self, message: str, is_error: bool) -> None:
        self.query_one("#run-refine", Button).disabled = False
        self.query_one("#refine-progress").add_class("hidden")
        self.extraction_in_progress = False
        self.app.notify(message, severity="error" if is_error else "information")

    def _scan_class_folder(self) -> ClassFolderScan | None:
        folder = self._clean_path(self.query_one("#pipe-out", Input).value)
        if not folder or not Path(folder).is_dir():
            self.app.notify(
                "Choose an existing class folder to scan.", severity="error"
            )
            return None
        try:
            found = scan_class_folder(folder)
        except OSError as exc:
            self.app.notify(f"Could not read {folder}: {exc}", severity="error")
            return None

        self._autofill("pipe-scan", found.scan)
        self._autofill("pipe-qa", found.qa_file)
        self._autofill("pipe-roster", found.roster, checkbox_id="pipe-use-roster")
        self._autofill(
            "pipe-template", found.blank_template, checkbox_id="pipe-use-template"
        )
        self._autofill_lines(
            "pipe-scan-list",
            "pipe-many-scans",
            tuple(f"{item.path}={item.section}" for item in found.section_scans),
        )
        if found.section_scans:
            self._autofill_lines(
                "pipe-roster-list",
                "pipe-many-rosters",
                tuple(f"{item.path}={item.section}" for item in found.section_rosters),
            )

        summary = self.query_one("#pipe-folder-summary", Static)
        summary.update("\n".join(describe_class_folder(found)))
        summary.remove_class("hidden")
        has_key = bool(self.query_one("#pipe-qa", Input).value.strip())
        self.query_one("#pipe-qa-hint").set_class(has_key, "hidden")

        # The scan and answer-key fields are tucked away; show them only when
        # the folder could not answer for itself.
        has_scan = bool(self.query_one("#pipe-scan", Input).value.strip()) or bool(
            self._section_scans()
        )
        if not has_scan or found.ambiguity(ROLE_SCAN) or found.ambiguity(ROLE_QA):
            self.query_one("#pipe-advanced", Collapsible).collapsed = False
        if found.ambiguity(ROLE_ROSTER) or found.ambiguity(ROLE_TEMPLATE):
            self.query_one("#pipe-settings", Collapsible).collapsed = False
        return found

    def _load_class_folder(self, *, only_if_changed: bool = False) -> None:
        """Scan the chosen class folder and bring back what was saved for it.

        Saved settings are applied once per folder, when it is first chosen, so
        they never overwrite what the user has changed since.
        """
        folder = self._clean_path(self.query_one("#pipe-out", Input).value)
        loaded = getattr(self, "_pipe_folder_loaded", None)
        if only_if_changed and (folder == loaded or not Path(folder).is_dir()):
            return
        if self._scan_class_folder() is None:
            return
        if folder == loaded:
            return
        self._pipe_folder_loaded = folder
        saved = load_folder_settings(folder)
        if saved is not None:
            self._apply_folder_settings(saved)

    def _apply_folder_settings(self, saved: FolderPipelineSettings) -> None:
        def fill(input_id: str, value: str) -> None:
            if value:
                self.query_one(f"#{input_id}", Input).value = value

        def tick(checkbox_id: str, value: bool) -> None:
            self.query_one(f"#{checkbox_id}", Checkbox).value = value

        # A saved scan or answer key is one the user chose by hand: it is not
        # recorded as auto-filled, so a later re-scan leaves it alone.
        fill("pipe-scan", saved.scan)
        fill("pipe-qa", saved.qa_file)
        if saved.qa_file:
            self.query_one("#pipe-qa-hint").add_class("hidden")
        self.query_one("#pipe-pages", Input).value = (
            str(saved.pages_per_submission) if saved.pages_per_submission else ""
        )
        fill("pipe-roster", saved.roster)
        tick("pipe-use-roster", saved.use_roster)
        fill("pipe-template", saved.blank_template)
        tick("pipe-use-template", saved.use_template)
        fill("pipe-region-map", saved.region_map)
        tick("pipe-use-region-map", saved.use_region_map)
        self.query_one("#pipe-roster-list", TextArea).text = "".join(
            f"{entry}\n" for entry in saved.section_rosters
        )
        tick("pipe-many-rosters", saved.use_section_rosters)
        # Saved lines are ones typed by hand; none saved keeps what the folder
        # scan found (a section added since is picked up next time).
        if saved.section_scans or not saved.use_section_scans:
            self.query_one("#pipe-scan-list", TextArea).text = "".join(
                f"{entry}\n" for entry in saved.section_scans
            )
        tick("pipe-many-scans", saved.use_section_scans)
        tick("pipe-reorder", saved.reorder_pages)
        tick("pipe-draft-rubrics", saved.draft_rubrics)

    def _folder_settings(self) -> FolderPipelineSettings:
        """The card as it stands, in the shape that is saved with the folder."""

        def path(input_id: str) -> str:
            return self._clean_path(self.query_one(f"#{input_id}", Input).value)

        def ticked(checkbox_id: str) -> bool:
            return self.query_one(f"#{checkbox_id}", Checkbox).value

        def override(input_id: str) -> str:
            # What the folder scan filled in is found again next time; only a
            # path the user chose instead is worth remembering.
            value = self.query_one(f"#{input_id}", Input).value.strip()
            autofilled = getattr(self, "_autofilled", {}).get(input_id)
            return "" if value == autofilled else path(input_id)

        pages = self.query_one("#pipe-pages", Input).value.strip()
        raw_rosters = self.query_one("#pipe-roster-list", TextArea).text
        raw_scans = self.query_one("#pipe-scan-list", TextArea).text
        if raw_scans.strip() == getattr(self, "_autofilled", {}).get(
            "pipe-scan-list", ""
        ).strip():
            # Found by the folder scan: found again next time.
            raw_scans = ""
        return FolderPipelineSettings(
            scan=override("pipe-scan"),
            qa_file=override("pipe-qa"),
            pages_per_submission=int(pages) if pages.isdigit() and int(pages) >= 1 else None,
            use_roster=ticked("pipe-use-roster"),
            roster=path("pipe-roster"),
            use_template=ticked("pipe-use-template"),
            blank_template=path("pipe-template"),
            use_region_map=ticked("pipe-use-region-map"),
            region_map=path("pipe-region-map"),
            use_section_rosters=ticked("pipe-many-rosters"),
            section_rosters=tuple(
                line.strip() for line in raw_rosters.splitlines() if line.strip()
            ),
            use_section_scans=ticked("pipe-many-scans"),
            section_scans=tuple(
                line.strip() for line in raw_scans.splitlines() if line.strip()
            ),
            reorder_pages=ticked("pipe-reorder"),
            draft_rubrics=ticked("pipe-draft-rubrics"),
        )

    def _restore_machine_settings(self) -> None:
        saved = load_machine_settings()
        self.query_one("#pipe-grade-asap", Checkbox).value = saved.grade_as_ready
        if saved.jobs:
            self.query_one("#pipe-jobs", Input).value = str(saved.jobs)
            self.query_one("#pipe-parallel", Checkbox).value = True
        if saved.answer_key_points:
            self.query_one("#ak-default-points", Input).value = format_points(
                saved.answer_key_points
            )
        last = saved.last_class_folder
        if last and Path(last).is_dir():
            self.query_one("#pipe-out", Input).value = last
            self._load_class_folder()

    def _save_pipeline_settings(self, request: PipelineRequest) -> None:
        """Remember this run's settings: the assignment's with its folder, the
        rest for this computer. "Grade it anyway" and "Start over" are one-shot
        and never saved."""
        try:
            save_folder_settings(request.output_dir, self._folder_settings())
            save_machine_settings(
                replace(
                    load_machine_settings(),
                    grade_as_ready=self.query_one("#pipe-grade-asap", Checkbox).value,
                    jobs=request.jobs,
                    last_class_folder=str(request.output_dir),
                )
            )
        except OSError as exc:
            # Remembering is a convenience; the run itself goes ahead.
            self.app.notify(
                f"Could not save these settings for next time: {exc}",
                severity="warning",
            )

    def show_session(
        self, qa_file: str | Path | None, submissions: str | Path | None
    ) -> None:
        """Say what is open for grading, and fill the card's fields with it.

        Filling the fields keeps the helper tools that fall back to "the folder
        being graded" (`_session_paths`) pointed at the right place, and makes
        Launch Grading re-open it after a look around Tools.
        """
        # Whatever was just opened is now the newest recent folder.
        self.call_later(self.refresh_recents)
        banner = self.query_one("#setup-session", Static)
        if submissions is None:
            banner.add_class("hidden")
            return
        banner.update(f"Grading {submissions}. F3 goes back to it.")
        banner.remove_class("hidden")
        # Recorded as filled in, not typed: choosing another class folder
        # afterwards replaces them instead of opening this one again.
        autofilled: dict[str, str] = getattr(self, "_autofilled", {})
        self._autofilled = autofilled
        if qa_file is not None:
            self.query_one("#tui-qa-path", Input).value = str(qa_file)
            autofilled["tui-qa-path"] = str(qa_file)
        self.query_one("#tui-sub-path", Input).value = str(submissions)
        autofilled["tui-sub-path"] = str(submissions)
        self._refresh_answers_button()

    async def refresh_recents(self) -> None:
        """Re-read the last `RECENT_FOLDERS_SHOWN` class folders from recents."""
        folders = [item for item in load_recents() if item.kind == "folder"]
        shown = folders[:RECENT_FOLDERS_SHOWN]
        await self.query_one("#launch-recent-list", RecentList).set_items(shown)
        self.query_one("#launch-recents").set_class(not shown, "hidden")

    def focus_launch_card(self, *, scroll: bool = True) -> None:
        """Land on the recent folders if there are any, else the class-folder box.

        Queued behind `refresh_recents` (both go through `call_later`), so the
        choice is made on the list as it is about to be shown. `scroll=False`
        on first launch, where the page is already at its top (the header).
        """
        self.call_later(self._focus_launch_card, scroll)

    def _focus_launch_card(self, scroll: bool) -> None:
        recents = self.query_one("#launch-recent-list", RecentList)
        target = recents if recents.items else self.query_one("#tui-class-folder", Input)
        target.focus(scroll_visible=scroll)
        if scroll:
            self.call_after_refresh(
                self.query_one("#launch-section").scroll_visible, top=True
            )

    def on_recent_list_chosen(self, event: RecentList.Chosen) -> None:
        """Open a recent folder, or only choose it when there are two ways to.

        With *Open in browser* on the card, a recent entry fills the card
        (folder, answer key, submissions) and waits for Launch Grading or
        Open in browser. Without it, Launch Grading is the only way, so the
        entry opens straight away with the pair recorded last time.
        """
        event.stop()
        item = event.item
        if not item.qa_file or not item.submissions:
            self.app.notify(
                "That entry has no answer key on record; choose the folder above.",
                severity="error",
            )
            return
        if self.query_one("#browser-open-button").has_class("hidden"):
            self.post_message(self.LaunchRequested(item.qa_file, item.submissions))
            return
        self._choose_recent(item)

    def _choose_recent(self, item) -> None:  # noqa: ANN001 - core.recents.RecentItem
        """Fill the card with a recent entry, exactly as recorded, and wait."""
        autofilled: dict[str, str] = getattr(self, "_autofilled", {})
        self._autofilled = autofilled
        self.query_one("#tui-class-folder", Input).value = item.path
        for input_id, value in (
            ("tui-qa-path", item.qa_file),
            ("tui-sub-path", item.submissions),
        ):
            self.query_one(f"#{input_id}", Input).value = value
            # Filled in, not typed: choosing another folder replaces them.
            autofilled[input_id] = value
        # The pair is the one recorded, so Launch Grading must not re-scan the
        # folder and perhaps pick a different answer key.
        self._launch_folder_scanned = item.path
        self._launch_assignments = None
        self.query_one("#workspace-button").add_class("hidden")
        self._refresh_answers_button()
        title = item.title or Path(item.path).name
        summary = self.query_one("#tui-folder-summary", Static)
        summary.update(
            f"{title} — answer key {Path(item.qa_file).name}, submissions "
            f"{Path(item.submissions).name}/.\nPress Launch Grading to grade "
            "here, or Open in browser."
        )
        summary.remove_class("hidden")
        self.query_one("#launch-button", Button).focus()

    def on_recent_list_forgotten(self, event: RecentList.Forgotten) -> None:
        event.stop()
        folders = [item for item in load_recents() if item.kind == "folder"]
        if len(folders) > event.remaining:
            # One that did not fit moves up into the list.
            self.call_later(self.refresh_recents)
        elif event.remaining == 0:
            self.query_one("#launch-recents").add_class("hidden")
            self.query_one("#tui-class-folder", Input).focus()

    def _session_paths(self) -> tuple[str, str]:
        """The answer key and submissions the Start grading card points at."""
        return (
            self._clean_path(self.query_one("#tui-qa-path", Input).value),
            self._clean_path(self.query_one("#tui-sub-path", Input).value),
        )

    def _scan_launch_folder(self) -> ClassFolderScan | None:
        folder = self._clean_path(self.query_one("#tui-class-folder", Input).value)
        if not folder or not Path(folder).is_dir():
            self.app.notify(
                "Choose an existing class folder to open.", severity="error"
            )
            return None
        try:
            found = scan_class_folder(folder)
        except OSError as exc:
            self.app.notify(f"Could not read {folder}: {exc}", severity="error")
            return None

        self._autofill("tui-qa-path", found.qa_file)
        self._autofill("tui-sub-path", found.submissions)
        self._launch_folder_scanned = folder
        self._refresh_answers_button()

        lines = describe_class_folder(found, GRADING_ROLES, include_notes=False)
        assignments: AssignmentFolders | None = None
        own_workspace = is_workspace(found.folder)
        if own_workspace:
            lines = self._workspace_lines(found)
        elif (
            found.qa_file is None
            and found.ambiguity(ROLE_QA) is None
            and found.submissions is None
        ):
            # No answer key or submissions of its own: perhaps a folder of
            # assignments. (Its own scan finds their submissions and scans as
            # candidates, so what it says here is not the whole story.)
            assignments = find_assignment_folders(found.folder)
            if not assignments.assignments and not assignments.workspaces:
                assignments = None
            else:
                lines = self._assignment_folders_lines(assignments)
        self._launch_assignments = assignments
        self._set_workspace_action(found.folder, own_workspace, assignments)

        summary = self.query_one("#tui-folder-summary", Static)
        summary.update("\n".join(lines))
        summary.remove_class("hidden")
        if (
            (found.qa_file is None or found.submissions is None)
            and assignments is None
            and not own_workspace
        ):
            self.query_one("#launch-files", Collapsible).collapsed = False
        return found

    def _set_workspace_action(
        self,
        folder: Path,
        own_workspace: bool,
        assignments: AssignmentFolders | None,
    ) -> None:
        """One button, worded for the folder: *Open workspace* when it is one,
        *Open "Midterm" workspace* when one subfolder is, *Make this a
        workspace…* for a folder of assignments, and hidden otherwise.
        Nothing opens without a press: classic never redirects by itself."""
        action: tuple[str, Path] | None = None
        label = ""
        if own_workspace:
            action, label = ("open", folder), "Open workspace"
        elif assignments is not None and len(assignments.workspaces) == 1:
            inner = assignments.workspaces[0]
            name = inner.name if len(inner.name) <= 24 else inner.name[:23] + "…"
            action, label = ("open", inner), f"Open “{name}” workspace"
        elif assignments is not None and assignments.is_workspace_candidate:
            action, label = ("make", folder), "Make this a workspace…"
        self._workspace_action = action
        button = self.query_one("#workspace-button", Button)
        if label:
            button.label = label
        button.set_class(action is None, "hidden")

    @staticmethod
    def _workspace_lines(found: ClassFolderScan) -> list[str]:
        """What the card says about a folder that is a workspace."""
        lines = ["This folder is a workspace."]
        try:
            from opengrader.core.workspace.store import Workspace

            titles = [item.title for item in Workspace(found.folder).assignments()]
        except Exception:  # noqa: BLE001 - an unreadable database still opens
            titles = []
        if titles:
            shown = ", ".join(titles[:5]) + (
                f" and {len(titles) - 5} more" if len(titles) > 5 else ""
            )
            lines[0] = f"This folder is a workspace with {len(titles)} assignment(s): {shown}."
        lines.append(
            "Open workspace opens its assignment list, shared with the browser app."
        )
        if found.qa_file is not None and found.submissions is not None:
            lines.append(
                "Launch Grading grades the files here in place instead; those "
                "grades reach the workspace only when it imports the folder again."
            )
        return lines

    def _on_workspace_button(self) -> None:
        action = getattr(self, "_workspace_action", None)
        if action is None:
            return
        kind, path = action
        if kind == "open":
            self._open_workspace(path)
        else:
            self._offer_workspace()

    def _open_workspace(self, root: Path) -> None:
        """Close classic and open an existing workspace (`main._after_classic`)."""
        from opengrader.tui.make_workspace import OpenWorkspaceRequest

        if getattr(self, "extraction_in_progress", False):
            self.app.notify(
                "A folder is being prepared. Open the workspace when it is done.",
                severity="warning",
            )
            return
        self.app.exit(OpenWorkspaceRequest(root=root))

    @staticmethod
    def _assignment_folders_lines(
        found: AssignmentFolders, *, advice: bool = True
    ) -> list[str]:
        """What a folder of assignments holds, for the Start grading card."""

        def name(path: Path) -> str:
            return f"{path.relative_to(found.folder).as_posix()}/"

        lines = (
            [
                f"This folder holds {len(found.assignments)} assignment(s), each "
                "with its own answer key:"
            ]
            if found.assignments
            else []
        )
        for item in found.assignments:
            if item.submissions is not None:
                count = sum(1 for _ in item.submissions.rglob("*.submission.md"))
                state = f"ready: {count} submission(s)"
            else:
                state = "scans not prepared yet"
            lines.append(f"  {'✓' if item.submissions else '·'} {name(item.folder):<22} {state}")
        for group in found.same_key:
            lines.append(
                "! "
                + " and ".join(name(path) for path in group)
                + " have the same answer key: sections of one assignment. Put "
                "their scans in one folder, one scan per section, and prepare "
                "it once (Prepare an assignment → Advanced settings)."
            )
        for path in found.workspaces:
            opens = (
                f"Open “{path.name}” workspace opens it"
                if len(found.workspaces) == 1
                else "choose it above to open it"
            )
            lines.append(
                f"! {name(path)} is a workspace of its own: {opens}. A "
                "workspace cannot hold another, so this folder cannot become one."
            )
        if advice and found.is_workspace_candidate:
            lines.append(
                "A workspace keeps them together: one list of assignments to "
                "grade from, shared with the browser app. Make this a "
                "workspace… sets it up."
            )
        return lines

    def _offer_workspace(self) -> None:
        """Make this folder of assignments a workspace, after asking.

        Classic never opens a workspace itself: on a yes the app closes with
        a `MakeWorkspaceRequest`, and `classic_command` imports the ready
        assignments and opens the workspace's assignment list.
        """
        from opengrader.tui.make_workspace import MakeWorkspaceRequest

        found: AssignmentFolders | None = getattr(self, "_launch_assignments", None)
        if found is None or not found.is_workspace_candidate:
            self.app.notify(
                "Choose a folder whose subfolders each hold an assignment.",
                severity="error",
            )
            return
        if getattr(self, "extraction_in_progress", False):
            self.app.notify(
                "A folder is being prepared. Make the workspace when it is done.",
                severity="warning",
            )
            return
        ready = found.ready
        if not ready:
            self.app.notify(
                "None of these assignments is prepared yet. Choose each "
                "subfolder here and prepare it first; then make the workspace.",
                severity="error",
                timeout=10,
            )
            return
        waiting = [item for item in found.assignments if item not in ready]
        note = (
            "Each ready assignment is imported with the grades already in its "
            "files, and the workspace's assignment list opens. From then on "
            "grade them there (here, in the browser app, or with opengrader "
            "tui), not in place: the files stay as they are, and W in the "
            "assignment list writes the workspace's grades back to them."
        )
        if waiting:
            names = ", ".join(f"{item.folder.name}/" for item in waiting)
            note += (
                f" Not prepared yet, so left out: {names}. Add each later with "
                "`opengrader run <its folder>`, which publishes into this workspace."
            )

        def answered(action: str | None) -> None:
            if action == ACTION_WORKSPACE:
                self.app.exit(
                    MakeWorkspaceRequest(
                        root=found.folder,
                        assignments=tuple(
                            (item.qa_file, item.submissions)  # type: ignore[misc]
                            for item in ready
                        ),
                    )
                )

        self.app.push_screen(
            PrepareFolderScreen(
                "Make this folder a workspace?",
                "\n".join(
                    [f"Folder: {found.folder}"]
                    + self._assignment_folders_lines(found, advice=False)
                ),
                note,
                [(ACTION_WORKSPACE, "Make workspace")],
            ),
            answered,
        )

    def _launch_grading(self, *, browser: bool = False) -> None:
        """Open the card's folder for grading, or offer to get it ready first.

        `browser` is Open in browser: the same check and the same offers, and
        the browser app is what opens at the end instead of the grade screen.
        """
        self._launch_to_browser = browser
        folder = self._clean_path(self.query_one("#tui-class-folder", Input).value)
        qa_path, sub_path = self._session_paths()
        is_folder = bool(folder) and Path(folder).is_dir()
        found: ClassFolderScan | None = None
        # A folder typed over the last one, with no Enter or Browse to scan
        # it, must not open the last one's files.
        if is_folder and (
            not qa_path
            or not sub_path
            or folder != getattr(self, "_launch_folder_scanned", None)
        ):
            found = self._scan_launch_folder()
            qa_path, sub_path = self._session_paths()

        if qa_path and sub_path:
            if not self._offer_auto_grade(qa_path, sub_path):
                self._open_for_grading(qa_path, sub_path)
        elif found is not None:
            self._offer_to_prepare(found)
        elif not is_folder:
            self.query_one("#launch-files", Collapsible).collapsed = False
            self.app.notify(
                "Choose a class folder to open, or an answer key and a "
                "submissions folder.",
                severity="error",
            )

    def _open_for_grading(self, qa_path: str, sub_path: str) -> None:
        """Open a ready folder where the press that started this asked for it:
        the grade screen (Launch Grading) or the browser app (Open in browser)."""
        if getattr(self, "_launch_to_browser", False):
            self.query_one(BrowserLauncher).open_folder(qa_path, sub_path)
        else:
            self.post_message(self.LaunchRequested(qa_path, sub_path))

    def _offer_to_prepare(self, found: ClassFolderScan) -> None:
        """The folder has nothing to grade yet: say what it would take.

        Nothing is prepared without a yes. A folder that cannot answer for
        itself (no scan, or two candidates) is handed to the pipeline card,
        where the choice is made.
        """
        folder = found.folder
        if getattr(self, "extraction_in_progress", False):
            self.app.notify(
                "A folder is already being prepared. Grading opens when its "
                "submissions are ready.",
                severity="warning",
            )
            return

        if is_workspace(folder):
            # Launch Grading on a workspace is a press to open it.
            self._open_workspace(folder)
            return
        assignments: AssignmentFolders | None = getattr(self, "_launch_assignments", None)
        if assignments is not None and assignments.folder == folder:
            if assignments.is_workspace_candidate:
                self._offer_workspace()
                return
            if assignments.assignments or assignments.workspaces:
                self.app.notify(
                    "This folder holds assignments in its subfolders. Choose one "
                    "of them to grade it (see the list on the card).",
                    severity="warning",
                    timeout=10,
                )
                return

        two_keys = found.ambiguity(ROLE_QA) is not None
        if found.ambiguity(ROLE_SUBMISSIONS) is not None or (
            found.submissions is not None and two_keys
        ):
            which = "answer key" if two_keys else "set of submissions"
            self.app.notify(
                f"This folder has more than one {which}. Choose the one to "
                'grade under "Answer key and submissions".',
                severity="error",
            )
            return

        if found.submissions is not None:
            # Read already; only the answer key is missing.
            count = sum(1 for _ in found.submissions.rglob("*.submission.md"))
            self._ask_about_folder(
                found,
                "This folder needs an answer key",
                f"Folder: {folder}\n"
                f"✓ {count} submission(s) are read and ready\n"
                "· answer key      none found",
                "Grading needs an answer key: it holds the questions, the "
                "points and the rubric. Create answer key writes one from "
                "your assignment or your solutions (a text file, a Word "
                "document or a PDF). Opengrader picks up from here when it "
                "is made.",
                [(ACTION_CREATE_KEY, "Create answer key…")],
            )
            return

        self._show_folder_in_pipeline_card(folder)
        scan = self._clean_path(self.query_one("#pipe-scan", Input).value) or (
            "sections" if self._section_scans() else ""
        )
        has_key = bool(self.query_one("#pipe-qa", Input).value.strip())
        if not scan or (two_keys and not has_key):
            self._show_pipeline_card()
            if two_keys and scan:
                reason = (
                    "This folder has more than one answer key. Choose one "
                    "under Advanced settings, then press Run Pipeline."
                )
            elif found.ambiguity(ROLE_SCAN) is not None:
                reason = (
                    "More than one PDF in this folder could be the scan. "
                    "Choose the scanned bundle under Advanced settings, then "
                    "press Run Pipeline."
                )
            else:
                reason = (
                    "There is nothing to grade in this folder yet: no "
                    "submissions and no scanned PDF. Put the scan in the "
                    "folder, or choose it under Advanced settings."
                )
            self.app.notify(reason, severity="error", timeout=10)
            return

        if getattr(self, "_launch_to_browser", False):
            wait = "The browser opens when it finishes."
        elif self.query_one("#pipe-grade-asap", Checkbox).value:
            wait = "Grading opens as soon as the first submission is ready."
        else:
            wait = "Grading opens when it finishes."

        work = (
            "the scan is checked, split into one PDF per student, and every "
            "answer is read. That builds student-pdfs/ and submissions/ inside the "
            "folder and can take a few minutes."
        )
        if has_key:
            self._ask_about_folder(
                found,
                "Prepare this folder for grading?",
                self._prepare_summary(folder),
                f"This folder has scans but nothing to grade yet. To get it "
                f"ready, {work} {wait}",
                [
                    (ACTION_SETTINGS, "Change settings…"),
                    (ACTION_PREPARE, "Prepare and grade"),
                ],
            )
            return

        needs_key = (
            "Grading needs an answer key: it holds the questions, the points "
            "and the rubric."
        )
        draft = self._draft_key(folder)
        if draft is not None:
            # A blank template prints the questions and their points: the key
            # can be drafted from it, with only the answers left to add.
            key, output = draft
            points = (
                f" ({key.defaulted} with no points printed get the standard ones)"
                if key.defaulted
                else ""
            )
            self._ask_about_folder(
                found,
                "No answer key yet — one can be drafted",
                self._prepare_summary(folder),
                f"{needs_key} The blank template prints {key.questions} "
                f"question(s) and their points{points}, so Opengrader can "
                f"draft the key from it as {output.name}: every question, "
                "its points and the usual rubric, with the answers left "
                "blank for you.\n\n"
                f"Then {work} {wait} You can grade by hand straight away; "
                f"add the answers to {output.name} when you want "
                "auto-grading.",
                [
                    (ACTION_SETTINGS, "Change settings…"),
                    (ACTION_CREATE_KEY, "Create it myself…"),
                    (ACTION_PREPARE, "Draft the key and read the scans"),
                ],
                draft=draft,
            )
        elif self._card_says_where_answers_are():
            self._ask_about_folder(
                found,
                "This folder needs an answer key",
                self._prepare_summary(folder),
                f"{needs_key} Create answer key writes one from your "
                "assignment or your solutions.\n\n"
                "Reading the scans is the slow part and does not need it. "
                f"Start that now and {work} Create answer key opens too, so "
                "you can make the key while it runs.",
                [
                    (ACTION_SETTINGS, "Change settings…"),
                    (ACTION_CREATE_KEY, "Create answer key…"),
                    (ACTION_PREPARE, "Read the scans now"),
                ],
            )
        else:
            self._ask_about_folder(
                found,
                "This folder needs an answer key",
                self._prepare_summary(folder),
                f"{needs_key} Create answer key writes one from your "
                "assignment or your solutions.\n\n"
                "With no blank template, the key is also what finds the "
                "questions on the pages (unless they are Opengrader's own "
                "printed assignment). So start now: the scan is checked and split "
                "into one PDF per student while Create answer key opens for "
                "you. Reading the answers starts by itself as soon as the "
                "key is made.",
                [
                    (ACTION_SETTINGS, "Change settings…"),
                    (ACTION_CREATE_KEY, "Create answer key…"),
                    (ACTION_PREPARE, "Check and split the scans now"),
                ],
            )

    def _card_says_where_answers_are(self) -> bool:
        """A blank template or a region map is set on the pipeline card."""
        return any(
            self.query_one(f"#pipe-use-{name}", Checkbox).value
            and self.query_one(f"#pipe-{name}", Input).value.strip()
            for name in ("template", "region-map")
        )

    def _draft_key(self, folder: Path):  # noqa: ANN202 - (DraftKey, Path) | None
        """The answer key the card's blank template can be read into, and the
        file it would be written to; None with no template, or one that
        prints no questions a reader can find (a scanned template)."""
        from opengrader.core.qa_file.draft_key import draft_from_blank_template

        if not self.query_one("#pipe-use-template", Checkbox).value:
            return None
        template = self._clean_path(self.query_one("#pipe-template", Input).value)
        output = next(
            (
                folder / name
                for name in _NEW_ANSWER_KEY_NAMES
                if not (folder / name).exists()
            ),
            None,
        )
        if not template or output is None or not Path(template).is_file():
            return None
        points = load_machine_settings().answer_key_points or DEFAULT_QUESTION_POINTS
        key = draft_from_blank_template(template, default_points=points)
        return None if key is None else (key, output)

    def _write_draft_key(self, draft) -> bool:  # noqa: ANN001
        """Write the drafted answer key and point the pipeline card at it."""
        key, output = draft
        try:
            # Never over a file that appeared since the dialog opened.
            with output.open("x", encoding="utf-8") as handle:
                handle.write(key.markdown)
        except OSError as exc:
            self.app.notify(
                f"Could not write the draft answer key ({exc}).", severity="error"
            )
            return False
        self.query_one("#pipe-qa", Input).value = str(output)
        self.query_one("#pipe-qa-hint").add_class("hidden")
        launch_folder = self._clean_path(self.query_one("#tui-class-folder", Input).value)
        if launch_folder == str(output.parent):
            # Start grading is on this folder: it has an answer key now.
            self._autofill("tui-qa-path", output)
            self._refresh_answers_button()
        self.app.notify(
            f"Drafted {output.name} from the blank template: {key.questions} "
            "question(s) with their points and rubric. The answers are blank "
            "until you add them.",
            timeout=10,
        )
        return True

    def _draft_and_run(self, draft, *, from_launch: bool) -> bool:  # noqa: ANN001
        """Write the drafted key, start the run with it, and put the answers
        form in front of the user while the scans are read. False when the
        draft could not be written (nothing was started)."""
        if not self._write_draft_key(draft):
            return False
        self._run_pipeline(from_launch=from_launch, offer_draft=False)
        self._open_answer_form(
            draft[1],
            note=(
                "The scans are being read meanwhile. Answers saved before "
                "auto-grading starts are used by it; you can also do this "
                "later with Add answers… on Start grading."
            ),
        )
        return True

    def _add_answers(self) -> None:
        """Start grading's Add answers…: the card's answer key as a form."""
        qa_path = self._session_paths()[0]
        if not qa_path or not Path(qa_path).is_file():
            # The button is only shown for a key that is there; it went since.
            self._refresh_answers_button()
            return
        self._open_answer_form(Path(qa_path))

    def _refresh_answers_button(self) -> None:
        """Show Add answers… only while there is something to add: the
        card's answer key exists and has a question with no answer."""
        from opengrader.core.qa_file.answers import unanswered_count

        button = self.query_one("#answers-button", Button)
        missing = 0
        qa_path = self._session_paths()[0]
        if qa_path and Path(qa_path).is_file():
            try:
                missing = unanswered_count(Path(qa_path).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                missing = 0
        button.label = f"Add answers · {missing} left"
        button.set_class(missing == 0, "hidden")

    def _open_answer_form(self, qa_file: Path, *, note: str = "") -> None:
        def closed(saved: bool | None) -> None:
            self._refresh_answers_button()
            if not saved:
                return
            if getattr(self, "extraction_in_progress", False):
                message = (
                    "Answers saved. Nothing else to do: this run auto-grades "
                    "with them when it gets there."
                )
            else:
                message = (
                    "Answers saved. Work that is already read has not been "
                    "graded with them yet. Press Run Pipeline (under Prepare "
                    "an assignment) and Opengrader grades it again with the "
                    "new answers. The scans are not read again; a question "
                    "the answers settle outright is scored afresh, and the "
                    "rest of your grades are kept."
                )
            self.app.notify(message, timeout=15)

        self.app.push_screen(AnswerFillScreen(qa_file, note=note), closed)

    def _offer_draft_then_run(self, request: PipelineRequest, draft) -> None:  # noqa: ANN001
        """Run Pipeline with a blank template and no answer key: the same
        offer Launch Grading makes, so the two do not behave differently."""
        key, output = draft

        def answered(action: str | None) -> None:
            if action == ACTION_PREPARE:
                self._draft_and_run(draft, from_launch=False)
            elif action == ACTION_SKIP:
                self._run_pipeline(offer_draft=False)

        self.app.push_screen(
            PrepareFolderScreen(
                "No answer key yet — one can be drafted",
                self._prepare_summary(request.output_dir),
                "Without an answer key the scans are read but grading cannot "
                f"open. The blank template prints {key.questions} question(s) "
                "and their points, so Opengrader can draft the key from it as "
                f"{output.name}: every question, its points and the usual "
                "rubric, with the answers left blank. A form to add the "
                "answers opens while the scans are read.",
                [
                    (ACTION_SKIP, "Run without a key"),
                    (ACTION_PREPARE, "Draft the key and run"),
                ],
            ),
            answered,
        )

    def _offer_auto_grade(self, qa_path: str, sub_path: str) -> bool:
        """Offer the auto-grading a folder prepared without its answer key
        never had. True when the question was asked (its answer opens the
        folder); False to open the folder as it is."""
        submissions = Path(sub_path)
        folder = submissions.parent
        declined: set[str] = getattr(self, "_auto_grade_declined", set())
        self._auto_grade_declined = declined
        if (
            submissions.name != "submissions"
            or getattr(self, "extraction_in_progress", False)
            or str(folder) in declined
            or not self._ai().auto_grade
            or not auto_grade_pending(folder)
        ):
            return False
        self._show_folder_in_pipeline_card(folder)
        scan = self._clean_path(self.query_one("#pipe-scan", Input).value)
        card_key = self._clean_path(self.query_one("#pipe-qa", Input).value)
        if not scan or card_key != qa_path:
            # Nothing to run again from, or not the answer key being opened.
            return False

        count = sum(1 for _ in submissions.rglob("*.submission.md"))

        def answered(action: str | None) -> None:
            if action == ACTION_AUTO_GRADE:
                # Opens when the grades are in, not while they are written.
                self._run_pipeline(from_launch=True, grade_as_ready=False)
            elif action == ACTION_OPEN:
                declined.add(str(folder))
                self._open_for_grading(qa_path, sub_path)

        self.app.push_screen(
            PrepareFolderScreen(
                "Auto-grade before opening?",
                f"Folder: {folder}\n"
                f"✓ {count} submission(s) are read\n"
                f"✓ answer key      {Path(qa_path).name}\n"
                "· auto-grading    not done yet",
                "These submissions were read before they could be "
                "auto-graded. Auto-grading scores what the answer key "
                "settles outright, so you only grade the rest. Nothing is "
                "read again; grading opens when it is done.",
                [
                    (ACTION_OPEN, "Open without it"),
                    (ACTION_AUTO_GRADE, "Auto-grade and open"),
                ],
                ai_status=self._ai_status_text("pipe"),
            ),
            answered,
        )
        return True

    def _ask_about_folder(
        self,
        found: ClassFolderScan,
        title: str,
        summary: str,
        note: str,
        actions: list[tuple[str, str]],
        *,
        draft=None,  # noqa: ANN001 - (DraftKey, Path) from `_draft_key`
    ) -> None:
        def answered(action: str | None) -> None:
            if action == ACTION_PREPARE:
                has_key = bool(self.query_one("#pipe-qa", Input).value.strip())
                if not has_key and draft is not None and self._draft_and_run(
                    draft, from_launch=True
                ):
                    return
                if not has_key:
                    # The key can be made while the scans are read.
                    self._open_answer_key_tool(found, scroll=False)
                self._run_pipeline(from_launch=True, offer_draft=False)
            elif action == ACTION_SETTINGS:
                self._show_pipeline_card(settings=True)
            elif action == ACTION_CREATE_KEY:
                self._open_answer_key_tool(found)

        runs = any(action == ACTION_PREPARE for action, _label in actions)
        self.app.push_screen(
            PrepareFolderScreen(
                title,
                summary,
                note,
                actions,
                ai_status=self._ai_status_text("pipe") if runs else "",
            ),
            answered,
        )

    def _prepare_summary(self, folder: Path) -> str:
        """What a run from the pipeline card would use, as the card stands."""

        def shown(input_id: str) -> str:
            value = self._clean_path(self.query_one(f"#{input_id}", Input).value)
            if not value:
                return ""
            path = Path(value)
            try:
                name = path.relative_to(folder).as_posix()
            except ValueError:
                return value
            return f"{name}/" if path.is_dir() else name

        def ticked(checkbox_id: str) -> bool:
            return self.query_one(f"#{checkbox_id}", Checkbox).value

        scans = self._section_scans()
        rows = [
            (
                "scanned bundle",
                f"one per section, {len(scans)} sections"
                if scans
                else shown("pipe-scan"),
            ),
            ("answer key", shown("pipe-qa")),
        ]
        if ticked("pipe-many-rosters"):
            count = len(self._section_rosters())
            rows.append(("roster", f"{count} section roster(s)"))
        else:
            rows.append(
                ("roster", shown("pipe-roster") if ticked("pipe-use-roster") else "")
            )
        if ticked("pipe-use-region-map"):
            rows.append(("region map", shown("pipe-region-map")))
        else:
            rows.append(
                (
                    "blank template",
                    shown("pipe-template") if ticked("pipe-use-template") else "",
                )
            )
        lines = [f"Folder: {folder}"]
        for label, value in rows:
            if value:
                lines.append(f"✓ {label:<15} {value}")
            elif label == "answer key":
                lines.append(f"· {label:<15} none found")
            else:
                lines.append(f"· {label:<15} not used")
        pages = self.query_one("#pipe-pages", Input).value.strip()
        lines.append(f"  {'pages each':<15} {pages or 'worked out from the scan'}")
        lines.append(
            "Settings: the ones saved with this folder."
            if load_folder_settings(folder) is not None
            else "Settings: the usual ones; nothing is saved for this folder yet."
        )
        return "\n".join(lines)

    def _show_folder_in_pipeline_card(self, folder: Path) -> None:
        """Point the pipeline card at a folder chosen on the Start grading card.

        A different folder starts from a clean card: one assignment's page
        count or template must not run against another's scans. What the
        folder saved for itself is then brought back by `_load_class_folder`.
        """
        if str(folder) != getattr(self, "_pipe_folder_loaded", None):
            autofilled: dict[str, str] = getattr(self, "_autofilled", {})
            for input_id in (
                "pipe-scan",
                "pipe-qa",
                "pipe-roster",
                "pipe-template",
                "pipe-region-map",
            ):
                self.query_one(f"#{input_id}", Input).value = ""
                autofilled.pop(input_id, None)
            autofilled.pop("pipe-scan-list", None)
            autofilled.pop("pipe-roster-list", None)
            self._apply_folder_settings(FolderPipelineSettings())
            # One-shot overrides belong to the run they were ticked for.
            self.query_one("#pipe-force", Checkbox).value = False
            self.query_one("#pipe-fresh", Checkbox).value = False
            self.query_one("#pipe-no-key", Checkbox).value = False
        self.query_one("#pipe-out", Input).value = str(folder)
        self._load_class_folder()

    def _show_pipeline_card(self, *, settings: bool = False) -> None:
        """Open Prepare an assignment and bring it into view."""
        section = self.query_one("#section-pipeline", Collapsible)
        section.collapsed = False
        if settings:
            self.query_one("#pipe-settings", Collapsible).collapsed = False
        self.query_one("#pipe-out", Input).focus(scroll_visible=False)
        self.call_after_refresh(section.scroll_visible, top=True)

    def _open_answer_key_tool(
        self, found: ClassFolderScan, *, scroll: bool = True
    ) -> None:
        """Open Create answer key filled in for this folder, and remember to
        carry on with the folder once the key is written."""
        folder = found.folder
        source = find_answer_key_source(folder)
        output = next(
            (
                folder / name
                for name in _NEW_ANSWER_KEY_NAMES
                if not (folder / name).exists()
            ),
            None,
        )
        self._autofill("ak-input", source)
        self._autofill("ak-output", output)
        # The template only helps a scanned key line up with its questions.
        from_pdf = source is not None and source.suffix.lower() == ".pdf"
        self._autofill(
            "ak-template-path",
            found.blank_template if from_pdf else None,
            checkbox_id="ak-use-template",
        )
        self._ak_resume_folder = folder

        section = self.query_one("#section-answer-key", Collapsible)
        section.collapsed = False
        if scroll:
            self.query_one("#ak-input", Input).focus(scroll_visible=False)
            self.call_after_refresh(section.scroll_visible, top=True)
        if source is not None:
            message = (
                f"Create answer key is filled in from {source.name}. Check "
                "it and press Create answer key."
            )
        else:
            message = (
                "In Create answer key, choose your assignment or solutions "
                "file, then press Create answer key. Press t for templates "
                "to copy."
            )
        self.app.notify(message, timeout=10)

    def _resume_launch_after_key(self, output: str) -> None:
        """An answer key asked for by Launch Grading was written: carry on
        with that folder, which asks again now that it has one."""
        folder: Path | None = getattr(self, "_ak_resume_folder", None)
        if folder is None or Path(output).parent != folder:
            return
        self._ak_resume_folder = None
        if getattr(self, "_pipe_waiting_for_key", None) == folder:
            # A run stopped for exactly this key: carry on with it. The yes
            # was given when the run was started.
            self._pipe_waiting_for_key = None
            if not getattr(self, "extraction_in_progress", False):
                self._show_folder_in_pipeline_card(folder)
                self._run_pipeline(from_launch=getattr(self, "_pipe_from_launch", False))
                return
        self.query_one("#tui-class-folder", Input).value = str(folder)
        # Scan it again: the folder has changed since it was last read.
        self._launch_folder_scanned = None
        self.call_after_refresh(
            self.query_one("#launch-section").scroll_visible, top=True
        )
        # Still headed where the press that began this was: grading, or the browser.
        self._launch_grading(browser=getattr(self, "_launch_to_browser", False))

    def _open_in_browser(self) -> None:
        # Everything Launch Grading does for a folder that is not ready.
        self._launch_grading(browser=True)

    def _set_browser_enabled(self, enabled: bool) -> None:
        self.query_one("#browser-open-button").set_class(not enabled, "hidden")
        self.query_one(BrowserHealth).set_enabled(enabled)
        try:
            browser_app.save_browser_preferences(
                replace(browser_app.load_browser_preferences(), enabled=enabled)
            )
        except OSError as exc:
            self.app.notify(f"Could not save this choice: {exc}", severity="error")

    def _autofill(
        self, input_id: str, path: Path | None, *, checkbox_id: str | None = None
    ) -> None:
        # A path the user typed survives a re-scan; one a scan filled is replaced.
        autofilled: dict[str, str] = getattr(self, "_autofilled", {})
        self._autofilled = autofilled
        widget = self.query_one(f"#{input_id}", Input)
        current = widget.value.strip()
        if current and current != autofilled.get(input_id):
            return
        value = str(path) if path is not None else ""
        widget.value = value
        autofilled[input_id] = value
        if checkbox_id is not None:
            self.query_one(f"#{checkbox_id}", Checkbox).value = bool(value)

    def _build_pipeline_request(self) -> PipelineRequest | None:
        scan = self._clean_path(self.query_one("#pipe-scan", Input).value)
        out = self._clean_path(self.query_one("#pipe-out", Input).value)
        if out and Path(out).is_dir():
            # A folder typed and run without leaving the box, or one whose scan
            # has not been found yet: read it now.
            if not scan:
                self._load_class_folder()
            else:
                self._load_class_folder(only_if_changed=True)
            scan = self._clean_path(self.query_one("#pipe-scan", Input).value)
        if not out:
            self.app.notify("Choose the class folder first.", severity="error")
            return None
        section_scans = self._section_scans()
        if self.query_one("#pipe-many-scans", Checkbox).value and not section_scans:
            self.query_one("#pipe-advanced", Collapsible).collapsed = False
            self.app.notify(
                'Add one scan per section, or untick "One scan per class section".',
                severity="error",
            )
            return None
        if section_scans:
            # The scans' own folder stands for them where one path is asked.
            scan = os.path.commonpath(
                [
                    str(parse_roster_arg(entry)[0].expanduser().resolve().parent)
                    for entry in section_scans
                ]
            )
        if not scan:
            self.query_one("#pipe-advanced", Collapsible).collapsed = False
            self.app.notify(
                "No scanned bundle was found in that folder. Choose it under "
                "Advanced settings.",
                severity="error",
            )
            return None

        qa = self._clean_path(self.query_one("#pipe-qa", Input).value)

        roster: Path | None = None
        if self.query_one("#pipe-use-roster", Checkbox).value:
            roster_path = self._clean_path(self.query_one("#pipe-roster", Input).value)
            if not roster_path:
                self.app.notify(
                    'Choose a roster file, or untick "Match student names '
                    'against a roster".',
                    severity="error",
                )
                return None
            roster = Path(roster_path)

        template: Path | None = None
        if self.query_one("#pipe-use-template", Checkbox).value:
            template_path = self._clean_path(
                self.query_one("#pipe-template", Input).value
            )
            if not template_path:
                self.app.notify(
                    'Choose a template PDF, or untick "Align with a blank '
                    'template PDF".',
                    severity="error",
                )
                return None
            template = Path(template_path)

        region_map: Path | None = None
        if self.query_one("#pipe-use-region-map", Checkbox).value:
            region_map_path = self._clean_path(
                self.query_one("#pipe-region-map", Input).value
            )
            if not region_map_path:
                self.app.notify(
                    "Choose the region map, or untick "
                    '"Use a region map (your own assignment)".',
                    severity="error",
                )
                return None
            region_map = Path(region_map_path)
            # The two boxes untick each other; this covers a state set in code.
            template = None

        section_rosters = self._section_rosters()
        if self.query_one("#pipe-many-rosters", Checkbox).value and not section_rosters:
            self.app.notify(
                'Add at least one roster, or untick "This stack mixes several '
                'class sections".',
                severity="error",
            )
            return None

        jobs = self._pipe_jobs()
        if jobs == -1:
            self.app.notify(
                f"Parallel jobs must be a whole number from 1 to {_CPU_COUNT} "
                "(or blank to choose automatically).",
                severity="error",
            )
            return None

        pages_str = self.query_one("#pipe-pages", Input).value.strip()
        pages: int | None = None
        if pages_str:
            if not pages_str.isdigit() or int(pages_str) < 1:
                self.app.notify(
                    "Pages per submission must be a whole number (or blank "
                    "to auto-detect).",
                    severity="error",
                )
                return None
            pages = int(pages_str)

        current = self._ai()
        llm = LlmOptions(
            read_handwriting=current.read_handwriting,
            grade_written=current.grade_written,
            host=current.host,
        )

        request = PipelineRequest(
            scan=Path(scan),
            output_dir=Path(out),
            qa_file=Path(qa) if qa else None,
            roster=roster,
            section_rosters=section_rosters,
            section_scans=section_scans,
            blank_template=template,
            region_map=region_map,
            pages_per_submission=pages,
            reorder_pages=self.query_one("#pipe-reorder", Checkbox).value,
            llm=llm,
            jobs=jobs,
            auto_grade=current.auto_grade,
            force=self.query_one("#pipe-force", Checkbox).value,
            fresh=self.query_one("#pipe-fresh", Checkbox).value,
            read_without_key=self.query_one("#pipe-no-key", Checkbox).value,
            draft_rubrics=self.query_one("#pipe-draft-rubrics", Checkbox).value,
        )
        try:
            section_requests(request)
        except ValueError as exc:
            self.query_one("#pipe-advanced", Collapsible).collapsed = False
            self.app.notify(str(exc), severity="error", timeout=10)
            return None
        return request

    def _run_pipeline_preview(self) -> None:
        request = self._build_pipeline_request()
        if request is None:
            return
        self.query_one("#pipe-preview-output").add_class("hidden")
        self.query_one("#pipe-run", Button).disabled = True
        self.query_one("#pipe-preview", Button).disabled = True
        self._pipeline_preview_worker(request)

    @work(thread=True, exclusive=True, group="pipeline-preview")
    def _pipeline_preview_worker(self, request: PipelineRequest) -> None:
        from opengrader.core.pipeline_preview import describe_plan, technical_lines

        app = self.app
        try:
            plans = plan_pipeline(request)
            summary = describe_plan(request, plans).lines()
            details = technical_lines(plans, request)
        except Exception as exc:
            app.call_from_thread(self._pipe_preview_finish, None, None, str(exc))
            return
        app.call_from_thread(self._pipe_preview_finish, summary, details, None)

    def _pipe_preview_finish(
        self,
        summary: list[str] | None,
        details: list[str] | None,
        error: str | None,
    ) -> None:
        self.query_one("#pipe-run", Button).disabled = False
        self.query_one("#pipe-preview", Button).disabled = False
        if error is not None or summary is None or details is None:
            self.app.notify(f"Could not build a plan: {error}", severity="error")
            return
        self.query_one("#pipe-preview-summary", Static).update("\n".join(summary))
        self.query_one("#pipe-preview-details", Static).update("\n".join(details))
        self.query_one("#pipe-preview-output").remove_class("hidden")

    def _run_pipeline(
        self,
        *,
        from_launch: bool = False,
        grade_as_ready: bool | None = None,
        offer_draft: bool = True,
    ) -> None:
        """Run the card as it stands.

        `from_launch` is a run the Start grading card asked for: its progress
        is shown on that card too. `grade_as_ready` overrides the card's
        "Start grading as submissions arrive" for this one run. `offer_draft`
        off is a run whose answer-key question was already asked.
        """
        request = self._build_pipeline_request()
        if request is None:
            if from_launch:
                # The reason was just notified; the fields it names are here.
                self._show_pipeline_card()
            return

        if offer_draft and request.qa_file is None and request.blank_template is not None:
            draft = self._draft_key(request.output_dir)
            if draft is not None:
                self._offer_draft_then_run(request, draft)
                return

        self._save_pipeline_settings(request)

        asap = self.query_one("#pipe-grade-asap", Checkbox).value
        if grade_as_ready is not None:
            asap = grade_as_ready
        # Where this run opens the folder when it is ready. The browser app
        # is given finished work: it opens once, at the end.
        self._pipe_to_browser = from_launch and getattr(self, "_launch_to_browser", False)
        if self._pipe_to_browser:
            asap = False
        qa_path = str(request.qa_file) if request.qa_file is not None else ""
        if asap and not qa_path and not from_launch:
            self.app.notify(
                "No answer key set — grading will open once the whole run "
                "finishes instead of as submissions arrive. Put an answer "
                "key in the class folder to grade sooner.",
            )

        self._pipe_log_lines: list[str] = []
        self._pipe_total = 0
        self._pipe_steps = self._pipeline_steps(request)
        self._pipe_step = ""
        self._pipe_from_launch = from_launch
        self.query_one("#pipe-preview-output").add_class("hidden")
        for bar in self._pipe_bars():
            bar.update(total=None, progress=0)
        self.query_one("#pipe-stage-log", Static).update("")
        self.query_one("#launch-progress").set_class(not from_launch, "hidden")
        self._pipe_start(request, asap, qa_path)

    def _pipe_start(self, request: PipelineRequest, asap: bool, qa_path: str) -> None:
        """Start the worker — also after a question the run stopped to ask
        (the cloud send, the page order), where the same request runs again."""
        self.query_one("#pipe-progress").remove_class("hidden")
        self.query_one("#launch-progress-bar").remove_class("hidden")
        self._pipe_status("Starting…")
        self.query_one("#pipe-run", Button).disabled = True
        self.query_one("#pipe-preview", Button).disabled = True
        self.extraction_in_progress = True
        self._pipeline_worker(request, asap, qa_path)

    @staticmethod
    def _pipeline_steps(request: PipelineRequest) -> tuple[str, ...]:
        """The stages this run can reach, for "Step 2 of 4". The rest are
        skipped by `plan_pipeline` whatever the ledger says."""
        has_key = request.qa_file is not None
        skipped = set()
        if request.input_is_split:
            skipped.add(STAGE_SPLIT)
        if not (request.draft_rubrics and has_key):
            skipped.add(STAGE_DRAFT_RUBRICS)
        if not (request.auto_grade and has_key):
            skipped.add(STAGE_AUTO_GRADE)
        return tuple(stage for stage in PIPELINE_STAGES if stage not in skipped)

    def _pipe_bars(self) -> tuple[ProgressBar, ProgressBar]:
        return (
            self.query_one("#pipe-progress-bar", ProgressBar),
            self.query_one("#launch-progress-bar", ProgressBar),
        )

    def _pipe_status(self, message: str) -> None:
        """The run's one-line status, on the pipeline card and on Start grading."""
        self.query_one("#pipe-progress-status", Static).update(message)
        self.query_one("#launch-progress-status", Static).update(message)

    def _pipe_detail(self, detail: str) -> None:
        """What the current step is working on, kept behind the step's name."""
        step = getattr(self, "_pipe_step", "")
        if step:
            detail = f"{step} — {detail}" if detail else f"{step}…"
        self._pipe_status(detail)

    @work(thread=True, exclusive=True, group="pipeline")
    def _pipeline_worker(
        self, request: PipelineRequest, asap: bool, qa_path: str
    ) -> None:
        app = self.app
        state = {"stage": "", "launched_early": False}

        watcher = ExtractionWatcher(request.submissions_dir) if asap and qa_path else None

        def maybe_launch_early() -> None:
            if watcher is None or state["launched_early"] or state["stage"] != STAGE_EXTRACT:
                return
            result = watcher.poll(known_active=True)
            if result.new_keys:
                state["launched_early"] = True
                app.call_from_thread(
                    self._pipe_launch_early, qa_path, str(request.submissions_dir)
                )

        def on_stage_start(stage: str) -> None:
            state["stage"] = stage
            app.call_from_thread(self._pipe_stage_start, stage)

        def on_stage_finish(outcome: StageOutcome) -> None:
            app.call_from_thread(self._pipe_stage_finish, outcome)

        def on_status(message: str) -> None:
            app.call_from_thread(self._pipe_set_status, message)
            maybe_launch_early()

        def on_file_start(name: str, count: int) -> None:
            app.call_from_thread(self._pipe_file_start, name, count)
            maybe_launch_early()

        def on_response(name: str, item: str) -> None:
            app.call_from_thread(self._pipe_response, name, item)

        progress = PipelineProgress(
            on_stage_start=on_stage_start,
            on_stage_finish=on_stage_finish,
            on_status=on_status,
            on_file_start=on_file_start,
            on_response=on_response,
        )

        try:
            report = run_pipeline(request, progress=progress)
        except Exception as exc:
            app.call_from_thread(self._pipe_finish_error, f"Pipeline error: {exc}")
            return

        app.call_from_thread(
            self._pipe_finish, report, state["launched_early"], qa_path, request, asap
        )

    def _pipe_stage_start(self, stage: str) -> None:
        steps = getattr(self, "_pipe_steps", PIPELINE_STAGES)
        if stage not in steps:
            # Skipped for this run; it is over before it could be read.
            return
        self._pipe_step = (
            f"Step {steps.index(stage) + 1} of {len(steps)} · "
            f"{_PIPE_STAGE_LABELS.get(stage, stage)}"
        )
        self._pipe_status(f"{self._pipe_step}…")

    def _pipe_stage_finish(self, outcome: StageOutcome) -> None:
        glyph = _PIPE_STATUS_GLYPH.get(outcome.status, "·")
        self._pipe_log_lines.append(f"{glyph} {outcome.stage}: {outcome.message}")
        if (
            outcome.status in (StageStatus.BLOCKED, StageStatus.FAILED, StageStatus.PAUSED)
            and outcome.detail
        ):
            for line in outcome.detail.splitlines():
                self._pipe_log_lines.append(f"    {line}")
        self.query_one("#pipe-stage-log", Static).update("\n".join(self._pipe_log_lines))

    def _pipe_set_status(self, message: str) -> None:
        self._pipe_detail(message.strip())

    def _pipe_file_start(self, name: str, count: int) -> None:
        self._pipe_total = getattr(self, "_pipe_total", 0) + count
        for bar in self._pipe_bars():
            bar.update(total=self._pipe_total)
        self._pipe_detail(f"{name} ({count})")

    def _pipe_response(self, name: str, item: str) -> None:
        for bar in self._pipe_bars():
            bar.advance(1)
        self._pipe_detail(f"{name} · {item}")

    def _pipe_launch_early(self, qa_path: str, submissions_path: str) -> None:
        self.app.notify(
            "Submissions are ready — opening the grading workspace while the "
            "rest of the pipeline keeps running in the background."
        )
        self.post_message(self.GradeWhileExtractingRequested(qa_path, submissions_path))

    def _pipe_open(self, qa_path: str, sub_path: str) -> None:
        """Open what a finished run prepared, where that run was asked to."""
        if getattr(self, "_pipe_to_browser", False):
            self.query_one(BrowserLauncher).open_folder(qa_path, sub_path)
        else:
            self.post_message(self.LaunchRequested(qa_path, sub_path))

    def _pipe_reset_buttons(self) -> None:
        self.query_one("#pipe-run", Button).disabled = False
        self.query_one("#pipe-preview", Button).disabled = False
        # Start grading keeps the last status line, without a bar still moving.
        self.query_one("#launch-progress-bar").add_class("hidden")
        self.extraction_in_progress = False

    def _pipe_stopped_short(self) -> None:
        """A run asked for on Start grading stopped: open the card that holds
        the stage log and the settings that fix it."""
        if getattr(self, "_pipe_from_launch", False):
            self.query_one("#section-pipeline", Collapsible).collapsed = False

    def _pipe_finish_error(self, message: str) -> None:
        self._pipe_reset_buttons()
        self.app.notify(message, severity="error")

    def _pipe_finish(
        self,
        report: PipelineReport,
        launched_early: bool,
        qa_path: str,
        request: PipelineRequest,
        asap: bool,
    ) -> None:
        self._pipe_reset_buttons()
        # A run re-started after a question, while grading was already open.
        launched_early = launched_early or getattr(self, "_pipe_opened_before_rerun", False)
        self._pipe_opened_before_rerun = False

        if report.cloud_confirmation_needed:
            self._confirm_cloud_and_rerun(report, request, asap, qa_path)
            return

        blocked = next(
            (o for o in report.stages if o.status is StageStatus.BLOCKED), None
        )
        if blocked is not None:
            self._pipe_status(
                f"Stopped: {blocked.message}"
            )
            self._pipe_stopped_short()
            self.app.notify(
                f"{blocked.message} Fix the scan and re-run, or tick "
                '"Grade it anyway (ignore scan warnings)" to grade it as it is.',
                severity="error",
            )
            return

        if not report.ok:
            failed = next(
                (o for o in report.stages if o.status is StageStatus.FAILED), None
            )
            message = failed.message if failed is not None else "The pipeline failed."
            self._pipe_status(f"Failed: {message}")
            self._pipe_stopped_short()
            self.app.notify(f"Pipeline failed: {message}", severity="error")
            return

        if report.split_review_needed:
            self._confirm_split_and_rerun(request, asap, qa_path)
            return

        if report.page_order_review_needed:
            self._confirm_page_order_and_rerun(request, asap, qa_path)
            return

        if report.answer_key_needed:
            self._wait_for_answer_key(request)
            return

        if report.ai_unavailable:
            paused = next(
                o for o in report.stages if o.status is StageStatus.PAUSED
            )
            problem = " ".join(paused.detail.splitlines()[1:])
            self._pipe_status("Waiting for you to choose whether to auto-grade without AI…")
            self.run_worker(
                self._ask_grade_without_ai(problem, request, asap, launched_early, qa_path),
                exclusive=True,
                group="pipeline-grade-without-ai",
            )
            return

        if report.paused_for_review:
            paused = next(
                o for o in report.stages if o.status is StageStatus.PAUSED
            )
            self._pipe_status(
                f"Paused: {paused.message}"
            )
            self.app.notify(
                "The AI drafted rubric items into the answer key. Check them in "
                "grading (press e on a question), then press Y to have the AI "
                "grade with them.",
                timeout=12,
            )
            if not launched_early and report.ready_to_grade and qa_path:
                self._pipe_open(qa_path, str(report.submissions_dir))
            return

        if launched_early:
            self.query_one("#launch-progress").add_class("hidden")
            self.app.notify(
                f"Pipeline finished — {report.submission_count} submission(s) "
                f"in {report.submissions_dir}."
            )
            return

        if report.ready_to_grade and qa_path:
            self.query_one("#launch-progress").add_class("hidden")
            self.app.notify(
                f"{report.submission_count} submission(s) ready — opening "
                + ("the browser." if getattr(self, "_pipe_to_browser", False) else "the grading workspace.")
            )
            self._pipe_open(qa_path, str(report.submissions_dir))
            return

        if report.ready_to_grade:
            self._pipe_status(
                f"{report.submission_count} submission(s) are read and waiting "
                "for an answer key. Press Launch Grading once it is in the folder."
            )
            self.app.notify(
                f"{report.submission_count} submission(s) ready in "
                f"{report.submissions_dir}. Put an answer key in the class folder, or "
                'open them from "Start grading".'
            )
            return

        self.app.notify(
            "Pipeline finished, but no submissions were produced.",
            severity="warning",
        )

    @staticmethod
    def _cloud_preflight_statement(report: PipelineReport) -> tuple[str, str]:
        """The scale-and-cost sentence for the run that just refused to send anything.

        Prefers a `cloud_preflight` field on the report when the core carries
        one; otherwise falls back to the BLOCKED outcome's detail, which is
        the same statement with a "cloud-preflight:\\n" marker in front of it.
        """
        preflight = getattr(report, "cloud_preflight", None)
        if preflight is not None:
            return preflight.statement(), preflight.host
        marker = "cloud-preflight:\n"
        blocked = next(
            (
                o
                for o in report.stages
                if o.status is StageStatus.BLOCKED and o.detail.startswith(marker)
            ),
            None,
        )
        statement = blocked.detail[len(marker):] if blocked is not None else (
            "This run would send student work to the configured AI provider."
        )
        return statement, ""

    def _wait_for_answer_key(self, request: PipelineRequest) -> None:
        """The run split the scans and stopped: nothing but an answer key can
        say where their questions are. Open Create answer key for the folder
        and carry on by itself once the key is written."""
        folder = request.output_dir
        self._pipe_status(
            "The scans are checked and split. Reading them waits for an "
            "answer key: on these pages it is what finds the questions. "
            "Create answer key is open below; the run carries on when the "
            "key is made."
        )
        self._pipe_waiting_for_key = folder
        try:
            found = scan_class_folder(folder)
        except OSError:
            return
        self._open_answer_key_tool(found, scroll=False)

    def _confirm_cloud_and_rerun(
        self,
        report: PipelineReport,
        request: PipelineRequest,
        asap: bool,
        qa_path: str,
    ) -> None:
        statement, host = self._cloud_preflight_statement(report)
        self._pipe_status(
            "Waiting for you to confirm the cloud send…"
        )
        self.run_worker(
            self._ask_cloud_confirmation(statement, host, request, asap, qa_path),
            exclusive=True,
            group="pipeline-cloud-confirm",
        )

    async def _ask_cloud_confirmation(
        self,
        statement: str,
        host: str,
        request: PipelineRequest,
        asap: bool,
        qa_path: str,
    ) -> None:
        confirmed = await self.app.push_screen_wait(
            CloudPreflightConfirmScreen(statement, host)
        )
        if not confirmed:
            self._pipe_reset_buttons()
            self.query_one("#pipe-progress").add_class("hidden")
            self._pipe_status(
                "Not sent — the cloud send was declined. Nothing left this "
                "computer; re-run when you're ready."
            )
            return
        self._pipe_start(replace(request, send_to_cloud_confirmed=True), asap, qa_path)

    async def _ask_grade_without_ai(
        self,
        problem: str,
        request: PipelineRequest,
        asap: bool,
        launched_early: bool,
        qa_path: str,
    ) -> None:
        """The pipeline paused before auto-grading: the AI can't be reached.
        Run again either way: graded without AI, or with auto-grading off, so
        the submissions are published and ready to grade by hand."""
        without_ai = await self.app.push_screen_wait(GradeWithoutAIScreen(problem))
        rerun = (
            replace(request, grade_without_ai=True)
            if without_ai
            else replace(request, auto_grade=False)
        )
        # Grading already open stays the one window; the re-run opens nothing.
        self._pipe_opened_before_rerun = launched_early
        self._pipe_start(rerun, asap and not launched_early, qa_path)

    def _confirm_split_and_rerun(
        self, request: PipelineRequest, asap: bool, qa_path: str
    ) -> None:
        self._pipe_status("Waiting for you to check the split…")
        self.run_worker(
            self._ask_split_review(request, asap, qa_path),
            exclusive=True,
            group="pipeline-split-review",
        )

    async def _ask_split_review(
        self, request: PipelineRequest, asap: bool, qa_path: str
    ) -> None:
        """The pipeline paused before extraction because the split looks
        wrong somewhere: show where, apply the fixes the user ticks, and run
        again (the split is reused)."""
        import asyncio

        from opengrader.core.pages.split_review import pending_split_findings
        from opengrader.core.pipeline import answer_split_review

        def first_pending() -> Path:
            # With one scan per section, the section the run stopped at.
            return next(
                (folder for folder in request.review_dirs if pending_split_findings(folder)),
                request.extract_source,
            )

        split_dir = await asyncio.to_thread(first_pending)
        pending = await asyncio.to_thread(pending_split_findings, split_dir)
        chosen: tuple[str, ...] | None = ()
        if pending:
            chosen = await self.app.push_screen_wait(SplitReviewScreen(pending, split_dir))
        if chosen is None:
            self._pipe_reset_buttons()
            self._pipe_status(
                f"Paused before extraction — nothing was changed. Check the PDFs in "
                f"{split_dir}, fix any that need it, then run again."
            )
            return
        await asyncio.to_thread(
            answer_split_review,
            request,
            split_dir,
            fix=list(chosen),
            keep=[finding.key for finding in pending],
        )
        self._pipe_start(request, asap, qa_path)

    def _confirm_page_order_and_rerun(
        self, request: PipelineRequest, asap: bool, qa_path: str
    ) -> None:
        self._pipe_status(
            "Waiting for you to confirm the page order…"
        )
        self.run_worker(
            self._ask_page_order(request, asap, qa_path),
            exclusive=True,
            group="pipeline-page-order",
        )

    async def _ask_page_order(
        self, request: PipelineRequest, asap: bool, qa_path: str
    ) -> None:
        """The pipeline paused before extraction: ask which submissions to sort,
        apply the answer to the split PDFs, and run again (the split is reused)."""
        import asyncio

        from opengrader.core.pages.order_review import (
            keep_pages,
            pending_findings,
            sort_pages,
        )

        def first_pending() -> Path:
            # With one scan per section, the section the run stopped at.
            return next(
                (folder for folder in request.review_dirs if pending_findings(folder)),
                request.extract_source,
            )

        split_dir = await asyncio.to_thread(first_pending)
        pending = await asyncio.to_thread(pending_findings, split_dir)
        chosen: tuple[str, ...] | None = ()
        if pending:
            chosen = await self.app.push_screen_wait(PageOrderScreen(pending, split_dir))
        if chosen is None:
            self._pipe_reset_buttons()
            self._pipe_status(
                f"Paused before extraction — nothing was moved. Check the PDFs in "
                f"{split_dir}, rearrange any that need it, then run again."
            )
            return
        names = [finding.name for finding in pending]
        done = await asyncio.to_thread(sort_pages, split_dir, chosen)
        await asyncio.to_thread(
            keep_pages, split_dir, [name for name in names if name not in done]
        )
        self._pipe_start(request, asap, qa_path)

    def _confirm_tool_cloud_send(
        self,
        needed: CloudConfirmationNeeded,
        status_selector: str,
        rerun_confirmed,
        finish,
    ) -> None:
        """Ask before a tool run sends to the cloud; the tool itself stopped first.

        The re-read and Answer key tools run their CLI in-process with
        `prompt_cloud_send=False`, so instead of an `input()` prompt the
        Textual worker cannot answer, the CLI raises before sending anything.
        Confirming re-runs it with `--yes-send-to-cloud`; declining calls
        `finish` with a message and nothing is sent.
        """
        self.query_one(status_selector, Static).update(
            "Waiting for you to confirm the cloud send…"
        )
        self.run_worker(
            self._ask_tool_cloud_confirmation(needed, rerun_confirmed, finish),
            exclusive=True,
            group="tool-cloud-confirm",
        )

    async def _ask_tool_cloud_confirmation(
        self, needed: CloudConfirmationNeeded, rerun_confirmed, finish
    ) -> None:
        confirmed = await self.app.push_screen_wait(
            CloudPreflightConfirmScreen(needed.statement(), needed.preflight.host)
        )
        if confirmed:
            rerun_confirmed()
        else:
            finish(
                "Not sent — the cloud send was declined. Nothing left this computer."
            )

    def _run_answer_key(self) -> None:
        input_file = self._clean_path(self.query_one("#ak-input", Input).value)
        output_md = self._clean_path(self.query_one("#ak-output", Input).value)
        if not input_file or not output_md:
            self.app.notify("Input file and output path are required.", severity="error")
            return

        raw_points = self.query_one("#ak-default-points", Input).value.strip()
        try:
            points = float(raw_points) if raw_points else DEFAULT_QUESTION_POINTS
        except ValueError:
            points = 0.0
        # Whole points: an answer key's question points are whole numbers.
        if points < 1 or not points.is_integer():
            self.app.notify(
                "Points when none are given must be a whole number, 1 or "
                f"more (or blank for {format_points(DEFAULT_QUESTION_POINTS)}).",
                severity="error",
            )
            return
        try:
            save_machine_settings(
                replace(
                    load_machine_settings(),
                    answer_key_points=points if raw_points else None,
                )
            )
        except OSError:
            # Remembering is a convenience; the answer key is still made.
            pass

        argv = [
            "--input", input_file, "--output", output_md,
            "--default-points", format_points(points),
        ]
        self._ak_points = points
        title = self.query_one("#ak-title", Input).value.strip()
        if title:
            argv.extend(["--title", title])
        if self.query_one("#ak-use-template", Checkbox).value:
            template = self._clean_path(self.query_one("#ak-template-path", Input).value)
            if template:
                argv.extend(["--blank-template", template])

        self._ak_total = 0
        self._ak_output = output_md
        bar = self.query_one("#ak-progress-bar", ProgressBar)
        bar.update(total=None, progress=0)
        self.query_one("#ak-progress-status", Static).update("Working…")
        self.query_one("#ak-progress").remove_class("hidden")
        self.query_one("#run-answer-key", Button).disabled = True
        self._answer_key_worker(argv)

    @work(thread=True, exclusive=True, group="answer-key")
    def _answer_key_worker(self, argv: list[str]) -> None:
        app = self.app

        def on_status(message: str) -> None:
            app.call_from_thread(self._ak_set_status, message)

        def on_progress(current: int, total: int) -> None:
            app.call_from_thread(self._ak_progress, current, total)

        defaulted: list[tuple[int, int]] = []

        try:
            run_answer_key_cli(
                argv,
                status_callback=on_status,
                progress_callback=on_progress,
                prompt_cloud_send=False,
                default_points_callback=lambda given, total: defaulted.append(
                    (given, total)
                ),
            )
        except CloudConfirmationNeeded as needed:
            app.call_from_thread(
                self._confirm_tool_cloud_send,
                needed,
                "#ak-progress-status",
                lambda: self._answer_key_worker([*argv, "--yes-send-to-cloud"]),
                lambda message: self._ak_finish(message, False),
            )
            return
        except SystemExit as exc:
            app.call_from_thread(self._ak_finish, f"Answer key failed (exit {exc.code}).", True)
            return
        except Exception as exc:
            app.call_from_thread(self._ak_finish, f"Answer key error: {exc}", True)
            return
        message = "Answer key created."
        if defaulted:
            note = default_points_note(
                *defaulted[-1], getattr(self, "_ak_points", DEFAULT_QUESTION_POINTS)
            )
            if note:
                message = f"{message} {note}"
        app.call_from_thread(self._ak_finish, message, False, created=True)

    def _ak_progress(self, current: int, total: int) -> None:
        bar = self.query_one("#ak-progress-bar", ProgressBar)
        if getattr(self, "_ak_total", 0) != total:
            self._ak_total = total
            bar.update(total=total)
        bar.update(progress=current)
        self.query_one("#ak-progress-status", Static).update(
            f"Reading question {current} of {total}…"
        )

    def _ak_set_status(self, message: str) -> None:
        self.query_one("#ak-progress-status", Static).update(message.strip())

    def _ak_finish(self, message: str, is_error: bool, *, created: bool = False) -> None:
        self.query_one("#run-answer-key", Button).disabled = False
        self.query_one("#ak-progress").add_class("hidden")
        self.app.notify(
            message,
            severity="error" if is_error else "information",
            # A created key may carry a note about points worth reading.
            timeout=12 if created else 5,
        )
        if created:
            self._resume_launch_after_key(getattr(self, "_ak_output", ""))

    def _sync_template_options(self) -> None:
        bubble = self.query_one("#tmpl-bubble-sheet", Checkbox).value
        qr = self.query_one("#tmpl-qr", Checkbox).value
        self.query_one("#tmpl-bubble-note").set_class(not bubble, "hidden")
        self.query_one("#tmpl-roster-area").set_class(not (qr or bubble), "hidden")
        self.query_one("#tmpl-batch-area").set_class(not bubble, "hidden")
        shuffle = self.query_one("#tmpl-shuffle", Checkbox).value
        self.query_one("#tmpl-copies-area").set_class(
            not (bubble and shuffle), "hidden"
        )

    def _run_template(self) -> None:
        qa_md = self._clean_path(self.query_one("#tmpl-qa-input", Input).value)
        output_pdf = self._clean_path(self.query_one("#tmpl-output", Input).value)
        if not qa_md or not output_pdf:
            self.app.notify(
                "Both a QA file and an output PDF path are required.",
                severity="error",
            )
            return

        argv = ["generate-template", "--qa-file", qa_md, "--output-pdf", output_pdf]
        title = self.query_one("#tmpl-title", Input).value.strip()
        if title:
            argv.extend(["--title", title])
        if self.query_one("#tmpl-all-versions", Checkbox).value:
            argv.append("--all-versions")

        bubble = self.query_one("#tmpl-bubble-sheet", Checkbox).value
        if bubble:
            argv.append("--bubble-sheet")
        roster = (
            self._clean_path(self.query_one("#tmpl-roster", Input).value)
            if (bubble or self.query_one("#tmpl-qr", Checkbox).value)
            else ""
        )
        if roster:
            argv.extend(["--roster", roster])
        elif self.query_one("#tmpl-qr", Checkbox).value and not bubble:
            argv.append("--qr")

        shuffle = bubble and self.query_one("#tmpl-shuffle", Checkbox).value
        if shuffle:
            raw_copies = self.query_one("#tmpl-copies", Input).value.strip()
            if raw_copies and not roster:
                try:
                    copies = int(raw_copies)
                except ValueError:
                    self.app.notify(
                        f"'{raw_copies}' is not a whole number of sheets.",
                        severity="error",
                    )
                    return
                if copies < 1:
                    self.app.notify("Number of sheets must be at least 1.", severity="error")
                    return
                argv.extend(["--copies", str(copies)])
            elif raw_copies and roster:
                self.app.notify(
                    "Give a roster or a number of sheets, not both.",
                    severity="error",
                )
                return
            elif not roster:
                self.app.notify(
                    "Scrambling needs a roster or a number of sheets — it "
                    "gives each sheet in a batch a different order.",
                    severity="error",
                )
                return
            argv.append("--shuffle")

        captured = io.StringIO()
        try:
            with contextlib.redirect_stdout(captured):
                run_command(qa_file_command, argv)
        except SystemExit as exc:
            self.app.notify(f"Template generation failed: {exc}", severity="error")
            return
        except Exception as e:
            self.app.notify(f"Template generation error: {e}", severity="error")
            return

        notes = [
            line.strip()
            for line in captured.getvalue().splitlines()
            if line.startswith("Note:")
        ]
        kind = "Bubble sheet" if bubble else "Template PDF"
        if notes:
            self.app.notify(
                f"{kind} written to {output_pdf}\n" + "\n".join(notes),
                severity="warning",
                timeout=15,
            )
        else:
            self.app.notify(f"{kind} written to {output_pdf}")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "policy-submissions":
            self._sync_policy_checkbox()
        elif event.input.id == "pipe-out":
            self._load_class_folder()
        elif event.input.id == "tui-class-folder":
            self._scan_launch_folder()
        elif event.input.id == "tui-qa-path":
            self._refresh_answers_button()

    def _sync_policy_checkbox(self) -> None:
        submissions = self._clean_path(
            self.query_one("#policy-submissions", Input).value
        )
        if not submissions or not Path(submissions).is_dir():
            return
        try:
            policy = read_policy(submissions)
        except Exception:
            return
        self.query_one("#policy-lock-anon", Checkbox).value = policy.anonymity_locked
        self.query_one("#policy-shuffle", Checkbox).value = policy.traversal_shuffle

    def _run_policy(self) -> None:
        submissions = self._clean_path(
            self.query_one("#policy-submissions", Input).value
        )
        if not submissions:
            self.app.notify(
                "A submissions directory is required.", severity="error"
            )
            return

        locked = self.query_one("#policy-lock-anon", Checkbox).value
        shuffle = self.query_one("#policy-shuffle", Checkbox).value
        policy = GradingPolicy(
            mode="multi" if locked else "solo",
            anonymity_locked=locked,
            traversal_shuffle=shuffle,
        )
        try:
            write_policy(submissions, policy)
        except Exception as e:
            self.app.notify(f"Could not write policy: {e}", severity="error")
            return

        state = (
            "locked to anonymous grading (multi-grader)"
            if locked
            else "unlocked — solo grading"
        )
        if shuffle:
            state += ", randomized student order per grader"
        self.app.notify(f"Grading policy set: {state}.")

    def _clean_path(self, path_str: str) -> str:
        p = path_str.strip()
        if not p:
            return ""
        p = p.strip("'\"")
        return str(Path(p.replace("\\ ", " ")).expanduser())
