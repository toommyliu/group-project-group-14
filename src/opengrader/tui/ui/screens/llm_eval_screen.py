from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, RichLog, Select, Static, TextArea

from opengrader.cli.llm_eval import run_cli as run_llm_eval_cli
from opengrader.core.grading.evaluation import (
    DEFAULT_TARGET_AGREEMENT,
    LlmEvalReport,
)
from opengrader.core.llm import OllamaConfig
from opengrader.tui.ui.screens.path_picker_screen import PathPickerScreen

SOURCE_THIS = "this"
SOURCE_OTHER = "other"

_SOURCE_OPTIONS: list[tuple[str, str]] = [
    ("This assignment", SOURCE_THIS),
    ("Another graded assignment…", SOURCE_OTHER),
]


class LlmEvalScreen(ModalScreen[float | None]):
    DEFAULT_CSS = """
    LlmEvalScreen {
        align: center middle;
    }

    #llm-eval-dialog {
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

    #llm-eval-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #llm-eval-fields {
        height: auto;
        max-height: 40%;
    }

    LlmEvalScreen .field-label {
        width: 100%;
        height: auto;
        margin-top: 1;
    }

    LlmEvalScreen .path-row {
        height: auto;
    }

    LlmEvalScreen .path-row Input {
        width: 1fr;
    }

    LlmEvalScreen .path-row Button {
        min-width: 10;
        width: auto;
        margin-left: 1;
        margin-top: 1;
    }
    #llm-eval-other-source {
        height: auto;
    }

    #llm-eval-tuning {
        height: auto;
    }

    #llm-eval-tuning Input {
        width: 1fr;
        margin-right: 1;
    }

    #llm-eval-classes {
        height: 5;
        margin-top: 1;
    }

    #llm-eval-classes-browse {
        margin-top: 1;
    }

    #llm-eval-log {
        height: 1fr;
        margin-top: 1;
        border: round $surface-lighten-2;
        border-title-align: left;
        border-title-color: $text-muted;
        background: transparent;
        padding: 0 1;
    }

    #llm-eval-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #llm-eval-buttons Button {
        margin-left: 1;
    }

    .hidden {
        display: none;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Close")]

    def __init__(
        self,
        *,
        solutions_path: str = "",
        submissions_path: str = "",
        apply_label: str | None = None,
    ) -> None:
        super().__init__()
        self._solutions_path = solutions_path
        self._submissions_path = submissions_path
        self._apply_label = apply_label
        self._recommended: float | None = None

    @staticmethod
    def _field_label(text: str, detail: str = "") -> Static:
        markup = f"[b]{text}[/b]"
        if detail:
            markup = f"{markup}  [dim]{detail}[/dim]"
        return Static(markup, classes="field-label")

    def _path_row(self, placeholder: str, widget_id: str, value: str = "") -> Horizontal:
        row = Horizontal(
            Input(value=value, placeholder=placeholder, id=widget_id),
            Button("Browse", id=f"{widget_id}-browse", compact=True),
            classes="path-row",
            id=f"{widget_id}-row",
        )
        return row

    def compose(self) -> ComposeResult:
        with Vertical(id="llm-eval-dialog") as dialog:
            dialog.border_title = "▸ Calibrate AI grading"
            yield Static(
                "Re-grades free responses [b]you have already marked[/b] with the "
                "model, and reports how often it agreed — per model and per "
                "confidence level. Nothing is written: this only measures. The "
                "answer is a --llm-min-confidence you can defend.",
                id="llm-eval-hint",
            )

            with VerticalScroll(id="llm-eval-fields"):
                yield self._field_label(
                    "Evaluate against", "which graded answers to measure the model on"
                )
                yield Select(
                    _SOURCE_OPTIONS,
                    value=SOURCE_THIS if self._submissions_path else SOURCE_OTHER,
                    allow_blank=False,
                    id="llm-eval-source",
                )
                with Vertical(
                    id="llm-eval-other-source",
                    classes="" if not self._submissions_path else "hidden",
                ):
                    yield Static(
                        "A finished assignment gives a much bigger sample than one "
                        "you have just started grading — and the more hand-graded "
                        "answers behind the number, the more it is worth.",
                        classes="card-hint",
                    )
                    yield self._field_label(
                        "Answer key", "QA markdown for that assignment"
                    )
                    yield self._path_row(
                        "Path to QA markdown file…", "llm-eval-solutions"
                    )
                    yield self._field_label(
                        "Submissions", "its graded .submission.md files"
                    )
                    yield self._path_row(
                        "Path to submissions directory…", "llm-eval-submissions"
                    )

                yield self._field_label(
                    "More graded classes",
                    "optional — one folder per line, all sharing the answer key above",
                )
                yield Static(
                    "Taught this exam before? Add those terms' submissions to "
                    "measure the model on every class at once. Same answer key "
                    "means same rubric and same point scale, so it is simply a "
                    "bigger sample — which is the only thing that makes a "
                    "threshold worth trusting.",
                    classes="card-hint",
                )
                yield TextArea(id="llm-eval-classes")
                yield Button(
                    "Browse & add class",
                    id="llm-eval-classes-browse",
                    compact=True,
                )

                yield self._field_label(
                    "Models to compare",
                    f"comma-separated; blank = {OllamaConfig.model}",
                )
                yield Input(
                    placeholder=f"e.g. {OllamaConfig.model}, llama3.1",
                    id="llm-eval-models",
                )

                with Horizontal(id="llm-eval-tuning"):
                    with Vertical():
                        yield self._field_label(
                            "Agreement target", "% of grades that must match yours"
                        )
                        yield Input(
                            value=f"{DEFAULT_TARGET_AGREEMENT * 100:g}",
                            placeholder="90",
                            id="llm-eval-target",
                        )
                    with Vertical():
                        yield self._field_label(
                            "Answer limit", "total across classes; blank = all"
                        )
                        yield Input(placeholder="e.g. 40", id="llm-eval-limit")

            log = RichLog(id="llm-eval-log", wrap=True, auto_scroll=True, markup=False)
            log.border_title = "Report"
            yield log

            with Horizontal(id="llm-eval-buttons"):
                if self._apply_label:
                    apply_button = Button(
                        self._apply_label,
                        variant="success",
                        id="llm-eval-apply",
                        compact=True,
                    )
                    apply_button.disabled = True
                    yield apply_button
                yield Button(
                    "Run", variant="primary", id="llm-eval-run", compact=True
                )
                yield Button("Close", id="llm-eval-close", compact=True)

    def on_mount(self) -> None:
        log = self.query_one("#llm-eval-log", RichLog)
        log.write(
            "Grade some free responses by hand first — this measures the model "
            "against them.\n\nEvery answer is one model call, so a full pass "
            "takes a while. The answer limit caps the total (spread across "
            "students and classes, not just the first few) for a quick first "
            "look."
        )
        self.query_one("#llm-eval-run", Button).focus()


    @on(Select.Changed, "#llm-eval-source")
    def _on_source_changed(self, event: Select.Changed) -> None:
        event.stop()
        self.query_one("#llm-eval-other-source").set_class(
            event.value != SOURCE_OTHER, "hidden"
        )

    @on(Button.Pressed, "#llm-eval-solutions-browse")
    def _on_browse_solutions(self, event: Button.Pressed) -> None:
        event.stop()
        self._browse_into("#llm-eval-solutions", "Choose the answer key", "file")

    @on(Button.Pressed, "#llm-eval-submissions-browse")
    def _on_browse_submissions(self, event: Button.Pressed) -> None:
        event.stop()
        self._browse_into(
            "#llm-eval-submissions", "Choose the submissions folder", "directory"
        )

    @on(Button.Pressed, "#llm-eval-classes-browse")
    def _on_browse_class(self, event: Button.Pressed) -> None:
        event.stop()
        text_area = self.query_one("#llm-eval-classes", TextArea)

        def _apply(path: Path | None) -> None:
            if path is None:
                return
            existing = text_area.text
            if existing and not existing.endswith("\n"):
                existing += "\n"
            text_area.text = f"{existing}{path}\n"

        self.app.push_screen(
            PathPickerScreen(
                title="Choose another graded class (same answer key)",
                mode="directory",
            ),
            _apply,
        )

    def _class_paths(self) -> list[str]:
        return [
            line.strip().strip("'\"")
            for line in self.query_one("#llm-eval-classes", TextArea).text.splitlines()
            if line.strip()
        ]

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


    def _resolve_paths(self) -> tuple[str, str] | None:
        source = self.query_one("#llm-eval-source", Select).value
        if source == SOURCE_THIS:
            if not (self._solutions_path and self._submissions_path):
                self.app.notify(
                    "No assignment is loaded — pick another graded assignment "
                    "instead.",
                    severity="error",
                )
                return None
            return self._solutions_path, self._submissions_path

        solutions = self.query_one("#llm-eval-solutions", Input).value.strip().strip("'\"")
        submissions = (
            self.query_one("#llm-eval-submissions", Input).value.strip().strip("'\"")
        )
        if not solutions or not submissions:
            self.app.notify(
                "An answer key and a submissions folder are both required.",
                severity="error",
            )
            return None
        return solutions, submissions

    def _build_argv(self, solutions: str, submissions: str) -> list[str] | None:
        argv = ["--solutions", solutions, "--input", submissions, "--quiet"]

        for folder in self._class_paths():
            argv += ["--input", folder]

        models = [
            model.strip()
            for model in self.query_one("#llm-eval-models", Input).value.split(",")
            if model.strip()
        ]
        for model in models:
            argv += ["--llm-model", model]

        target_raw = self.query_one("#llm-eval-target", Input).value.strip()
        if target_raw:
            try:
                target = float(target_raw.rstrip("%"))
            except ValueError:
                self.app.notify(
                    f"Agreement target '{target_raw}' is not a number.",
                    severity="error",
                )
                return None
            if not 0 < target <= 100:
                self.app.notify(
                    "Agreement target must be a percentage between 0 and 100.",
                    severity="error",
                )
                return None
            argv += ["--target-agreement", f"{target / 100:g}"]

        limit_raw = self.query_one("#llm-eval-limit", Input).value.strip()
        if limit_raw:
            try:
                limit = int(limit_raw)
            except ValueError:
                self.app.notify(
                    f"Answer limit '{limit_raw}' is not a whole number.",
                    severity="error",
                )
                return None
            if limit < 1:
                self.app.notify("Answer limit must be at least 1.", severity="error")
                return None
            argv += ["--limit", str(limit)]

        argv.append("--auto-ollama")
        return argv

    @on(Button.Pressed, "#llm-eval-run")
    def _on_run_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        paths = self._resolve_paths()
        if paths is None:
            return
        argv = self._build_argv(*paths)
        if argv is None:
            return

        self._recommended = None
        self.query_one("#llm-eval-run", Button).disabled = True
        if self._apply_label:
            self.query_one("#llm-eval-apply", Button).disabled = True
        log = self.query_one("#llm-eval-log", RichLog)
        log.clear()
        log.write("Measuring… every graded answer is sent to each model in turn.")
        self.run_worker(lambda: self._run_eval(argv), thread=True, exclusive=True)

    def _run_eval(self, argv: list[str]) -> None:
        buffer = io.StringIO()
        report: LlmEvalReport | None = None
        succeeded = True
        try:
            with redirect_stdout(buffer), redirect_stderr(buffer):
                report = run_llm_eval_cli(argv)
        except SystemExit as exc:
            succeeded = exc.code in (0, None)
        except Exception as exc:
            succeeded = False
            buffer.write(f"\nerror: {exc}")
        self.app.call_from_thread(
            self._show_result, buffer.getvalue(), report, succeeded
        )

    def _show_result(
        self, output: str, report: LlmEvalReport | None, succeeded: bool
    ) -> None:
        if not self.is_attached:
            return
        self.query_one("#llm-eval-run", Button).disabled = False
        log = self.query_one("#llm-eval-log", RichLog)
        log.clear()
        log.write(output.strip() or "(no output)")

        best = report.best_model if report is not None else None
        self._recommended = best.recommendation.threshold if best is not None else None

        if self._recommended is not None:
            if self._apply_label:
                apply_button = self.query_one("#llm-eval-apply", Button)
                apply_button.label = f"{self._apply_label} ({self._recommended:.2f})"
                apply_button.disabled = False
            self.app.notify(
                f"Recommended --llm-min-confidence {self._recommended:.2f} "
                f"({best.model})."
            )
        elif not succeeded:
            self.app.notify("Calibration could not run — see the report.", severity="error")
        else:
            self.app.notify(
                "No confidence cutoff was trustworthy enough — see the report.",
                severity="warning",
            )


    @on(Button.Pressed, "#llm-eval-apply")
    def _on_apply_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(self._recommended)

    @on(Button.Pressed, "#llm-eval-close")
    def _on_close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
