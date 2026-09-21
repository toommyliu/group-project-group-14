from __future__ import annotations
import contextlib
import io
import os
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from dataclasses import replace
from typing import Literal

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.message import Message
from textual.widgets import (
    Button,
    Checkbox,
    Input,
    Label,
    ProgressBar,
    RadioButton,
    RadioSet,
    Static,
    TextArea,
    Collapsible,
)

from opengrader.core import ai_config
from opengrader.core.pipeline import (
    STAGE_EXTRACT,
    LlmOptions,
    PipelineProgress,
    PipelineReport,
    PipelineRequest,
    StageOutcome,
    StagePlan,
    StageStatus,
    plan_pipeline,
    run_pipeline,
)
from opengrader.core.policy import GradingPolicy, read_policy, write_policy
from opengrader.core.submissions import ExtractionProgress
from opengrader.core.submissions.watch import ExtractionWatcher
from opengrader.core.submissions.photo_intake import (
    PhotoIntakeError,
    PhotoIntakeReport,
    import_photos,
)
from opengrader.cli.submission_extractor import run_cli as run_extractor_cli
from opengrader.cli.submission_splitter import run_cli as run_splitter_cli
from opengrader.cli.answer_key import run_cli as run_answer_key_cli
from opengrader.cli.automated_grading import (
    DEFAULT_LLM_MIN_CONFIDENCE as _DEFAULT_LLM_MIN_CONFIDENCE,
    GradingProgress,
    run_cli as run_grading_cli,
)
from opengrader.cli.qa_file import run_cli as run_qa_file_cli
from opengrader.core.class_folder import (
    ClassFolderScan,
    describe_class_folder,
    scan_class_folder,
)
from opengrader.core.course import COURSE_FILENAME
from opengrader.tui.ui.screens.ai_settings_screen import AISettingsScreen
from opengrader.tui.ui.screens.backup_screen import BackupScreen
from opengrader.tui.ui.screens.doctor_screen import DoctorScreen
from opengrader.tui.ui.screens.course_grades_screen import CourseGradesScreen
from opengrader.tui.ui.screens.handoff_screen import GraderPacketScreen, MergeScreen
from opengrader.tui.ui.screens.llm_eval_screen import LlmEvalScreen
from opengrader.tui.ui.screens.provider_screen import ProviderScreen
from opengrader.tui.ui.screens.path_picker_screen import PathPickerScreen, PathPickMode
from opengrader.tui.ui.screens.region_map_screen import RegionMapScreen

_CPU_COUNT = os.cpu_count() or 1


_PIPE_STATUS_GLYPH = {
    StageStatus.RAN: "✓",
    StageStatus.REUSED: "↺",
    StageStatus.SKIPPED: "·",
    StageStatus.BLOCKED: "●",
    StageStatus.FAILED: "✗",
}


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


class SetupScreen(Vertical):
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

    SetupScreen .card-actions {
        width: 100%;
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    SetupScreen .path-row .browse-button {
        margin-top: 1;
    }

    SetupScreen #pipe-progress {
        height: auto;
    }

    SetupScreen #pipe-pages {
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

    SetupScreen #split-pages {
        width: 24;
    }

    SetupScreen #ext-parallel-options,
    SetupScreen #ext-refine-options {
        height: auto;
    }

    SetupScreen #ext-roster-area,
    SetupScreen #ext-one-section,
    SetupScreen #ext-many-section,
    SetupScreen #tmpl-bubble-note,
    SetupScreen #tmpl-batch-area,
    SetupScreen #tmpl-copies-area,
    SetupScreen #tmpl-roster-area {
        height: auto;
    }

    SetupScreen #ext-roster-mode {
        width: 100%;
        height: auto;
        margin-top: 1;
    }

    SetupScreen #ext-roster-list {
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
        "split-input": ("file", "Choose the scanned submissions PDF"),
        "split-output": ("directory", "Choose the split output directory"),
        "split-template-path": ("file", "Choose the blank template PDF"),
        "ext-input": ("directory", "Choose the directory of split PDFs"),
        "ext-output": ("directory", "Choose the extraction output directory"),
        "ext-template-path": ("file", "Choose the blank template PDF"),
        "ext-id-path": ("file", "Choose the roster/ID file"),
        "photo-input": ("any", "Choose the folder of photos (or one photo)"),
        "photo-output": ("directory", "Choose the directory for the per-student PDFs"),
        "region-pdf": ("file", "Choose the blank assignment PDF"),
        "region-map-path": ("any", "Choose an existing region map (.regions.json)"),
        "ext-region-map-path": ("file", "Choose the region map (.regions.json)"),
        "ak-input": ("file", "Choose the answer-key source (.md/.txt/.pdf)"),
        "ak-output": ("any", "Choose the output QA markdown file"),
        "ak-template-path": ("file", "Choose the blank template PDF"),
        "grade-solutions": ("file", "Choose the solutions markdown file"),
        "grade-input": ("any", "Choose the submissions file or directory"),
        "tmpl-qa-input": ("file", "Choose the QA markdown file"),
        "tmpl-output": ("any", "Choose the output template PDF"),
        "policy-submissions": ("directory", "Choose the submissions directory"),
        "handoff-submissions": ("directory", "Choose the submissions directory"),
        "backup-submissions": ("directory", "Choose the submissions directory"),
        "ext-qa": ("file", "Choose the QA markdown file"),
        "pipe-scan": ("any", "Choose the scanned bundle PDF, or a folder of split PDFs"),
        "pipe-qa": ("file", "Choose the answer-key markdown file"),
        "pipe-out": ("directory", "Choose the class folder"),
        "pipe-roster": ("file", "Choose the roster/ID file (.tsv)"),
        "pipe-template": ("file", "Choose the blank template PDF"),
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
            Button("Browse", id=f"browse-{input_id}", classes="browse-button", compact=True),
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
    def _ai() -> ai_config.ResolvedAI:
        return ai_config.resolve()

    def _auto_ollama_argv(self) -> list[str]:
        return ["--auto-ollama"] if self._ai().auto_start else []

    def _jobs_argv(self) -> list[str] | None:
        if not self.query_one("#ext-parallel", Checkbox).value:
            return []
        jobs = self.query_one("#ext-jobs", Input).value.strip()
        if not jobs:
            return []
        if not jobs.isdigit() or int(jobs) < 1:
            return None
        return ["--jobs", jobs]

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

                with Vertical(id="launch-section") as session_card:
                    # Choosing what to grade lives on Home (tui/home.py) now;
                    # this card only says what is open, and how to get back.
                    session_card.border_title = "▸ Grading"
                    yield Static("", id="setup-session", classes="card-hint", markup=False)

                yield Static(
                    "[b]RUN THE WHOLE PIPELINE[/b]", id="pipeline-heading",
                    classes="section-heading",
                )
                yield Static(
                    "Start here. One scanned bundle in, graded work out: the "
                    "scan is checked, split, extracted and auto-graded in "
                    "order, and you never hand the output of one step to the "
                    "next. Re-run it safely — a second run reuses whatever "
                    "still stands.",
                    id="pipeline-caption",
                    classes="section-caption",
                )

                with Collapsible(
                    title="Run every step — one scan in, graded work out",
                    id="section-pipeline",
                    collapsed=True,
                ):
                    yield Static(
                        "Choose the class folder and press Scan: the scanned "
                        "bundle, answer key, roster and blank template inside "
                        "it are filled in below, and split/, submissions/ and "
                        "the run record are built there too. Check what it "
                        "found, change anything it got wrong, then press Run. "
                        "Preview shows the plan without writing anything.",
                        classes="card-hint",
                    )
                    yield self._field_label(
                        "Class folder",
                        "holds the scan, answer key and roster; results are built here",
                    )
                    yield self._path_row("Path to the class folder…", "pipe-out")
                    yield self._card_actions(
                        Button("Scan folder", id="pipe-scan-folder", compact=True)
                    )
                    yield Static(
                        "",
                        id="pipe-folder-summary",
                        classes="card-hint hidden",
                        markup=False,
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
                        "Match student names against a roster", id="pipe-use-roster"
                    )
                    yield self._path_row(
                        "Path to roster file (.tsv)…", "pipe-roster", hidden=True
                    )

                    yield Checkbox(
                        "Align with a blank template PDF", id="pipe-use-template"
                    )
                    yield self._path_row(
                        "Path to blank template PDF…", "pipe-template", hidden=True
                    )

                    yield Static(
                        self._ai_status_text("pipe"),
                        id="pipe-ai-status",
                        classes="card-hint",
                    )

                    yield self._field_label(
                        "Pages per submission", "leave blank to auto-detect"
                    )
                    yield Input(
                        placeholder="e.g. 6 (blank = auto-detect)", id="pipe-pages"
                    )
                    yield Static(
                        "This is also what lets the scan pre-flight catch a "
                        "wrong page count or a swapped pair before anything is "
                        "graded — without it, that check can't run at all.",
                        classes="card-hint",
                    )

                    yield Checkbox(
                        "Start grading as submissions arrive",
                        id="pipe-grade-asap",
                        value=True,
                    )
                    yield Static(
                        "On: opens the grading workspace the moment the first "
                        "submission is ready, while the rest keep extracting "
                        "behind it — the same background extraction the "
                        "numbered Extract step offers. Off: waits for the "
                        "whole run to finish first. Either way, opening "
                        "automatically needs the answer key above.",
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

                    yield self._card_actions(
                        Button("Preview", id="pipe-preview", compact=True),
                        Button(
                            "Run Pipeline",
                            id="pipe-run",
                            variant="primary",
                            compact=True,
                        ),
                    )
                    yield Static("", id="pipe-preview-output", classes="card-hint hidden")
                    with Vertical(id="pipe-progress", classes="hidden"):
                        yield ProgressBar(
                            id="pipe-progress-bar", total=None, show_eta=True
                        )
                        yield Static("", id="pipe-progress-status", classes="card-hint")
                        yield Static("", id="pipe-stage-log", classes="card-hint")

                yield Static(
                    "[b]PREPARE AN ASSIGNMENT[/b]", id="steps-heading",
                    classes="section-heading",
                )
                yield Static(
                    "The same work as above, one stage at a time — for when "
                    "that is what you actually want: a class that arrives as "
                    "photos, a scan you split last week, an answer key still "
                    "to write, or a re-run of just one stage.",
                    id="steps-caption",
                    classes="section-caption",
                )

                with Collapsible(
                    title="1 · Split — one scanned bundle into per-student PDFs",
                    id="section-splitter",
                    collapsed=True,
                ):
                    yield self._field_label("Scanned bundle", "PDF from the bulk scanner")
                    yield self._path_row("Path to scanned PDF…", "split-input")
                    yield self._field_label("Output directory")
                    yield self._path_row("Where to write per-student PDFs…", "split-output")
                    yield self._field_label(
                        "Pages per submission", "leave blank to auto-detect by Name/ID"
                    )
                    yield Input(placeholder="e.g. 6 (blank = auto-detect)", id="split-pages")
                    yield Static(
                        "With no page count and no template, submissions are "
                        "detected by the student Name/ID header on each first page "
                        "(a cover page counts).",
                        classes="card-hint",
                    )
                    # Checkbox labels cannot wrap.
                    yield Checkbox(
                        "Check the scan before splitting",
                        id="split-scan-check",
                        value=True,
                    )
                    yield Static(
                        "Reads every page once before anything is cut up: turns "
                        "upside-down sheets upright, puts out-of-order pages back "
                        "in order, and reports blanks, low resolution and clipped "
                        "margins. Corrections go to a copy — your original scan is "
                        "never modified. Pages are re-sorted only where their page "
                        "numbers are certain (QR codes, a PDF text layer, or a "
                        "blank template); otherwise it reports what it saw and "
                        "leaves them alone, since a misread number would move a "
                        "page that was already right. Page order needs the page "
                        "count or template above to know how long a submission is.",
                        classes="card-hint",
                    )
                    yield Checkbox("Always reorder pages", id="split-reorder")
                    yield Static(
                        "Sorts every submission's pages whether or not the check "
                        "above found a problem. This is the answer to the case it "
                        "deliberately declines: page numbers legible enough to "
                        "sort by, but only by reading the scan. Order is taken "
                        "from the text layer first, then QR codes, then Tesseract "
                        "OCR, then the vision model.",
                        classes="card-hint",
                    )
                    yield Checkbox("Align with a blank template PDF", id="split-use-template")
                    yield self._path_row(
                        "Path to blank template PDF…", "split-template-path", hidden=True
                    )
                    yield Checkbox(
                        "Submissions have extra work pages",
                        id="split-extra-pages",
                    )
                    yield Static(
                        "Moves any page that can't be matched to the template "
                        "sequence — scratch work, 'continued on back' — to the end, "
                        "so extraction handles the graded pages first. They stay "
                        "reachable while grading with w on the grade screen.",
                        classes="card-hint",
                    )
                    yield Static(
                        self._ai_status_text("split"),
                        id="split-ai-status",
                        classes="card-hint",
                    )
                    yield self._card_actions(
                        Button("Run Splitter", id="run-splitter", variant="primary", compact=True)
                    )
                    with Vertical(id="split-progress", classes="hidden"):
                        yield Static("", id="split-progress-status", classes="card-hint")

                with Collapsible(
                    title="2 · Extract — read answers with OCR + vision model",
                    id="section-extractor",
                    collapsed=True,
                ):
                    yield self._field_label("Split PDFs", "directory from step 1")
                    yield self._path_row("Path to split PDFs directory…", "ext-input")
                    yield self._field_label("Output directory")
                    yield self._path_row("Where to write .submission.md files…", "ext-output")
                    yield Checkbox("Align with a blank template PDF", id="ext-use-template")
                    yield self._path_row(
                        "Path to blank template PDF…", "ext-template-path", hidden=True
                    )
                    yield Checkbox(
                        "Use a region map (your own assignment)",
                        id="ext-use-region-map",
                    )
                    with Vertical(id="ext-region-map-area", classes="hidden"):
                        yield self._path_row(
                            "Path to the region map (.regions.json)…",
                            "ext-region-map-path",
                        )
                        yield Static(
                            "For an assignment Opengrader didn't generate: the "
                            "boxes you drew on a blank copy are where the answers "
                            "are, so it needs no text layer of its own. Draw or fix "
                            "one with [b]Draw answer regions[/b] in Helper Tools "
                            "below. "
                            "This replaces the blank template above — they both "
                            "answer the same question, so only one is used.",
                            classes="card-hint",
                        )
                    yield Checkbox("Match student names against a roster", id="ext-use-id")
                    with Vertical(id="ext-roster-area", classes="hidden"):
                        yield Static(
                            "One assignment, multiple class sections? Choose how "
                            "these PDFs are organized so each student is tagged "
                            "with their section.",
                            classes="card-hint",
                        )
                        with RadioSet(id="ext-roster-mode"):
                            yield RadioButton(
                                "These PDFs are all one section",
                                value=True,
                                id="ext-mode-one",
                            )
                            yield RadioButton(
                                "These PDFs contain multiple sections (route by roster)",
                                id="ext-mode-many",
                            )
                        with Vertical(id="ext-one-section"):
                            yield self._path_row(
                                "Path to roster file (.tsv)…", "ext-id-path"
                            )
                            yield self._field_label(
                                "Section name", "optional — defaults to the roster filename"
                            )
                            yield Input(
                                placeholder="e.g. SectionA (leave blank to use the filename)",
                                id="ext-section-name",
                                classes="path-input",
                            )
                        with Vertical(id="ext-many-section", classes="hidden"):
                            yield Static(
                                "One roster per line, in the form "
                                "[b]path/to/roster.tsv=SectionName[/b]. The "
                                "'=SectionName' is optional — omit it to name the "
                                "section after the roster filename. Use Browse to "
                                "add a roster, then type '=SectionName' after it if "
                                "you want to override the name. Each student is "
                                "routed to the section whose roster holds their ID; "
                                "anyone in no roster is tagged 'unassigned'.",
                                classes="card-hint",
                            )
                            yield TextArea(id="ext-roster-list")
                            yield self._card_actions(
                                Button(
                                    "Browse & add roster",
                                    id="browse-ext-roster-list",
                                    classes="browse-button",
                                    compact=True,
                                )
                            )
                    yield Static(
                        self._ai_status_text("ext"),
                        id="ext-ai-status",
                        classes="card-hint",
                    )
                    yield Checkbox(
                        "Set how many to extract at once",
                        id="ext-parallel",
                    )
                    yield Static(
                        "Extraction already runs on several CPU cores at once, "
                        "which is typically several times faster on a full class "
                        "stack. Left unchecked, Opengrader picks a safe number "
                        "for this machine — tick this only to override it.",
                        classes="card-hint",
                    )
                    with Vertical(id="ext-parallel-options", classes="hidden"):
                        yield self._field_label(
                            "Parallel jobs",
                            f"blank = automatic (this machine has {_CPU_COUNT} cores)",
                        )
                        yield Input(
                            placeholder=f"1–{_CPU_COUNT}",
                            id="ext-jobs",
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
                        "Resume: skip submissions already extracted",
                        id="ext-resume",
                    )
                    yield Static(
                        "Re-uses an earlier run's work when the scan, the settings, "
                        "and the output files are all unchanged, so an interrupted "
                        "run picks up where it stopped instead of starting over. "
                        "Grading already entered is never re-extracted over.",
                        classes="card-hint",
                    )
                    yield Checkbox(
                        "Re-read only the unclear answers",
                        id="ext-refine",
                    )
                    yield Static(
                        "A second pass over an extraction you already have: only "
                        "the submissions holding a low-confidence transcription "
                        "are re-read, and the new reading is kept only where it "
                        "beats the old one. Grades, comments and every answer "
                        "that was already clear are left alone. Give it something "
                        "the first pass didn't have — a higher DPI is the cheapest "
                        "thing that helps.",
                        classes="card-hint",
                    )
                    with Vertical(id="ext-refine-options", classes="hidden"):
                        yield self._field_label(
                            "Re-read at DPI",
                            "blank = the same resolution as the first pass",
                        )
                        yield Input(
                            placeholder="e.g. 450",
                            id="ext-refine-dpi",
                            classes="path-input",
                        )
                    yield Checkbox(
                        "Lock anonymous grading for TAs",
                        id="ext-lock-anon",
                    )
                    yield Static(
                        "Graders opening this folder can't reveal names or view "
                        "grade history. You can change this later under "
                        "\"Set grading policy\".",
                        classes="card-hint",
                    )
                    yield self._card_actions(
                        Button("Run Extractor", id="run-extractor", variant="primary", compact=True)
                    )
                    with Vertical(id="ext-progress", classes="hidden"):
                        yield ProgressBar(
                            id="ext-progress-bar", total=None, show_eta=True
                        )
                        yield Static("", id="ext-progress-status", classes="card-hint")
                        yield Static(
                            "The first submission is gradeable the moment it "
                            "lands — no need to wait for the whole batch.",
                            classes="card-hint",
                        )
                        yield self._field_label(
                            "Answer key", "needed to grade while extraction runs"
                        )
                        yield self._path_row("Path to QA markdown file…", "ext-qa")
                        yield self._card_actions(
                            Button(
                                "Grade what's ready →",
                                id="grade-while-extracting",
                                variant="success",
                                compact=True,
                            )
                        )

                with Collapsible(
                    title="3 · Create answer key — from a plain-text assignment or a scanned PDF",
                    id="section-answer-key",
                    collapsed=True,
                ):
                    yield self._field_label(
                        "Answer-key source",
                        "a plain-text assignment (.md/.txt) or a scanned answer-key PDF",
                    )
                    yield self._path_row("Path to answer-key source…", "ak-input")
                    yield self._field_label("Output QA markdown")
                    yield self._path_row("Where to write the answer key…", "ak-output")
                    yield self._field_label("Title", "optional")
                    yield Input(placeholder="e.g. CS 146 Quiz 1", id="ak-title")
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
                        "Plain-text files are read exactly. A born-digital PDF is "
                        "read directly from its text layer (fast, exact); a scanned "
                        "or image PDF is read with OCR, and with the handwriting "
                        "model from [b]AI setup[/b] when it is turned on, into a "
                        "draft to review. New to the format? "
                        "Press [b]t[/b] for copy-paste templates.",
                        classes="card-hint",
                    )
                    yield self._card_actions(
                        Button("Create answer key", id="run-answer-key", variant="primary", compact=True)
                    )
                    with Vertical(id="ak-progress", classes="hidden"):
                        yield ProgressBar(
                            id="ak-progress-bar", total=None, show_eta=True
                        )
                        yield Static("", id="ak-progress-status", classes="card-hint")

                with Collapsible(
                    title="4 · Auto-grade — score exact matches before manual review",
                    id="section-grading",
                    collapsed=True,
                ):
                    yield self._field_label("Solutions", "QA markdown with correct answers")
                    yield self._path_row("Path to solutions markdown…", "grade-solutions")
                    yield self._field_label("Submissions", "directory from step 2")
                    yield self._path_row("Path to submissions directory…", "grade-input")
                    yield Static(
                        self._ai_status_text("grade"),
                        id="grade-ai-status",
                        classes="card-hint",
                    )
                    yield self._card_actions(
                        Button("Run Auto-Grader", id="run-grading", variant="primary", compact=True)
                    )
                    with Vertical(id="autograde-progress", classes="hidden"):
                        yield ProgressBar(
                            id="autograde-progress-bar", total=None, show_eta=True
                        )
                        yield Static("", id="autograde-progress-status", classes="card-hint")

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
                        "refusing to start. It changes nothing.",
                        classes="card-hint",
                    )
                    yield self._card_actions(
                        Button(
                            "Check this computer",
                            id="run-doctor",
                            variant="primary",
                            compact=True,
                        )
                    )

                with Collapsible(
                    title="AI setup — choose the models, once, for everything",
                    id="section-ai",
                    collapsed=True,
                ):
                    yield Static(
                        "Opengrader can use AI for two jobs: reading a "
                        "student's handwriting, and suggesting grades for "
                        "written answers against your rubric. Both are "
                        "optional — everything works without them — and both "
                        "are set up here rather than in each step, because "
                        "which model you have installed is a fact about this "
                        "computer, not about today's assignment.",
                        classes="card-hint",
                    )
                    yield Static(
                        "What you save here applies everywhere: the steps "
                        "above, the one-shot run in step 0, and the same "
                        "commands run from a terminal. Test connection says "
                        "whether the models are actually there before a long "
                        "run finds out for you.",
                        classes="card-hint",
                    )
                    yield Static(
                        self._ai_summary_text(),
                        id="ai-current-summary",
                        classes="card-hint",
                    )
                    yield self._card_actions(
                        Button(
                            "Open AI setup",
                            id="run-ai-settings",
                            variant="primary",
                            compact=True,
                        )
                    )

                with Collapsible(
                    title="AI providers — connect a service your department runs",
                    id="section-providers",
                    collapsed=True,
                ):
                    yield Static(
                        "Opengrader's AI runs on this computer unless you add "
                        "another service here: a small file naming where the "
                        "service is, which models to ask for, and which "
                        "environment variable holds your key. The key itself "
                        "is never written down. Once added, a provider appears "
                        "in AI setup, where you choose it.",
                        classes="card-hint",
                    )
                    yield Static(
                        "A service that is not on this computer or your local "
                        "network receives student work: answer images and "
                        "handwriting. Check with your institution before you "
                        "choose one.",
                        classes="card-hint",
                    )
                    yield self._card_actions(
                        Button(
                            "Manage providers",
                            id="run-providers",
                            variant="primary",
                            compact=True,
                        )
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
                        "What comes out is what step 1 above produces, so carry "
                        "on at [b]2 · Extract[/b] with this output directory as "
                        "its input. iPhone photos in HEIC format need one extra "
                        "install; if yours are, the exact command is named.",
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
                            variant="primary",
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
                            variant="primary",
                            compact=True,
                        )
                    )

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
                        Button("Generate Template", id="run-template", variant="primary", compact=True)
                    )

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
                            variant="primary",
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
                            variant="primary",
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
                        Button("Apply Policy", id="run-policy", variant="primary", compact=True)
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
                        "with a code and leaves the scanned exams behind, so "
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
                            variant="primary",
                            compact=True,
                        )
                        yield Button(
                            "Merge a copy back…", id="run-handoff-merge", compact=True
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
                            variant="primary",
                            compact=True,
                        )
                    )

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "split-use-template":
            self.query_one("#split-template-path-row").set_class(not event.value, "hidden")
        elif event.checkbox.id == "ext-use-template":
            self.query_one("#ext-template-path-row").set_class(not event.value, "hidden")
            if event.value:
                self.query_one("#ext-use-region-map", Checkbox).value = False
        elif event.checkbox.id == "ext-use-region-map":
            self.query_one("#ext-region-map-area").set_class(not event.value, "hidden")
            if event.value:
                self.query_one("#ext-use-template", Checkbox).value = False
        elif event.checkbox.id == "ext-use-id":
            self.query_one("#ext-roster-area").set_class(not event.value, "hidden")
        elif event.checkbox.id in ("tmpl-qr", "tmpl-bubble-sheet", "tmpl-shuffle"):
            self._sync_template_options()
        elif event.checkbox.id == "ak-use-template":
            self.query_one("#ak-template-path-row").set_class(not event.value, "hidden")
        elif event.checkbox.id == "ext-parallel":
            self.query_one("#ext-parallel-options").set_class(not event.value, "hidden")
        elif event.checkbox.id == "ext-refine":
            self.query_one("#ext-refine-options").set_class(not event.value, "hidden")
            if event.value:
                self.query_one("#ext-resume", Checkbox).value = False
        elif event.checkbox.id == "ext-resume" and event.value:
            self.query_one("#ext-refine", Checkbox).value = False
        elif event.checkbox.id == "pipe-use-roster":
            self.query_one("#pipe-roster-row").set_class(not event.value, "hidden")
        elif event.checkbox.id == "pipe-use-template":
            self.query_one("#pipe-template-row").set_class(not event.value, "hidden")

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id == "ext-roster-mode":
            one_section = event.pressed.id == "ext-mode-one"
            self.query_one("#ext-one-section").set_class(not one_section, "hidden")
            self.query_one("#ext-many-section").set_class(one_section, "hidden")

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
                self._scan_class_folder()

        self.app.push_screen(
            PathPickerScreen(title=title, mode=mode, start_path=start_path),
            _apply,
        )

    def _append_roster_path(self) -> None:
        text_area = self.query_one("#ext-roster-list", TextArea)

        def _apply(path: Path | None) -> None:
            if path is None:
                return
            existing = text_area.text
            if existing and not existing.endswith("\n"):
                existing += "\n"
            text_area.text = f"{existing}{path}\n"

        self.app.push_screen(
            PathPickerScreen(title="Choose a roster file (.tsv)", mode="file"),
            _apply,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "browse-ext-roster-list":
            event.stop()
            self._append_roster_path()
            return
        if button_id.startswith("browse-"):
            input_id = button_id.removeprefix("browse-")
            if input_id in self._PATH_FIELDS:
                event.stop()
                self._open_path_picker(input_id)
                return
        if event.button.id == "run-splitter":
            self._run_splitter()
        elif event.button.id == "run-extractor":
            self._run_extractor()
        elif event.button.id == "grade-while-extracting":
            self._open_grade_while_extracting()
        elif event.button.id == "run-answer-key":
            self._run_answer_key()
        elif event.button.id == "run-grading":
            self._run_grading()
        elif event.button.id == "run-template":
            self._run_template()
        elif event.button.id == "run-llm-eval":
            self._open_llm_eval()
        elif event.button.id == "run-course-grades":
            self._open_course_grades()
        elif event.button.id == "run-policy":
            self._run_policy()
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
        elif event.button.id == "pipe-scan-folder":
            self._scan_class_folder()

    def _handoff_submissions(self, input_id: str) -> str:
        own = self._clean_path(self.query_one(f"#{input_id}", Input).value)
        if own:
            return own
        return self._session_paths()[1]

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
            self.query_one("#ext-region-map-path", Input).value = str(saved)
            self.query_one("#ext-use-region-map", Checkbox).value = True

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

        if step == "split":
            if current.read_handwriting:
                body = (
                    "AI: hard-to-read page numbers will be read with the "
                    "handwriting model."
                )
            else:
                body = (
                    "AI: off — page numbers are read from the text layer, QR "
                    "codes and text recognition only."
                )
        elif step == "ext":
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
        elif step == "grade":
            if current.grade_written:
                calibration = (
                    "a measured confidence is required"
                    if current.require_calibration
                    else "no measurement is required"
                )
                body = (
                    f"AI: {current.text_model} suggests grades for written "
                    f"answers at {current.min_confidence:g}+ confidence; "
                    f"{calibration}."
                )
            else:
                body = (
                    "AI: off — only answers your rubric can settle outright "
                    "are scored."
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
                else "auto-grade does not run automatically — run it "
                "yourself when you're ready"
            )
            body = f"AI: {handwriting}, {grading}, and {auto_grade}."
        else:
            raise ValueError(f"unknown AI status step {step!r}")

        return f"{body} {change_here}"

    def _refresh_ai_displays(self) -> None:
        self.query_one("#ai-current-summary", Static).update(self._ai_summary_text())
        self.query_one("#split-ai-status", Static).update(self._ai_status_text("split"))
        self.query_one("#ext-ai-status", Static).update(self._ai_status_text("ext"))
        self.query_one("#ak-ai-status", Static).update(self._ai_status_text("ak"))
        self.query_one("#grade-ai-status", Static).update(self._ai_status_text("grade"))
        self.query_one("#pipe-ai-status", Static).update(self._ai_status_text("pipe"))

    def _open_ai_settings(self) -> None:
        def _refresh(saved: ai_config.AISettings | None) -> None:
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
        solutions = self._clean_path(self.query_one("#grade-solutions", Input).value)
        submissions = self._clean_path(self.query_one("#grade-input", Input).value)

        def _apply(recommended: float | None) -> None:
            if recommended is None:
                return
            settings = replace(
                ai_config.load_settings(),
                min_confidence=round(recommended, 2),
                min_confidence_measured=True,
                grade_written=True,
            )
            try:
                ai_config.save_settings(settings)
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

    def _run_splitter(self) -> None:
        input_pdf = self._clean_path(self.query_one("#split-input", Input).value)
        output_dir = self._clean_path(self.query_one("#split-output", Input).value)
        pages = self.query_one("#split-pages", Input).value
        if not input_pdf or not output_dir:
            self.app.notify("Input PDF and output directory are required.", severity="error")
            return

        use_template = self.query_one("#split-use-template", Checkbox).value
        template = self._clean_path(self.query_one("#split-template-path", Input).value)
        if use_template and not template:
            self.app.notify("Choose a blank template PDF, or uncheck the template option.", severity="error")
            return

        argv = ["--input", input_pdf, "--output", output_dir]
        if pages.strip():
            argv.extend(["--pages-per-submission", pages.strip()])

        if use_template and template:
            argv.extend(["--blank-template", template])
        if self.query_one("#split-scan-check", Checkbox).value:
            argv.extend(["--fix-rotation", "--fix-order"])
        if self.query_one("#split-reorder", Checkbox).value:
            argv.append("--reorder")
        if self.query_one("#split-extra-pages", Checkbox).value:
            argv.append("--extra-work-pages")
        argv.extend(self._auto_ollama_argv())

        self.query_one("#split-progress-status", Static).update("Splitting…")
        self.query_one("#split-progress").remove_class("hidden")
        self.query_one("#run-splitter", Button).disabled = True
        self._splitter_worker(argv)

    @work(thread=True, exclusive=True, group="splitter")
    def _splitter_worker(self, argv: list[str]) -> None:
        app = self.app

        def on_status(message: str) -> None:
            app.call_from_thread(self._split_set_status, message)

        try:
            run_splitter_cli(argv, status_callback=on_status)
        except SystemExit as exc:
            app.call_from_thread(self._split_finish, f"Splitter failed (exit {exc.code}).", True)
            return
        except Exception as exc:
            app.call_from_thread(self._split_finish, f"Splitter error: {exc}", True)
            return
        app.call_from_thread(self._split_finish, "Splitting complete.", False)

    def _split_set_status(self, message: str) -> None:
        self.query_one("#split-progress-status", Static).update(message.strip())

    def _split_finish(self, message: str, is_error: bool) -> None:
        self.query_one("#run-splitter", Button).disabled = False
        self.query_one("#split-progress").add_class("hidden")
        self.app.notify(message, severity="error" if is_error else "information")

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
            extractor_input = self.query_one("#ext-input", Input)
            if not extractor_input.value.strip():
                extractor_input.value = str(report.output_dir)

    def _roster_argv(self) -> list[str]:
        multi = self.query_one("#ext-mode-many", RadioButton).value
        if multi:
            raw = self.query_one("#ext-roster-list", TextArea).text
            args: list[str] = []
            for line in raw.splitlines():
                entry = line.strip()
                if entry:
                    args.extend(["--id-file", entry])
            return args

        roster = self._clean_path(self.query_one("#ext-id-path", Input).value)
        name = self.query_one("#ext-section-name", Input).value.strip()
        if roster:
            section = name or Path(roster).stem
            return ["--id-file", f"{roster}={section}"]
        if name:
            return ["--section", name]
        return []

    def _run_extractor(self) -> None:
        input_dir = self._clean_path(self.query_one("#ext-input", Input).value)
        output_dir = self._clean_path(self.query_one("#ext-output", Input).value)
        if not input_dir or not output_dir:
            self.app.notify("Both input and output directories are required.", severity="error")
            return

        argv = ["--input", input_dir, "--output", output_dir]

        if self.query_one("#ext-use-template", Checkbox).value:
            template = self._clean_path(self.query_one("#ext-template-path", Input).value)
            if template:
                argv.extend(["--blank-template", template])

        if self.query_one("#ext-use-region-map", Checkbox).value:
            region_map = self._clean_path(
                self.query_one("#ext-region-map-path", Input).value
            )
            if not region_map:
                self.app.notify(
                    "Choose the region map, or untick "
                    '"Use a region map (your own assignment)".',
                    severity="error",
                )
                return
            argv.extend(["--region-map", region_map])

        if self.query_one("#ext-use-id", Checkbox).value:
            argv.extend(self._roster_argv())


        jobs_argv = self._jobs_argv()
        if jobs_argv is None:
            self.app.notify(
                f"Parallel jobs must be a whole number from 1 to {_CPU_COUNT} "
                "(or blank to choose automatically).",
                severity="error",
            )
            return
        argv.extend(jobs_argv)

        if self.query_one("#ext-resume", Checkbox).value:
            argv.append("--resume")

        if self.query_one("#ext-refine", Checkbox).value:
            argv.append("--refine")
            refine_dpi = self.query_one("#ext-refine-dpi", Input).value.strip()
            if refine_dpi:
                if not refine_dpi.isdigit() or int(refine_dpi) < 1:
                    self.app.notify(
                        "Re-read DPI must be a whole number (or blank to reuse "
                        "the first pass's resolution).",
                        severity="error",
                    )
                    return
                argv.extend(["--refine-dpi", refine_dpi])

        argv.extend(self._auto_ollama_argv())

        lock_anon = self.query_one("#ext-lock-anon", Checkbox).value

        self._ext_total = 0
        bar = self.query_one("#ext-progress-bar", ProgressBar)
        bar.update(total=None, progress=0)
        self.query_one("#ext-progress-status", Static).update("Starting…")
        self.query_one("#ext-progress").remove_class("hidden")
        self.query_one("#run-extractor", Button).disabled = True
        self.extraction_in_progress = True

        self._extractor_worker(argv, output_dir, lock_anon)

    @work(thread=True, exclusive=True, group="extractor")
    def _extractor_worker(
        self, argv: list[str], output_dir: str, lock_anon: bool
    ) -> None:
        app = self.app

        def on_file_start(_pdf_name: str, count: int) -> None:
            app.call_from_thread(self._ext_add_total, count)

        def on_response(pdf_name: str, question_id: str) -> None:
            app.call_from_thread(self._ext_advance, pdf_name, question_id)

        def on_status(message: str) -> None:
            app.call_from_thread(self._ext_set_status, message)

        progress = ExtractionProgress(on_file_start=on_file_start, on_response=on_response)
        try:
            run_extractor_cli(argv, progress=progress, status_callback=on_status)
            if lock_anon:
                write_policy(output_dir, GradingPolicy(mode="multi", anonymity_locked=True))
        except Exception as exc:
            app.call_from_thread(self._ext_finish, f"Extractor error: {exc}", True)
            return
        app.call_from_thread(self._ext_finish, "Extraction complete.", False)

    def _ext_add_total(self, count: int) -> None:
        self._ext_total = getattr(self, "_ext_total", 0) + count
        self.query_one("#ext-progress-bar", ProgressBar).update(total=self._ext_total)

    def _ext_advance(self, pdf_name: str, question_id: str) -> None:
        self.query_one("#ext-progress-bar", ProgressBar).advance(1)
        self.query_one("#ext-progress-status", Static).update(
            f"Transcribing {pdf_name} · {question_id}"
        )

    def _ext_set_status(self, message: str) -> None:
        self.query_one("#ext-progress-status", Static).update(message.strip())

    def _ext_finish(self, message: str, is_error: bool) -> None:
        self.query_one("#run-extractor", Button).disabled = False
        self.query_one("#ext-progress").add_class("hidden")
        self.extraction_in_progress = False
        self.app.notify(message, severity="error" if is_error else "information")

    def _open_grade_while_extracting(self) -> None:
        qa_path = self._clean_path(self.query_one("#ext-qa", Input).value)
        qa_path = qa_path or self._session_paths()[0]
        output_dir = self._clean_path(self.query_one("#ext-output", Input).value)
        if not qa_path:
            self.app.notify(
                "Set the answer key below the progress bar first.",
                severity="error",
            )
            return
        if not output_dir:
            self.app.notify(
                "Set the extraction output directory first.", severity="error"
            )
            return
        self.post_message(self.GradeWhileExtractingRequested(qa_path, output_dir))


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

        summary = self.query_one("#pipe-folder-summary", Static)
        summary.update("\n".join(describe_class_folder(found)))
        summary.remove_class("hidden")
        return found

    def show_session(self, text: str) -> None:
        """What is open for grading, as the launcher and TuiApp see it."""
        self.query_one("#setup-session", Static).update(text)

    def open_pipeline_folder(self, folder: str | Path) -> None:
        """Arrive from Home on a folder of new scans: pipeline card filled in."""
        self.query_one("#pipe-out", Input).value = str(folder)
        section = self.query_one("#section-pipeline", Collapsible)
        section.collapsed = False
        self._scan_class_folder()
        self.call_after_refresh(section.scroll_visible)

    def _session_paths(self) -> tuple[str, str]:
        """(answer key, submissions) of the folder being graded, if any.

        Tools that act on "the class" default to whatever is open, the way
        the Start grading card's fields used to be their fallback.
        """
        provider = getattr(self.app, "data_provider", None)
        if not getattr(self.app.workflow_state, "session_open", False):
            return "", ""
        qa = getattr(provider, "qa_file_path", None)
        subs = getattr(provider, "submissions_path", None)
        return (str(qa) if qa else "", str(subs) if subs else "")

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
        if not scan and out and Path(out).is_dir():
            self._scan_class_folder()
            scan = self._clean_path(self.query_one("#pipe-scan", Input).value)
        if not scan or not out:
            self.app.notify(
                "The scanned bundle and the class folder are both required.",
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
            auto_ollama=current.auto_start,
        )

        return PipelineRequest(
            scan=Path(scan),
            output_dir=Path(out),
            qa_file=Path(qa) if qa else None,
            roster=roster,
            blank_template=template,
            pages_per_submission=pages,
            llm=llm,
            auto_grade=current.auto_grade,
            force=self.query_one("#pipe-force", Checkbox).value,
        )

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
        app = self.app
        try:
            plans = plan_pipeline(request)
        except Exception as exc:
            app.call_from_thread(
                self._pipe_preview_finish, None, request.output_dir, str(exc)
            )
            return
        app.call_from_thread(self._pipe_preview_finish, plans, request.output_dir, None)

    def _pipe_preview_finish(
        self,
        plans: tuple[StagePlan, ...] | None,
        output_dir: Path,
        error: str | None,
    ) -> None:
        self.query_one("#pipe-run", Button).disabled = False
        self.query_one("#pipe-preview", Button).disabled = False
        if error is not None:
            self.app.notify(f"Could not build a plan: {error}", severity="error")
            return
        lines = ["Plan (nothing has been run or written):", ""]
        for plan in plans:
            verb = "run" if plan.will_run else plan.status.value
            lines.append(f"{plan.stage:<12} {verb:<8} {plan.reason}")
            if plan.command:
                lines.append(f"             $ {plan.command}")
        lines.append("")
        lines.append(f"class folder: {output_dir}")
        output = self.query_one("#pipe-preview-output", Static)
        output.update("\n".join(lines))
        output.remove_class("hidden")

    def _run_pipeline(self) -> None:
        request = self._build_pipeline_request()
        if request is None:
            return

        asap = self.query_one("#pipe-grade-asap", Checkbox).value
        qa_path = str(request.qa_file) if request.qa_file is not None else ""
        if asap and not qa_path:
            self.app.notify(
                "No answer key set — grading will open once the whole run "
                "finishes instead of as submissions arrive. Add an answer "
                "key above to grade sooner.",
            )

        self._pipe_log_lines: list[str] = []
        self._pipe_total = 0
        self.query_one("#pipe-preview-output").add_class("hidden")
        bar = self.query_one("#pipe-progress-bar", ProgressBar)
        bar.update(total=None, progress=0)
        self.query_one("#pipe-progress-status", Static).update("Starting…")
        self.query_one("#pipe-stage-log", Static).update("")
        self.query_one("#pipe-progress").remove_class("hidden")
        self.query_one("#pipe-run", Button).disabled = True
        self.query_one("#pipe-preview", Button).disabled = True
        self.extraction_in_progress = True

        self._pipeline_worker(request, asap, qa_path)

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

        app.call_from_thread(self._pipe_finish, report, state["launched_early"], qa_path)

    def _pipe_stage_start(self, stage: str) -> None:
        self.query_one("#pipe-progress-status", Static).update(f"▸ {stage}…")

    def _pipe_stage_finish(self, outcome: StageOutcome) -> None:
        glyph = _PIPE_STATUS_GLYPH.get(outcome.status, "·")
        self._pipe_log_lines.append(f"{glyph} {outcome.stage}: {outcome.message}")
        if outcome.status in (StageStatus.BLOCKED, StageStatus.FAILED) and outcome.detail:
            for line in outcome.detail.splitlines():
                self._pipe_log_lines.append(f"    {line}")
        self.query_one("#pipe-stage-log", Static).update("\n".join(self._pipe_log_lines))

    def _pipe_set_status(self, message: str) -> None:
        self.query_one("#pipe-progress-status", Static).update(message.strip())

    def _pipe_file_start(self, name: str, count: int) -> None:
        self._pipe_total = getattr(self, "_pipe_total", 0) + count
        self.query_one("#pipe-progress-bar", ProgressBar).update(total=self._pipe_total)
        self.query_one("#pipe-progress-status", Static).update(f"{name} ({count})")

    def _pipe_response(self, name: str, item: str) -> None:
        self.query_one("#pipe-progress-bar", ProgressBar).advance(1)
        self.query_one("#pipe-progress-status", Static).update(f"{name} · {item}")

    def _pipe_launch_early(self, qa_path: str, submissions_path: str) -> None:
        self.app.notify(
            "Submissions are ready — opening the grading workspace while the "
            "rest of the pipeline keeps running in the background."
        )
        self.post_message(self.GradeWhileExtractingRequested(qa_path, submissions_path))

    def _pipe_reset_buttons(self) -> None:
        self.query_one("#pipe-run", Button).disabled = False
        self.query_one("#pipe-preview", Button).disabled = False
        self.extraction_in_progress = False

    def _pipe_finish_error(self, message: str) -> None:
        self._pipe_reset_buttons()
        self.app.notify(message, severity="error")

    def _pipe_finish(
        self, report: PipelineReport, launched_early: bool, qa_path: str
    ) -> None:
        self._pipe_reset_buttons()

        blocked = next(
            (o for o in report.stages if o.status is StageStatus.BLOCKED), None
        )
        if blocked is not None:
            self.query_one("#pipe-progress-status", Static).update(
                f"Stopped: {blocked.message}"
            )
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
            self.query_one("#pipe-progress-status", Static).update(f"Failed: {message}")
            self.app.notify(f"Pipeline failed: {message}", severity="error")
            return

        if launched_early:
            self.app.notify(
                f"Pipeline finished — {report.submission_count} submission(s) "
                f"in {report.submissions_dir}."
            )
            return

        if report.ready_to_grade and qa_path:
            self.app.notify(
                f"{report.submission_count} submission(s) ready — opening the "
                "grading workspace."
            )
            self.post_message(self.LaunchRequested(qa_path, str(report.submissions_dir)))
            return

        if report.ready_to_grade:
            self.app.notify(
                f"{report.submission_count} submission(s) ready in "
                f"{report.submissions_dir}. Add an answer key above, or open "
                "open them from Home (F2)."
            )
            return

        self.app.notify(
            "Pipeline finished, but no submissions were produced.",
            severity="warning",
        )

    def _run_answer_key(self) -> None:
        input_file = self._clean_path(self.query_one("#ak-input", Input).value)
        output_md = self._clean_path(self.query_one("#ak-output", Input).value)
        if not input_file or not output_md:
            self.app.notify("Input file and output path are required.", severity="error")
            return

        argv = ["--input", input_file, "--output", output_md]
        title = self.query_one("#ak-title", Input).value.strip()
        if title:
            argv.extend(["--title", title])
        if self.query_one("#ak-use-template", Checkbox).value:
            template = self._clean_path(self.query_one("#ak-template-path", Input).value)
            if template:
                argv.extend(["--blank-template", template])
        argv.extend(self._auto_ollama_argv())

        self._ak_total = 0
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

        try:
            run_answer_key_cli(
                argv, status_callback=on_status, progress_callback=on_progress
            )
        except SystemExit as exc:
            app.call_from_thread(self._ak_finish, f"Answer key failed (exit {exc.code}).", True)
            return
        except Exception as exc:
            app.call_from_thread(self._ak_finish, f"Answer key error: {exc}", True)
            return
        app.call_from_thread(self._ak_finish, "Answer key created.", False)

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

    def _ak_finish(self, message: str, is_error: bool) -> None:
        self.query_one("#run-answer-key", Button).disabled = False
        self.query_one("#ak-progress").add_class("hidden")
        self.app.notify(message, severity="error" if is_error else "information")

    def _run_grading(self) -> None:
        solutions = self._clean_path(self.query_one("#grade-solutions", Input).value)
        input_dir = self._clean_path(self.query_one("#grade-input", Input).value)

        if not solutions or not input_dir:
            self.app.notify("Solutions file and Student Submissions directory are required.", severity="error")
            return

        argv = ["--solutions", solutions, "--input", input_dir]


        self._grade_prefix = ""
        bar = self.query_one("#autograde-progress-bar", ProgressBar)
        bar.update(total=None, progress=0)
        self.query_one("#autograde-progress-status", Static).update("Starting…")
        self.query_one("#autograde-progress").remove_class("hidden")
        self.query_one("#run-grading", Button).disabled = True
        self._grading_worker(argv, probe_llm=self._ai().grade_written)

    @work(thread=True, exclusive=True, group="grading")
    def _grading_worker(self, argv: list[str], *, probe_llm: bool = False) -> None:
        app = self.app

        if probe_llm:
            try:
                ai_config.ensure_ready(purpose="text")
            except ai_config.AIUnavailableError:
                argv = [*argv, *self._auto_ollama_argv()]

        def on_file_start(file_name: str, index: int, total: int) -> None:
            app.call_from_thread(self._grade_file_start, file_name, index, total)

        def on_question(_file_name: str, question_id: str) -> None:
            app.call_from_thread(self._grade_question, question_id)

        def on_status(message: str) -> None:
            app.call_from_thread(self._grade_set_status, message)

        progress = GradingProgress(
            on_file_start=on_file_start, on_question=on_question
        )
        try:
            run_grading_cli(argv, progress=progress, status_callback=on_status)
        except SystemExit as exc:
            app.call_from_thread(
                self._grade_finish, f"Grading failed (exit {exc.code}).", True
            )
            return
        except Exception as exc:
            app.call_from_thread(self._grade_finish, f"Grading error: {exc}", True)
            return
        app.call_from_thread(self._grade_finish, "Automated grading complete.", False)

    def _grade_file_start(self, file_name: str, index: int, total: int) -> None:
        bar = self.query_one("#autograde-progress-bar", ProgressBar)
        bar.update(total=total, progress=index - 1)
        self._grade_prefix = f"{index}/{total} · {file_name}"
        self.query_one("#autograde-progress-status", Static).update(
            f"Grading {self._grade_prefix}"
        )

    def _grade_question(self, question_id: str) -> None:
        prefix = getattr(self, "_grade_prefix", "")
        text = (
            f"Grading {prefix} · {question_id}"
            if prefix
            else f"Grading · {question_id}"
        )
        self.query_one("#autograde-progress-status", Static).update(text)

    def _grade_set_status(self, message: str) -> None:
        self.query_one("#autograde-progress-status", Static).update(message.strip())

    def _grade_finish(self, message: str, is_error: bool) -> None:
        if not is_error:
            bar = self.query_one("#autograde-progress-bar", ProgressBar)
            if bar.total is not None:
                bar.update(progress=bar.total)
        self.query_one("#run-grading", Button).disabled = False
        self.query_one("#autograde-progress").add_class("hidden")
        self.app.notify(message, severity="error" if is_error else "information")

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
                run_qa_file_cli(argv)
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
            self._scan_class_folder()

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
