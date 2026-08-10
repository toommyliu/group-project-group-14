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
from opengrader.core.llm import OllamaError, ensure_model_available
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
from opengrader.core.course import COURSE_FILENAME
from opengrader.tui.ui.screens.ai_settings_screen import AISettingsScreen
from opengrader.tui.ui.screens.backup_screen import BackupScreen
from opengrader.tui.ui.screens.doctor_screen import DoctorScreen
from opengrader.tui.ui.screens.course_grades_screen import CourseGradesScreen
from opengrader.tui.ui.screens.handoff_screen import GraderPacketScreen, MergeScreen
from opengrader.tui.ui.screens.llm_eval_screen import LlmEvalScreen
from opengrader.tui.ui.screens.path_picker_screen import PathPickerScreen, PathPickMode
from opengrader.tui.ui.screens.region_map_screen import RegionMapScreen

# Shown next to the parallel-jobs field so the user has a real ceiling in mind
# rather than guessing. Read once at import; core counts don't change at runtime.
_CPU_COUNT = os.cpu_count() or 1


# One glyph per stage outcome, for the "Run the whole pipeline" section's
# per-stage log. Deliberately its own small dict rather than an import from
# ``cli/run.py``'s ``_STATUS_GLYPH`` — that module is a terminal narrator this
# screen must not call into (see the section's docstring), and the mapping
# itself is stable enough that a second three-line copy costs less than a
# cross-module dependency for four emoji.
_PIPE_STATUS_GLYPH = {
    StageStatus.RAN: "✓",
    StageStatus.REUSED: "↺",
    StageStatus.SKIPPED: "·",
    StageStatus.BLOCKED: "●",
    StageStatus.FAILED: "✗",
}


# How far up from an assignment folder to look for a course file. Four levels
# covers the layouts instructors actually use (`course/exams/midterm/subs/`)
# without wandering into the home directory and picking up an unrelated course.
_COURSE_SEARCH_DEPTH = 4


def _find_course_file(start: Path) -> Path | None:
    """Walk up from ``start`` looking for a ``course.toml``, or return None.

    Best-effort convenience only — a miss just leaves the field empty, so an
    unreadable directory is swallowed rather than surfaced.
    """
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
    """
    A setup screen allowing users to configure and run processing scripts
    before launching the grading TUI.
    """

    DEFAULT_CSS = """
    SetupScreen #setup-container {
        align-horizontal: center;
        scrollbar-size: 1 1;
    }

    /* Containers inside the scroll view must size to their content;
       a Vertical's default 1fr height clips children and breaks the
       scroll range. */
    SetupScreen #setup-content {
        width: 100%;
        max-width: 104;
        height: auto;
        padding: 1 2 2 2;
    }

    /* ── Hero header ─────────────────────────────────────────── */
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

    /* ── Cards ───────────────────────────────────────────────── */
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

    /* ── Buttons: flat, compact chips ────────────────────────── */
    SetupScreen .path-row .browse-button {
        margin-top: 1;
    }

    /* ── Run the whole pipeline (step 0 of the numbered steps) ─── */
    /* Same "bare Vertical defaults to 1fr and hides what follows it inside an
       auto-sized Collapsible" trap documented below for the numbered steps —
       this section is one of them now, and the rule is identical. */
    SetupScreen #pipe-progress {
        height: auto;
    }

    SetupScreen #pipe-pages {
        width: 24;
    }

    /* ── Section headings ────────────────────────────────────────
       One rule for every section rather than one per id. The page now has
       three (Run the whole pipeline / Prepare an assignment / Helper tools)
       and they must be visually identical — a section that styles itself
       reads as a different *kind* of thing, which is exactly the impression
       the pipeline card gave while it was a bare card above the first
       heading. Ids are kept for querying; the class carries the look. */
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

    /* ── Form rows ───────────────────────────────────────────── */
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

    /* Advanced disclosures whose contents appear on demand — Extract's
       parallel-jobs and re-read blocks. Bare Verticals default to
       height: 1fr and stretch to the bottom of the auto-sized Collapsible —
       which hides whatever follows them (for the parallel-jobs block that was
       the anonymity-lock checkbox and the Run Extractor button) — so size them
       to their content. */
    SetupScreen #ext-parallel-options,
    SetupScreen #ext-refine-options {
        height: auto;
    }

    /* Roster / section sub-form. Bare Verticals default to height: 1fr, which
       stretches the whole area to the bottom of the card; size them to their
       content. The RadioSet and the multi-section roster list get explicit
       bounds so neither balloons. */
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

    # Path-taking inputs: input id -> (what the picker may confirm, dialog title).
    _PATH_FIELDS: dict[str, tuple[PathPickMode, str]] = {
        "split-input": ("file", "Choose the scanned submissions PDF"),
        "split-output": ("directory", "Choose the split output directory"),
        "split-template-path": ("file", "Choose the blank template PDF"),
        "ext-input": ("directory", "Choose the directory of split PDFs"),
        "ext-output": ("directory", "Choose the extraction output directory"),
        "ext-template-path": ("file", "Choose the blank template PDF"),
        "ext-id-path": ("file", "Choose the roster/ID file"),
        # "any" rather than "directory": a single photo is a legitimate import
        # (one student, one page), and the tool detects which it was given.
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
        "tui-qa-path": ("file", "Choose the QA markdown file"),
        "tui-sub-path": ("any", "Choose the submissions file or directory"),
        # "Run the whole pipeline" section. "any" for the scan: a bulk-scan PDF
        # and a directory of already-split PDFs are both legitimate inputs (see
        # `PipelineRequest.input_is_split`), and the picker can't tell which the
        # user means ahead of time.
        "pipe-scan": ("any", "Choose the scanned bundle PDF, or a folder of split PDFs"),
        "pipe-qa": ("file", "Choose the answer-key markdown file"),
        "pipe-out": ("directory", "Choose (or create) the class output folder"),
        "pipe-roster": ("file", "Choose the roster/ID file (.tsv)"),
        "pipe-template": ("file", "Choose the blank template PDF"),
    }

    class LaunchRequested(Message):
        """Emitted when the user wants to launch the TUI with specific paths."""
        def __init__(self, qa_path: str, submissions_path: str) -> None:
            super().__init__()
            self.qa_path = qa_path
            self.submissions_path = submissions_path

    class GradeWhileExtractingRequested(Message):
        """Open the grading workspace on a folder this screen's own extraction
        worker is still writing into.

        A separate message from ``LaunchRequested`` (rather than the same one
        with a flag) because the app's two handlers genuinely differ: this one
        also has to start the fold-in poll and tell it, with certainty, that
        extraction is live — see `TuiApp.on_setup_screen_grade_while_extracting_requested`.
        """

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
        """A path Input with a Browse button that opens the file picker."""
        classes = "path-row hidden" if hidden else "path-row"
        return Horizontal(
            Input(placeholder=placeholder, id=input_id, classes="path-input"),
            Button("Browse", id=f"browse-{input_id}", classes="browse-button", compact=True),
            classes=classes,
            id=f"{input_id}-row",
        )

    @staticmethod
    def _field_label(text: str, detail: str = "") -> Static:
        """A label rendered above a form field, with an optional muted detail."""
        markup = f"[b]{text}[/b]"
        if detail:
            markup = f"{markup}  [dim]{detail}[/dim]"
        return Static(markup, classes="field-label")

    @staticmethod
    def _card_actions(*buttons: Button) -> Horizontal:
        """Right-aligned action row at the bottom of a card.

        Variadic so a card that wants two buttons side by side (Preview and
        Run Pipeline, below) gets the same right-aligned row as every card
        with one — every existing call site passes exactly one button and
        keeps working unchanged.
        """
        return Horizontal(*buttons, classes="card-actions")

    @staticmethod
    def _ai() -> ai_config.ResolvedAI:
        """The AI settings as they stand right now, for whatever is about to run.

        Read fresh on every use rather than cached at startup: the AI Setup
        helper tool writes the file mid-session, and a step run afterwards must
        use what the user just saved, not what was on disk when the app opened.
        Reading a small JSON file is far cheaper than any run that consults it.

        Almost nothing has to *pass* these values on: every CLI resolves the
        same settings for its own argparse defaults (see ``core/ai_config``), so
        the setup screen only reads them where it has to make a decision of its
        own — whether to add ``--auto-ollama``, what to tell the environment
        check to probe, and what to put in the pipeline's ``LlmOptions``.
        """
        return ai_config.resolve()

    def _auto_ollama_argv(self) -> list[str]:
        """``--auto-ollama``, but only when the user asked for that behaviour.

        Split/Extract/Answer-key used to pass this unconditionally. It is now
        the AI Setup screen's "Start the AI automatically when needed" setting,
        because starting a server and downloading a multi-gigabyte model is
        exactly the kind of thing someone on a metered connection wants to
        decide once, for everything, rather than discover.
        """
        return ["--auto-ollama"] if self._ai().auto_start else []

    def _jobs_argv(self) -> list[str] | None:
        """``--jobs N``, or ``[]`` to let the extractor choose. ``None`` = invalid.

        Same rule as every other disclosure here: with the box unchecked the
        field is ignored entirely, so a number left behind in the hidden
        input can never silently apply to a later run. The value is validated
        here rather than in the background worker, where argparse rejecting it
        would surface as a crash instead of a notification.
        """
        if not self.query_one("#ext-parallel", Checkbox).value:
            return []
        jobs = self.query_one("#ext-jobs", Input).value.strip()
        if not jobs:
            return []  # ticked but left blank still means "choose for me"
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

                with Vertical(id="launch-section") as launch_card:
                    launch_card.border_title = "▸ Start grading"
                    yield Static(
                        "Open a prepared assignment in the grading workspace. "
                        "New scans? Prepare them below first.",
                        classes="card-hint",
                    )
                    yield self._field_label("Answer key", "QA markdown file, e.g. solutions.md")
                    yield self._path_row("Path to QA markdown file…", "tui-qa-path")
                    yield self._field_label(
                        "Submissions", "directory containing .submission.md files"
                    )
                    yield self._path_row("Path to submissions directory…", "tui-sub-path")
                    yield self._card_actions(
                        Button("Launch Grading", variant="success", id="launch-button", compact=True)
                    )

                # Its own section, above the numbered steps, and built from the
                # same heading + caption + Collapsible parts they use. The
                # earlier shape — a bare card sitting above the first heading —
                # made it read as a different *kind* of thing, and folding it in
                # as "step 0" of the list below buried the recommended path
                # inside the list of alternatives to it. A section of its own
                # says both true things at once: this is the way to run an
                # assignment, and it is made of the steps underneath.
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
                    # Deliberately not a restatement of the section caption
                    # above: that says what the run does, this says what you
                    # are left holding when it finishes.
                    yield Static(
                        "Fill in three paths and press Run. You end up with the "
                        "grading workspace open on the whole class, a split/ "
                        "and submissions/ folder you can re-open any time, and "
                        "a record of the run itself. Preview shows the plan "
                        "without writing anything.",
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
                    yield self._field_label(
                        "Class folder",
                        "builds split/, submissions/ and the run record here",
                    )
                    yield self._path_row(
                        "Where to build this class's work…", "pipe-out"
                    )

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
                    # Each option is a short label with its explanation directly
                    # underneath, matching the Extract step below. A Textual
                    # Checkbox is hard-limited to one line (ToggleButton's
                    # get_content_height returns 1, so no amount of CSS wraps
                    # it), which means anything past the card's width is simply
                    # lost — the label has to be scannable on its own and the
                    # detail has to live in a Static that *can* wrap.
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

                # First in the list on purpose: when something is wrong, this is
                # the tool that says what — and someone whose scans extract to
                # empty answers should not have to read past six other tools to
                # find out their text recognition was never installed.
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

                # Immediately after the environment check, and before anything
                # that runs: the check is what tells you the AI is missing, and
                # this is where you say which one to use. Every step that can
                # use AI reads what is saved here, which is why none of them
                # asks about models any more.
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

                # Directly after the environment check because it comes *before*
                # step 1 for a class with no scanner. It ends where Split ends —
                # a folder of per-student PDFs — so nothing downstream ever
                # learns the pages arrived as photos, which is why it belongs
                # here as an optional on-ramp rather than as a numbered step
                # every assignment has to walk past.
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

                # Next to photo import because they solve the same shape of
                # problem — "my class doesn't work the way the pipeline assumes"
                # — and an instructor arriving with their own assignment and
                # photos needs both.
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
                        # The sheet count only appears under scrambling because
                        # that is the only time the sheets differ from each
                        # other. Unscrambled, every sheet is identical and one
                        # printed page plus a photocopier beats asking a
                        # teacher for a number.
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
        """Toggle visibility of related path inputs when checkboxes are toggled."""
        if event.checkbox.id == "split-use-template":
            self.query_one("#split-template-path-row").set_class(not event.value, "hidden")
        elif event.checkbox.id == "ext-use-template":
            self.query_one("#ext-template-path-row").set_class(not event.value, "hidden")
            # The extractor refuses both at once (they are two answers to "where
            # are the boxes?"), so untick the other here rather than letting the
            # run fail on a form that looked valid.
            if event.value:
                self.query_one("#ext-use-region-map", Checkbox).value = False
        elif event.checkbox.id == "ext-use-region-map":
            self.query_one("#ext-region-map-area").set_class(not event.value, "hidden")
            if event.value:
                self.query_one("#ext-use-template", Checkbox).value = False
        elif event.checkbox.id == "ext-use-id":
            self.query_one("#ext-roster-area").set_class(not event.value, "hidden")
        elif event.checkbox.id in ("tmpl-qr", "tmpl-bubble-sheet", "tmpl-shuffle"):
            # The roster row personalizes either kind of sheet, so it appears
            # for whichever is on. A bubble sheet always carries its QR, so
            # ticking it alone is enough to reach the roster.
            self._sync_template_options()
        elif event.checkbox.id == "ak-use-template":
            self.query_one("#ak-template-path-row").set_class(not event.value, "hidden")
        elif event.checkbox.id == "ext-parallel":
            self.query_one("#ext-parallel-options").set_class(not event.value, "hidden")
        elif event.checkbox.id == "ext-refine":
            self.query_one("#ext-refine-options").set_class(not event.value, "hidden")
            # The two answer different questions and the CLI refuses the pair, so
            # untick the other rather than letting the run fail after the user has
            # filled in every other field.
            if event.value:
                self.query_one("#ext-resume", Checkbox).value = False
        elif event.checkbox.id == "ext-resume" and event.value:
            self.query_one("#ext-refine", Checkbox).value = False
        elif event.checkbox.id == "pipe-use-roster":
            self.query_one("#pipe-roster-row").set_class(not event.value, "hidden")
        elif event.checkbox.id == "pipe-use-template":
            self.query_one("#pipe-template-row").set_class(not event.value, "hidden")

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """Swap the roster inputs between one-section and multi-section modes."""
        if event.radio_set.id == "ext-roster-mode":
            one_section = event.pressed.id == "ext-mode-one"
            self.query_one("#ext-one-section").set_class(not one_section, "hidden")
            self.query_one("#ext-many-section").set_class(one_section, "hidden")

    def _open_path_picker(self, input_id: str) -> None:
        """Open the modal file browser and fill the input with the result."""
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

        self.app.push_screen(
            PathPickerScreen(title=title, mode=mode, start_path=start_path),
            _apply,
        )

    def _append_roster_path(self) -> None:
        """Pick a roster file and append it as a new line in the roster list.

        Multi-section mode takes several rosters, so Browse *appends* (rather
        than replacing an Input) — the grader can then type '=SectionName' after
        any line to override the filename-derived section name.
        """
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
        if event.button.id == "launch-button":
            qa_path = self._clean_path(self.query_one("#tui-qa-path", Input).value)
            sub_path = self._clean_path(self.query_one("#tui-sub-path", Input).value)
            
            if not qa_path or not sub_path:
                self.app.notify("Both QA path and Submissions path are required to launch.", severity="error")
                return
            
            self.post_message(self.LaunchRequested(qa_path, sub_path))
        elif event.button.id == "run-splitter":
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
        elif event.button.id == "run-photo-import":
            self._run_photo_import()
        elif event.button.id == "run-region-map":
            self._open_region_map()
        elif event.button.id == "pipe-run":
            self._run_pipeline()
        elif event.button.id == "pipe-preview":
            self._run_pipeline_preview()

    def _handoff_submissions(self, input_id: str) -> str:
        """The folder a Helper Tool acts on: its own field, else the launch card.

        Falling back to the launch card means the common case — share or back up
        the class you are about to grade — needs no second path typed in, while
        the field is still there for a folder this session never opened.
        """
        own = self._clean_path(self.query_one(f"#{input_id}", Input).value)
        if own:
            return own
        return self._clean_path(self.query_one("#tui-sub-path", Input).value)

    def _open_grader_packet(self) -> None:
        """Open the create-a-grader-copy modal, prefilled from the launch card."""
        submissions = self._handoff_submissions("handoff-submissions")
        if not submissions:
            self.app.notify(
                "Choose the submissions folder to share.", severity="error"
            )
            return
        self.app.push_screen(
            GraderPacketScreen(
                submissions_path=submissions,
                qa_file_path=self._clean_path(
                    self.query_one("#tui-qa-path", Input).value
                ),
            )
        )

    def _open_merge(self) -> None:
        """Open the merge-a-copy-back modal for the same folder."""
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
        """Open the region-map editor, and wire its result into Extract.

        The map it saves is only useful as the Extract step's ``--region-map``,
        so the path it hands back is written straight into that field and the
        option ticked — the same closing-the-loop move the calibration tool
        makes with the confidence it recommends. Without it the instructor has
        to remember a filename the editor chose for them.
        """
        pdf_path = self._clean_path(self.query_one("#region-pdf", Input).value)
        if not pdf_path:
            self.app.notify(
                "Choose the blank assignment PDF to draw regions on.",
                severity="error",
            )
            return
        existing = self._clean_path(self.query_one("#region-map-path", Input).value)

        def _apply(saved: Path | None) -> None:
            if saved is None:  # cancelled — nothing was written
                return
            self.query_one("#region-map-path", Input).value = str(saved)
            self.query_one("#ext-region-map-path", Input).value = str(saved)
            self.query_one("#ext-use-region-map", Checkbox).value = True

        self.app.push_screen(
            RegionMapScreen(pdf_path, existing or None),
            _apply,
        )

    def _open_doctor(self) -> None:
        """Open the environment check.

        Needs no folder — it is about the machine, not about any class — but it
        is pointed at the models saved in AI setup, so it reports on what this
        user's pipeline would actually ask for rather than on the built-in
        defaults.
        """
        current = self._ai()
        self.app.push_screen(
            DoctorScreen(
                vision_model=current.vision_model,
                text_model=current.text_model,
                ollama_host=current.host,
            )
        )

    def _ai_summary_text(self) -> str:
        """One muted line naming what the AI is currently set to *do*.

        Shown on the closed AI setup card so the answer to "which model am I
        using, and for what?" needs no click — the question the four removed
        per-section disclosures used to answer badly, by asking it again every
        time. Naming a model is not enough on its own: a summary that named two
        configured models while every feature toggle was off would say
        something true and misleading at once, so each job states whether it is
        even switched on before it says which model does it.
        """
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
        """One muted line naming what the AI is set to do for one step.

        Replaces that step's deleted AI checkbox: the checkbox is gone, but
        the fact it used to state — is the AI in play here, and doing what —
        must still be visible without opening AI setup. Reads `self._ai()`
        fresh on every call for the same reason `_ai()` itself does: the
        answer can change mid-session, the instant AI setup is saved.
        """
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
        """Refresh every place on this screen that states what the AI is set
        to do, after AI setup — or an accepted calibration recommendation —
        may have changed it.

        One method rather than each caller updating its own subset: a saved
        change can move any of read_handwriting/escalate_only/transcribe_math/
        grade_written/require_calibration/min_confidence, and every one of
        the six displays below reads at least one of them, so a caller that
        only refreshed "the field it touched" would drift the moment a second
        field started mattering to a display it didn't know about.
        """
        self.query_one("#ai-current-summary", Static).update(self._ai_summary_text())
        self.query_one("#split-ai-status", Static).update(self._ai_status_text("split"))
        self.query_one("#ext-ai-status", Static).update(self._ai_status_text("ext"))
        self.query_one("#ak-ai-status", Static).update(self._ai_status_text("ak"))
        self.query_one("#grade-ai-status", Static).update(self._ai_status_text("grade"))
        self.query_one("#pipe-ai-status", Static).update(self._ai_status_text("pipe"))

    def _open_ai_settings(self) -> None:
        """Open the one place the AI is configured, and refresh every display."""

        def _refresh(saved: ai_config.AISettings | None) -> None:
            if saved is None:  # cancelled — nothing was written
                return
            # Re-read rather than render `saved` directly: the displays state
            # *resolved* values, and a blank field there means "the default",
            # which only `resolve` knows how to fill in.
            self._refresh_ai_displays()

        self.app.push_screen(AISettingsScreen(), _refresh)

    def _open_course_grades(self) -> None:
        """Open the course-gradebook modal, prefilled if a course file is nearby.

        A ``course.toml`` sits *above* the assignment folders by design, so when
        the launch card already names a submissions folder we walk up from it
        looking for one. Finding it saves the instructor a Browse; not finding
        it costs nothing, since the field is simply left empty.
        """
        prefill = ""
        submissions = self._clean_path(self.query_one("#tui-sub-path", Input).value)
        if submissions:
            prefill = str(_find_course_file(Path(submissions)) or "")
        self.app.push_screen(CourseGradesScreen(course_path=prefill))

    def _open_llm_eval(self) -> None:
        """Open the AI-grading calibration modal, prefilled from Auto-grade.

        The paths come from the Auto-grade step above rather than from fields of
        their own: this tool measures *that* step's model against work already
        marked, and it is the same assignment either way.

        An accepted recommendation is now **saved into the AI settings** rather
        than typed into a form field. That is what closes the loop properly: the
        threshold is a property of how far this model can be trusted, so it
        should reach every later run — including one started from a terminal —
        not just the Auto-grade box that happened to be on screen.
        """
        solutions = self._clean_path(self.query_one("#grade-solutions", Input).value)
        submissions = self._clean_path(self.query_one("#grade-input", Input).value)

        def _apply(recommended: float | None) -> None:
            if recommended is None:
                return
            settings = replace(
                ai_config.load_settings(),
                min_confidence=round(recommended, 2),
                # Accepting a measurement is both the provenance fact
                # `require_calibration` checks for (see `ResolvedAI.problems`)
                # and an unambiguous statement that the user wants AI
                # grading — nobody measures a confidence they don't intend
                # to use.
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
            # The Auto-grade step no longer shows a checkbox or a confidence
            # field at all — the visible confirmation is every AI display on
            # this screen picking up the change, not a box ticking itself.
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
        # If pages is blank and no template, the CLI auto-detects submission
        # boundaries by the student Name/ID header — nothing to add here.

        if use_template and template:
            argv.extend(["--blank-template", template])
        if self.query_one("#split-scan-check", Checkbox).value:
            # Default-on, and it fixes rather than only reports: a page the
            # scanner fed in upside down produces an empty transcription and a
            # zero with no error anywhere, and nothing downstream can undo it.
            # --fix-order is the same bargain for a worse failure — a swapped
            # sheet grades cleanly against the wrong question — and it is safe to
            # pair with the default because it acts only on certain page numbers
            # and does nothing at all on a scan that is already in order.
            argv.extend(["--fix-rotation", "--fix-order"])
        if self.query_one("#split-reorder", Checkbox).value:
            # The unconditional pass, for the case --fix-order deliberately
            # declines: page numbers legible enough to sort by, but only via OCR.
            argv.append("--reorder")
        if self.query_one("#split-extra-pages", Checkbox).value:
            argv.append("--extra-work-pages")
        # No checkbox gating this any more, and no model or host flags: the
        # splitter resolves whether/what to read from the saved AI settings
        # itself (core/ai_config; see #split-ai-status above), so this only
        # has to say whether to start the server for the user.
        argv.extend(self._auto_ollama_argv())

        self.query_one("#split-progress-status", Static).update("Splitting…")
        self.query_one("#split-progress").remove_class("hidden")
        self.query_one("#run-splitter", Button).disabled = True
        self._splitter_worker(argv)

    @work(thread=True, exclusive=True, group="splitter")
    def _splitter_worker(self, argv: list[str]) -> None:
        """Run the splitter (and any reorder pass) off the UI thread so a long
        vision-model reorder doesn't freeze the app."""
        app = self.app

        def on_status(message: str) -> None:
            app.call_from_thread(self._split_set_status, message)

        try:
            run_splitter_cli(argv, status_callback=on_status)
        except SystemExit as exc:  # the CLI calls sys.exit on fatal input errors
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
        """Validate the photo-import form and hand it to the worker.

        Calls `import_photos` rather than the `photo-import` CLI (the pattern
        the pipeline steps above use) because the CLI's whole job past the core
        call is printing a report to a terminal this screen doesn't have —
        the structured report is what we actually want to summarize here.
        """
        input_path = self._clean_path(self.query_one("#photo-input", Input).value)
        output_dir = self._clean_path(self.query_one("#photo-output", Input).value)
        if not input_path or not output_dir:
            self.app.notify(
                "Photos path and output directory are required.", severity="error"
            )
            return

        # Rejected here rather than passed through, because a bad value would
        # otherwise surface as a grouping that silently mixed two students'
        # pages into one PDF — a mistake nothing downstream can detect.
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
        """Rectify and bind the photos off the UI thread.

        A phone photo is several megapixels and every one of them gets warped
        and re-encoded, so a class-sized folder is comfortably minutes of work —
        long enough that running it inline would look like the app had hung.
        """
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
            # The tool's own diagnostics — "no images found here", "HEIC photos
            # need this install" — are written to be read by the user, so they
            # are shown verbatim rather than wrapped in a generic failure.
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
        """One sentence naming what was written and what was not.

        Unreadable photos are called out by count even though the import
        succeeded: they are the one outcome where a student silently ends up
        with a missing page, so "12 submissions" alone would be a half-truth.
        """
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
            # Point Extract at what we just made, so the next step is one click
            # rather than a re-typed path. Only when that field is still empty:
            # a path already sitting there is the user's, not ours to replace.
            extractor_input = self.query_one("#ext-input", Input)
            if not extractor_input.value.strip():
                extractor_input.value = str(report.output_dir)

    def _roster_argv(self) -> list[str]:
        """Build the --id-file/--section args from the roster sub-form.

        One-section mode → a single ``--id-file roster.tsv=SectionName`` (or bare
        ``--section`` when no roster is given but a name is). Multi-section mode →
        one ``--id-file`` per non-blank line of the roster list. Empty inputs
        yield no args (matches the prior "roster is optional" behavior).
        """
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

        # Never both: the extractor rejects the pair outright, and the two
        # checkboxes untick each other above, so at most one of these fires.
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

        # No --llm-responses / --llm-escalate / --llm-math: whether to
        # transcribe with the AI, whether to escalate only what OCR read
        # badly, and whether to read maths as LaTeX are all the saved AI
        # settings now, and the extractor resolves every one of them for
        # itself — see #ext-ai-status above.

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
            # Unchecked, the DPI field is ignored entirely — the same rule the
            # Ollama-advanced and parallel-jobs disclosures follow, so a value
            # left behind in a hidden input can never apply to a later run.
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

        # Whether to start the AI for the user is the one AI question this step
        # still asks — and it asks it of the saved settings, not of the form.
        argv.extend(self._auto_ollama_argv())

        lock_anon = self.query_one("#ext-lock-anon", Checkbox).value

        # Reset and reveal the progress area, then run off the UI thread so the
        # app stays responsive during the (potentially long) vision pass.
        self._ext_total = 0
        bar = self.query_one("#ext-progress-bar", ProgressBar)
        bar.update(total=None, progress=0)
        self.query_one("#ext-progress-status", Static).update("Starting…")
        self.query_one("#ext-progress").remove_class("hidden")
        self.query_one("#run-extractor", Button).disabled = True
        # This screen is the one place that can say with certainty extraction
        # is live (it just started the worker below) rather than guess from
        # manifest recency — `_open_grade_while_extracting` reads this flag
        # and the app-level poll passes it straight to `ExtractionWatcher` as
        # `known_active`. See `core/submissions/watch.py`.
        self.extraction_in_progress = True

        self._extractor_worker(argv, output_dir, lock_anon)

    @work(thread=True, exclusive=True, group="extractor")
    def _extractor_worker(
        self, argv: list[str], output_dir: str, lock_anon: bool
    ) -> None:
        """Run the extractor in a background thread, marshalling progress and
        completion back onto the UI thread."""
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
                # Mark the freshly created folder anonymous-locked so graders who
                # open it inherit the lock rather than opting in.
                write_policy(output_dir, GradingPolicy(mode="multi", anonymity_locked=True))
        except Exception as exc:  # surface any failure to the user
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
        # The worker this screen launched is done, one way or the other — the
        # certainty `_open_grade_while_extracting`/the app poll relied on no
        # longer holds, so it must flip immediately rather than let a grading
        # workspace opened moments ago keep believing extraction is live for
        # another `_ACTIVE_RECENCY_SECONDS` of guesswork.
        self.extraction_in_progress = False
        self.app.notify(message, severity="error" if is_error else "information")

    def _open_grade_while_extracting(self) -> None:
        """Open the grading workspace on the folder this screen is extracting
        into, without waiting for the batch to finish.

        The headline path for this feature: extracting a whole class takes
        minutes, grading takes hours, and the first submission is gradeable
        the instant it lands. The answer key path is read from the launch
        card above rather than a field of its own — the common case is
        grading the assignment this same session just prepared.
        """
        qa_path = self._clean_path(self.query_one("#tui-qa-path", Input).value)
        output_dir = self._clean_path(self.query_one("#ext-output", Input).value)
        if not qa_path:
            self.app.notify(
                "Set the answer key path in \"Start grading\" above first.",
                severity="error",
            )
            return
        if not output_dir:
            self.app.notify(
                "Set the extraction output directory first.", severity="error"
            )
            return
        self.post_message(self.GradeWhileExtractingRequested(qa_path, output_dir))

    # ------------------------------------------------------------------ #
    # "Run the whole pipeline" — the one-shot front end over
    # core/pipeline.py's `run_pipeline`/`plan_pipeline`. See that module's
    # docstring for the stage order and the ledger; this section only builds
    # a `PipelineRequest` from the form, runs it off the UI thread, and
    # narrates the `PipelineProgress` callbacks the same way the numbered
    # steps narrate their own CLIs.
    # ------------------------------------------------------------------ #

    def _build_pipeline_request(self) -> PipelineRequest | None:
        """Read the form into a `PipelineRequest`, or notify and return None.

        Validation lives here rather than in the worker so a bad field reads
        as an immediate notification instead of a background-thread crash —
        the same reasoning `_run_extractor`'s `_jobs_argv` gives.
        """
        scan = self._clean_path(self.query_one("#pipe-scan", Input).value)
        out = self._clean_path(self.query_one("#pipe-out", Input).value)
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

        # No checkbox any more: whether the AI helps at all, for which of the
        # two jobs, and the host/auto-start to use are all the saved AI
        # settings — read once here so the same values drive `LlmOptions`
        # below and `#pipe-ai-status` above. The model names stay blank
        # (`LlmOptions`' own default): each provider's own client already
        # resolves the configured model from this same settings file, so
        # naming them again here would be a second channel for one answer.
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
            # Whether auto-grade runs once extraction finishes is likewise a
            # saved AI setting now, not a checkbox on this card.
            auto_grade=current.auto_grade,
            # The one extra checkbox this section has beyond the design's core
            # fields — see `core/pipeline.py`'s scan-check stage: a
            # high-severity finding blocks the run outright, and `force` is the
            # only way past that from inside the TUI. Without it a blocked
            # instructor would have no move except leaving this screen to run
            # `opengrader-run --force` from a terminal.
            force=self.query_one("#pipe-force", Checkbox).value,
        )

    def _run_pipeline_preview(self) -> None:
        """`plan_pipeline` in a worker, and nothing else — this is the TUI's
        `--dry-run`: it reads the ledger and the input, and writes nothing."""
        request = self._build_pipeline_request()
        if request is None:
            return
        self.query_one("#pipe-preview-output").add_class("hidden")
        self.query_one("#pipe-run", Button).disabled = True
        self.query_one("#pipe-preview", Button).disabled = True
        self._pipeline_preview_worker(request)

    @work(thread=True, exclusive=True, group="pipeline-preview")
    def _pipeline_preview_worker(self, request: PipelineRequest) -> None:
        """`plan_pipeline` off the UI thread — it fingerprints every input file,
        which on a large scan is worth not blocking the app for."""
        app = self.app
        try:
            plans = plan_pipeline(request)
        except Exception as exc:  # surface any failure to the user
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
            # The checkbox asked for something this run cannot do (opening the
            # workspace needs an answer key, early or late) — say so rather
            # than quietly falling back to "wait for the end", which would
            # look identical to the checkbox having been unticked.
            self.app.notify(
                "No answer key set — grading will open once the whole run "
                "finishes instead of as submissions arrive. Add an answer "
                "key above to grade sooner.",
            )

        # Reset and reveal the progress area, same shape as `_run_extractor`'s.
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
        # This screen is the one place that can say with certainty extraction
        # might still be running (it is about to start the whole pipeline,
        # which always reaches the extract stage before auto-grade) — see
        # `_run_extractor`'s identical flag and `_ext_finish`'s comment on why
        # it has to flip back the instant the worker ends, not on a timer.
        self.extraction_in_progress = True

        self._pipeline_worker(request, asap, qa_path)

    @work(thread=True, exclusive=True, group="pipeline")
    def _pipeline_worker(
        self, request: PipelineRequest, asap: bool, qa_path: str
    ) -> None:
        """Run the pipeline in a background thread, marshalling every UI
        update — and the early-launch decision — back onto the UI thread."""
        app = self.app
        # Mutable across the closures below without `nonlocal` plumbing per
        # callback; `stage` tracks which stage is currently running (so the
        # early-launch check only fires during extraction) and
        # `launched_early` guards against posting the open-workspace message
        # twice, which would reset a workspace the user is already grading in.
        state = {"stage": "", "launched_early": False}

        # Built only when an early launch is actually possible — polling reads
        # the extraction manifest (see core/submissions/watch.py), the same
        # after-the-fact-only signal the "Grade what's ready" button and the
        # background fold-in poll already trust, so a submission is never
        # handed to the grader mid-write.
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
            # A new submission file starting is the natural checkpoint for
            # "has the previous one finished?" — polling here rather than on
            # every `on_response` keeps this to one manifest stat() per file
            # instead of one per transcribed answer.
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
        except Exception as exc:  # run_pipeline itself only re-raises
            # KeyboardInterrupt or SystemExit escaping a stage — everything
            # else already comes back as a FAILED StageOutcome inside the
            # report (see `run_pipeline`'s docstring: "a failed run still
            # returns its report").
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
        """The launch-policy checkbox's "on" behaviour: open the workspace the
        moment the extract stage has produced anything gradeable.

        Posts the same message the numbered Extract step's "Grade what's
        ready →" button posts, so `TuiApp` handles both identically — see
        `on_setup_screen_grade_while_extracting_requested`.
        """
        self.app.notify(
            "Submissions are ready — opening the grading workspace while the "
            "rest of the pipeline keeps running in the background."
        )
        self.post_message(self.GradeWhileExtractingRequested(qa_path, submissions_path))

    def _pipe_reset_buttons(self) -> None:
        self.query_one("#pipe-run", Button).disabled = False
        self.query_one("#pipe-preview", Button).disabled = False
        # The worker this screen launched is done — ran to completion, failed,
        # or blocked, it does not matter which — so the certainty backing
        # `extraction_in_progress` no longer holds. See `_ext_finish`'s
        # identical comment for why this has to happen the instant the worker
        # ends rather than be inferred later from the manifest.
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
            # A BLOCKED outcome must never open the grading workspace — that is
            # the whole point of the scan pre-flight. Surface the one-line
            # message and the full multi-line detail (already appended to the
            # stage log by `_pipe_stage_finish`) and name both ways forward.
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
            # The workspace is already open and the user may already be
            # grading in it — posting LaunchRequested now would reset that
            # workflow state out from under them, so this run's completion is
            # just a notification, not a second launch.
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
            # No answer key was ever given, so there is nothing to launch with
            # — `LaunchRequested` needs one exactly as much as the early path
            # did. The submissions themselves are real and worth pointing at.
            self.app.notify(
                f"{report.submission_count} submission(s) ready in "
                f"{report.submissions_dir}. Add an answer key above, or open "
                'them from "Start grading".'
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
        # No checkbox gating this any more: whether a scanned answer key is
        # read with the handwriting model is the saved AI setting (see
        # #ak-ai-status above), so this only has to say whether to start the
        # server for the user.
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
        """Build the answer key off the UI thread (PDF + vision model can be slow)."""
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

        # No --llm: whether to grade written answers with the AI, which
        # model does it, and the confidence gate all come from the saved AI
        # settings now (see #grade-ai-status above), which is also where the
        # CLI reads its own argparse default from — so this screen never
        # builds that flag itself.

        # Reset and reveal the progress area, then grade off the UI thread: LLM
        # grading is a network call per free response, so it must not block.
        self._grade_prefix = ""
        bar = self.query_one("#autograde-progress-bar", ProgressBar)
        bar.update(total=None, progress=0)
        self.query_one("#autograde-progress-status", Static).update("Starting…")
        self.query_one("#autograde-progress").remove_class("hidden")
        self.query_one("#run-grading", Button).disabled = True
        # The Ollama pre-flight probe still has to fire exactly when a model
        # will actually be asked to grade — that is `grade_written`, read
        # fresh here now that no checkbox carries the answer.
        self._grading_worker(argv, probe_llm=self._ai().grade_written)

    @work(thread=True, exclusive=True, group="grading")
    def _grading_worker(self, argv: list[str], *, probe_llm: bool = False) -> None:
        """Run auto-grading in a background thread, marshalling progress and
        completion back onto the UI thread."""
        app = self.app

        if probe_llm:
            # Fast path: if the server is already up and the model present, run
            # straight away. Otherwise ask the CLI to auto-start the server and
            # pull the model — but only if the user asked for that, the same
            # rule the Split/Extract/Answer-key steps now follow.
            #
            # **This probe belongs on the worker, not on the UI thread.** It is
            # an HTTP call carrying the configured timeout (120 s by default),
            # and since AI setup lets a user type any host, a typo'd or
            # firewalled one no longer refuses the connection instantly — it
            # black-holes the request and the whole TUI freezes for the full
            # timeout with no progress bar and no way to cancel. Everything the
            # decision needs (`grade_written`, read above; the settings file,
            # read here) is safe to reach from a thread, so there was never a
            # reason for it to run before the worker started.
            try:
                ensure_model_available(ai_config.text_config())
            except OllamaError:
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
        except SystemExit as exc:  # the CLI calls sys.exit on fatal input errors
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
        # index is 1-based; index-1 files are done before this one starts.
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
        """Show a CLI status line (server start / model pull) before grading."""
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
        """Reveal the roster row and the bubble-sheet explanation as needed."""
        bubble = self.query_one("#tmpl-bubble-sheet", Checkbox).value
        qr = self.query_one("#tmpl-qr", Checkbox).value
        self.query_one("#tmpl-bubble-note").set_class(not bubble, "hidden")
        self.query_one("#tmpl-roster-area").set_class(not (qr or bubble), "hidden")
        # Copy count and scrambling are bubble-sheet only: a written template
        # is read back by aligning every scan to one blank layout, so a
        # per-sheet question order could never be recovered.
        self.query_one("#tmpl-batch-area").set_class(not bubble, "hidden")
        # ...and the count sits under scrambling, since that is the only case
        # where the sheets are not interchangeable.
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
        # The roster row is only reachable under QR or bubble-sheet; guard
        # against a stale value from a hidden input if both were toggled off.
        roster = (
            self._clean_path(self.query_one("#tmpl-roster", Input).value)
            if (bubble or self.query_one("#tmpl-qr", Checkbox).value)
            else ""
        )
        if roster:
            # Personalized batch — implies QR for either kind of sheet.
            argv.extend(["--roster", roster])
        elif self.query_one("#tmpl-qr", Checkbox).value and not bubble:
            # A bubble sheet always carries its QR, so the flag is redundant there.
            argv.append("--qr")

        # Copy count / scrambling, both bubble-sheet only. Same rule as every
        # other disclosure here: with the section hidden its fields are ignored
        # entirely, so a number left behind cannot silently apply.
        # Scrambling, and the sheet count that only exists under it. Same rule
        # as every other disclosure here: with the section hidden its fields
        # are ignored entirely, so a number left behind cannot silently apply.
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
                # Both would decide how many sheets to print; say so rather
                # than silently picking one.
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

        # The CLI reports questions it could not put on a bubble sheet on
        # stdout, which nothing in the TUI would otherwise show. That warning
        # is the one genuinely dangerous thing here: an instructor who prints a
        # sheet missing the essay question only finds out after the exam has
        # been sat. Capture it and put it in front of them.
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
        """Reflect a folder's saved policy when its path is entered by hand."""
        if event.input.id == "policy-submissions":
            self._sync_policy_checkbox()

    def _sync_policy_checkbox(self) -> None:
        """Set the lock checkbox from the folder's current on-disk policy.

        Keeps the teacher from silently flipping a folder's state: selecting a
        folder shows what it's already set to before they apply a change.
        """
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
        # Every field the policy file holds is written here, from a form that
        # was filled in from the file on disk. A `GradingPolicy` built from a
        # subset would quietly reset whatever it left out — applying an
        # anonymity change would turn randomized order back off.
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
        """Normalize path strings by removing quotes and resolving shell-style escapes."""
        p = path_str.strip()
        if not p:
            return ""
        # Strip both single and double quotes often included when copying paths
        p = p.strip("'\"")
        # Handle shell-style escaped spaces and expand home directory
        return str(Path(p.replace("\\ ", " ")).expanduser())