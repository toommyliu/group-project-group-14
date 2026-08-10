"""Modal course gradebook — a whole semester, from the start-up screen.

Every other screen in the app is scoped to one assignment, because that is the
unit you grade. A course is not: it spans folders this session was never opened
with. That is exactly why this lives in the start-up screen's Helper Tools and
**not** behind a workspace hotkey — a course report is not a report about the
assignment in front of you, and putting it next to the ones that are would
promise a connection that isn't there.

It is a thin front end over the `course-grades` CLI, run in a thread worker with
its output in a log pane, matching the Canvas / return-bundle / calibration
modals. Strictly read-only with respect to grades: it never writes into an
assignment folder, and writes at all only when a CSV path is given.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, RichLog, Select, Static

from opengrader.cli.course_grades import run_cli as run_course_grades_cli
from opengrader.core.course import COURSE_FILENAME
from opengrader.tui.ui.screens.course_builder_screen import CourseBuilderScreen
from opengrader.tui.ui.screens.path_picker_screen import PathPickerScreen

# What the report should lead with. These map onto the CLI's flags rather than
# inventing a second vocabulary, so what the screen shows and what a scripted
# run shows can never drift.
VIEW_EVERYTHING = "everything"
VIEW_SUMMARY = "summary"
VIEW_AT_RISK = "at-risk"
VIEW_OUTCOMES = "outcomes"

_VIEW_OPTIONS: list[tuple[str, str]] = [
    ("Everything (grades, at-risk, outcomes)", VIEW_EVERYTHING),
    ("Summary only", VIEW_SUMMARY),
    ("At-risk students", VIEW_AT_RISK),
    ("Learning outcomes", VIEW_OUTCOMES),
]


class CourseGradesScreen(ModalScreen[None]):
    """Weighted course grades, at-risk students, and outcome mastery."""

    DEFAULT_CSS = """
    CourseGradesScreen {
        align: center middle;
    }

    #course-grades-dialog {
        width: 88%;
        max-width: 120;
        height: 88%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #course-grades-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    CourseGradesScreen .field-label {
        width: 100%;
        height: auto;
        margin-top: 1;
    }

    CourseGradesScreen .path-row {
        height: auto;
    }

    CourseGradesScreen .path-row Input {
        width: 1fr;
    }

    CourseGradesScreen .path-row Button {
        min-width: 10;
        width: auto;
        margin-left: 1;
        margin-top: 1;
    }

    #course-grades-log {
        height: 1fr;
        margin-top: 1;
        border: round $surface-lighten-2;
        border-title-align: left;
        border-title-color: $text-muted;
        background: transparent;
        padding: 0 1;
    }

    #course-grades-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #course-grades-view {
        width: 42;
        margin-right: 1;
    }

    #course-grades-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Close")]

    def __init__(self, *, course_path: str = "") -> None:
        super().__init__()
        self._course_path = course_path

    @staticmethod
    def _field_label(text: str, detail: str = "") -> Static:
        markup = f"[b]{text}[/b]"
        if detail:
            markup = f"{markup}  [dim]{detail}[/dim]"
        return Static(markup, classes="field-label")

    def compose(self) -> ComposeResult:
        with Vertical(id="course-grades-dialog") as dialog:
            dialog.border_title = "▸ Course gradebook"
            yield Static(
                "Roll a whole semester of assignment folders into one grade per "
                "student: weighted totals, letter grades, who is at risk, and "
                "learning-outcome mastery across the course. Nothing is written "
                "into an assignment folder.",
                id="course-grades-hint",
            )
            yield self._field_label(
                "Course file", f"a {COURSE_FILENAME} listing your assignments and weights"
            )
            with Horizontal(classes="path-row"):
                yield Input(
                    value=self._course_path,
                    placeholder=f"Path to {COURSE_FILENAME}…",
                    id="course-grades-path",
                )
                yield Button("Browse", id="course-grades-browse", compact=True)
                yield Button("New…", id="course-grades-new", compact=True)
            yield self._field_label(
                "Save a CSV", "optional — one row per student, one column per assignment"
            )
            with Horizontal(classes="path-row"):
                yield Input(
                    placeholder="Leave blank to only show the report…",
                    id="course-grades-csv",
                )
                yield Button("Browse", id="course-grades-csv-browse", compact=True)
            log = RichLog(id="course-grades-log", wrap=True, auto_scroll=False)
            log.border_title = "Report"
            yield log
            with Horizontal(id="course-grades-buttons"):
                yield Select(
                    _VIEW_OPTIONS,
                    value=VIEW_EVERYTHING,
                    allow_blank=False,
                    id="course-grades-view",
                )
                yield Button(
                    "Build report", variant="primary", id="course-grades-run", compact=True
                )
                yield Button("Close", id="course-grades-close", compact=True)

    def on_mount(self) -> None:
        self.query_one("#course-grades-log", RichLog).write(
            f"Point this at a {COURSE_FILENAME} listing your assignment folders "
            "and their weights:\n\n"
            '  name = "CS 146 — Spring 2026"\n\n'
            "  [[assignment]]\n"
            '  name = "Quiz 1"\n'
            '  path = "quiz-1/student-submissions"\n'
            "  weight = 20\n"
            '  solutions = "quiz-1/solutions.md"   # optional, adds outcomes\n\n'
            "Weights are relative, so 20/80 and 1/4 mean the same thing. An "
            "assignment a student has no graded work for is left out of their "
            'average until you set missing = "zero".'
        )
        self.query_one("#course-grades-path", Input).focus()

    # ── browsing ─────────────────────────────────────────────────────────────

    @on(Button.Pressed, "#course-grades-browse")
    def _on_browse_course(self, event: Button.Pressed) -> None:
        event.stop()
        # "any" rather than "file": a course folder is a perfectly good answer,
        # and the CLI resolves a directory to the course.toml inside it.
        self._browse_into("#course-grades-path", f"Choose a {COURSE_FILENAME}", "any")

    @on(Button.Pressed, "#course-grades-new")
    def _on_new_course_file(self, event: Button.Pressed) -> None:
        """Build a course file from form fields instead of hand-writing TOML.

        The written path is dropped straight into the course field, so the
        instructor lands back here one click from their first report rather
        than having to find the file they just made.
        """
        event.stop()

        def _apply(written: Path | None) -> None:
            if written is None:
                return
            field = self.query_one("#course-grades-path", Input)
            field.value = str(written)
            field.cursor_position = len(field.value)

        self.app.push_screen(
            CourseBuilderScreen(start_dir=self._start_dir_for_builder()), _apply
        )

    def _start_dir_for_builder(self) -> str:
        """Where the builder should start looking — the course field's folder."""
        raw = self.query_one("#course-grades-path", Input).value.strip().strip("'\"")
        if not raw:
            return ""
        candidate = Path(raw).expanduser()
        return str(candidate if candidate.is_dir() else candidate.parent)

    @on(Button.Pressed, "#course-grades-csv-browse")
    def _on_browse_csv(self, event: Button.Pressed) -> None:
        event.stop()
        self._browse_into("#course-grades-csv", "Choose where to save the CSV", "any")

    def _browse_into(self, selector: str, title: str, mode: str) -> None:
        target = self.query_one(selector, Input)
        start_path: Path | None = None
        current = target.value.strip().strip("'\"")
        if current:
            candidate = Path(current).expanduser()
            if candidate.is_dir():
                start_path = candidate
            elif candidate.parent.is_dir():
                start_path = candidate.parent

        def _apply(path: Path | None) -> None:
            if path is None:
                return
            target.value = str(path)
            target.cursor_position = len(target.value)

        self.app.push_screen(
            PathPickerScreen(title=title, mode=mode, start_path=start_path), _apply
        )

    # ── running ──────────────────────────────────────────────────────────────

    def _build_argv(self) -> list[str] | None:
        course = self.query_one("#course-grades-path", Input).value.strip().strip("'\"")
        if not course:
            self.app.notify(
                f"Choose a {COURSE_FILENAME} (or the folder holding one).",
                severity="error",
            )
            return None
        argv = ["--course", course]

        view = self.query_one("#course-grades-view", Select).value
        # The at-risk list and outcomes print unasked when non-empty, so the
        # narrower views only ever *add* a flag — never suppress something the
        # instructor would have seen anyway.
        if view == VIEW_SUMMARY:
            argv.append("--summary")
        elif view == VIEW_AT_RISK:
            argv += ["--summary", "--at-risk"]
        elif view == VIEW_OUTCOMES:
            argv += ["--summary", "--outcomes"]

        csv_path = self.query_one("#course-grades-csv", Input).value.strip().strip("'\"")
        if csv_path:
            argv += ["--csv", csv_path]
        return argv

    @on(Button.Pressed, "#course-grades-run")
    def _on_run_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        argv = self._build_argv()
        if argv is None:
            return
        self.query_one("#course-grades-run", Button).disabled = True
        log = self.query_one("#course-grades-log", RichLog)
        log.clear()
        log.write("Reading assignment folders…")
        self.run_worker(lambda: self._run_report(argv), thread=True, exclusive=True)

    def _run_report(self, argv: list[str]) -> None:
        """Worker thread: run the CLI with its output captured.

        stderr is captured into the same buffer as stdout on purpose — the CLI
        sends per-assignment load failures there, and those are exactly the
        caveats a reader of the numbers must not miss.
        """
        buffer = io.StringIO()
        succeeded = True
        try:
            with redirect_stdout(buffer), redirect_stderr(buffer):
                run_course_grades_cli(argv)
        except SystemExit as exc:
            succeeded = exc.code in (0, None)
        except Exception as exc:  # surface unexpected failures in the log
            succeeded = False
            buffer.write(f"\nerror: {exc}")
        self.app.call_from_thread(self._show_result, buffer.getvalue(), succeeded)

    def _show_result(self, output: str, succeeded: bool) -> None:
        if not self.is_attached:
            return
        self.query_one("#course-grades-run", Button).disabled = False
        log = self.query_one("#course-grades-log", RichLog)
        log.clear()
        log.write(output.strip() or "(no output)")
        if succeeded:
            self.app.notify("Course report built.")
        else:
            self.app.notify(
                "Could not build the course report — see the log.", severity="error"
            )

    # ── leaving ──────────────────────────────────────────────────────────────

    @on(Button.Pressed, "#course-grades-close")
    def _on_close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
