"""One command from a stack of scans to a gradeable cohort.

    uv run opengrader-run --scan bundle.pdf --qa qa.md --out cohort/

Runs ``scan-check → split → extract → auto-grade`` in order and then opens the
TUI on what it built. Re-run the identical command after an interruption, a
re-scan or a change of mind and each stage consults the run ledger: the work
that still stands is **reused**, not repeated.

This is a thin front end. Every decision — what each stage does, what it may
skip, what stops the run — lives in ``core/pipeline.py``; everything here is
argument parsing and how the run is narrated to somebody watching a terminal.
That split is deliberate: the TUI drives the same engine and must not have to
re-derive any of it from printed text.

exit codes:
  0  everything that could run, ran
  1  a stage failed
  2  the scan pre-flight blocked the run — a decision is needed (the same
     meaning `grade-check --fail-on-blocking` gives 2)
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, BooleanOptionalAction, RawDescriptionHelpFormatter
from contextlib import redirect_stdout
from pathlib import Path

from opengrader.core import ai_config
from opengrader.core.pipeline import (
    PIPELINE_STAGES,
    LlmOptions,
    PipelineProgress,
    PipelineReport,
    PipelineRequest,
    StageOutcome,
    StageStatus,
    plan_pipeline,
    run_pipeline,
)

EPILOG = """
examples:
  uv run opengrader-run --scan scans/bundle.pdf --qa qa.md --out cohort/
  uv run opengrader-run --scan scans/bundle.pdf --qa qa.md --out cohort/ --pages-per-submission 6
  uv run opengrader-run --scan split/ --qa qa.md --out cohort/          # already-split PDFs
  uv run opengrader-run --scan scans/bundle.pdf --out cohort/ --dry-run # show the plan, do nothing
  uv run opengrader-run --scan scans/bundle.pdf --qa qa.md --out cohort/ --llm --auto-ollama
  uv run opengrader-run --scan scans/bundle.pdf --qa qa.md --out cohort/ --json   # for scripts

resuming:
  Re-run the same command. Nothing is repeated that does not need to be, and the
  summary says which stages were reused. --fresh ignores the ledger entirely.

what it builds under --out:
  split/                       per-student PDFs (skipped when --scan is a directory)
  submissions/                 the *.submission.md folder the TUI opens
  .opengrader/run-ledger.jsonl the stage ledger that makes a re-run resumable
"""

# How each status reads in the summary. The words are the point of the whole
# feature — "reused" is what teaches a user that re-running is cheap — so they
# are spelled out rather than borrowed from the enum's identifiers.
_STATUS_LABEL = {
    StageStatus.RAN: "ran",
    StageStatus.REUSED: "reused",
    StageStatus.SKIPPED: "skipped",
    StageStatus.BLOCKED: "BLOCKED",
    StageStatus.FAILED: "FAILED",
}

_STATUS_GLYPH = {
    StageStatus.RAN: "✓",
    StageStatus.REUSED: "↺",
    StageStatus.SKIPPED: "·",
    StageStatus.BLOCKED: "●",
    StageStatus.FAILED: "✗",
}


class _Narrator:
    """Prints the run as it happens, for somebody watching rather than logging.

    The per-answer ticks are the awkward part: on a class of two hundred they
    are tens of thousands of events, and printing a line each would bury every
    status message the stages emit. So they are collapsed into a single
    carriage-returned counter, and only when stdout is a terminal — redirected
    to a file, ``\\r`` produces one unreadable mega-line instead of a progress
    bar, so a non-TTY simply gets the status lines and nothing else.
    """

    def __init__(self, *, stream=None) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._interactive = bool(getattr(self._stream, "isatty", lambda: False)())
        self._ticks = 0
        self._tick_open = False

    def as_progress(self) -> PipelineProgress:
        return PipelineProgress(
            on_stage_start=self.stage_start,
            on_stage_finish=self.stage_finish,
            on_status=self.status,
            on_file_start=self.file_start,
            on_response=self.response if self._interactive else None,
        )

    def stage_start(self, stage: str) -> None:
        self._ticks = 0
        self._write(f"\n▸ {stage}\n")

    def stage_finish(self, outcome: StageOutcome) -> None:
        self._close_ticks()
        glyph = _STATUS_GLYPH.get(outcome.status, "·")
        seconds = (
            f"  ({outcome.duration_seconds:.1f}s)"
            if outcome.duration_seconds >= 0.05
            else ""
        )
        self._write(f"  {glyph} {outcome.message}{seconds}\n")
        if outcome.status in (StageStatus.BLOCKED, StageStatus.FAILED) and outcome.detail:
            for line in outcome.detail.splitlines():
                self._write(f"    {line}\n")

    def status(self, message: str) -> None:
        self._close_ticks()
        self._write(f"    {message}\n")

    def file_start(self, name: str, count: int) -> None:
        self._close_ticks()
        self._write(f"    {name} ({count})\n")

    def response(self, name: str, item: str) -> None:
        self._ticks += 1
        self._tick_open = True
        self._write(f"\r      {self._ticks} answer(s) — {item}\033[K")

    def _close_ticks(self) -> None:
        if self._tick_open:
            self._write("\n")
            self._tick_open = False

    def _write(self, text: str) -> None:
        self._stream.write(text)
        self._stream.flush()


def _build_parser() -> ArgumentParser:
    # Resolved once, here, not at import time: a teacher can change the saved
    # AI Setup switches at any time, and reading them only when the parser is
    # actually built means every invocation picks up whatever is current,
    # while an explicit CLI flag below still wins — the same reasoning every
    # other CLI in this package already follows.
    ai = ai_config.resolve()
    parser = ArgumentParser(
        # Deliberately no `prog=`: argparse then names whatever was
        # actually invoked — the standalone `opengrader-run` script, or
        # `opengrader run` when the front-door command dispatched
        # here (cli/main.py patches sys.argv[0] for exactly this). A
        # hardcoded name would tell a user to fix a command they never ran.
        description=(
            "Run the whole pre-grading pipeline — scan check, split, extract, "
            "auto-grade — with one command. Re-runs resume instead of repeating."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--scan",
        required=True,
        help=(
            "The bulk scan PDF, or a directory of already-split per-student "
            "PDFs (in which case the split stage is skipped)."
        ),
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Cohort folder to build: split/, submissions/ and the run ledger.",
    )
    parser.add_argument(
        "--qa",
        default=None,
        help=(
            "Answer key markdown. Without it the run stops after extraction and "
            "auto-grading is skipped."
        ),
    )
    parser.add_argument(
        "--roster",
        default=None,
        help="Tab-separated roster (ID<TAB>NAME); names and IDs are matched to it.",
    )
    parser.add_argument(
        "--blank-template",
        metavar="PDF",
        default=None,
        help=(
            "Blank copy of the assignment. Supplies the question layout to the "
            "extractor and the true page sequence to the split stage."
        ),
    )
    parser.add_argument(
        "--pages-per-submission",
        type=int,
        default=None,
        help=(
            "Pages each student's submission has. Also what lets the pre-flight "
            "check page order and page count at all."
        ),
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="Extraction worker processes (default: the extractor's own choice).",
    )
    parser.add_argument(
        "--llm",
        action=BooleanOptionalAction,
        default=None,
        help=(
            "Let a local model help: it is asked about handwriting Tesseract "
            "read badly, and about free responses the rubric cannot settle. "
            "Deliberately tri-state: left absent, reading handwriting and "
            "suggesting grades each follow their own AI Setup switch (they are "
            "genuinely different decisions); given as --llm/--no-llm, it is a "
            "blunt override for both, which is what a script or a CI run "
            "wants. (unset default: follows AI Setup)"
        ),
    )
    parser.add_argument(
        "--llm-vision-model",
        default="",
        help=(
            "Ollama vision model for transcription. Left blank, each stage "
            "resolves its own default from the AI Setup settings (settings > "
            "environment > built-in default) — set it there once instead of "
            "passing it here on every run."
        ),
    )
    parser.add_argument(
        "--llm-text-model",
        default="",
        help=(
            "Ollama chat model for grading free responses. Left blank, each "
            "stage resolves its own default from the AI Setup settings, same as "
            "--llm-vision-model above."
        ),
    )
    parser.add_argument(
        "--ollama-host",
        default="",
        help=(
            "Ollama server URL. Left blank, each stage resolves its own default "
            "from the AI Setup settings, same as --llm-vision-model above."
        ),
    )
    parser.add_argument(
        "--auto-ollama",
        action=BooleanOptionalAction,
        default=ai.auto_start,
        help=(
            "Start 'ollama serve' and pull the models if they are not there. "
            "(default: %(default)s, from AI Setup)"
        ),
    )
    parser.add_argument(
        "--llm-min-confidence",
        type=float,
        default=None,
        help=(
            "Only write AI grade suggestions at or above this confidence (0-1). "
            "Run 'llm-eval' on already-graded work to find the right value."
        ),
    )
    parser.add_argument(
        "--auto-grade",
        action=BooleanOptionalAction,
        default=ai.auto_grade,
        dest="auto_grade",
        help=(
            "Run the auto-grade stage automatically once extraction finishes "
            "(default: %(default)s, from AI Setup). --no-auto-grade: extract "
            "only; leave every answer for the TUI."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Carry on past a high-severity pre-flight finding. The finding is "
            "still reported — this says you have read it and want it graded."
        ),
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore the run ledger and redo every stage from scratch.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what each stage would do, then exit. Writes nothing.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the report as JSON instead of prose. Implies --no-tui.",
    )
    parser.add_argument(
        "--tui",
        action=BooleanOptionalAction,
        default=None,
        help=(
            "Open the TUI on the finished cohort (default: on when stdout is a "
            "terminal). --no-tui is for scripts and CI."
        ),
    )
    return parser


def _build_request(
    args, ai: "ai_config.ResolvedAI | None" = None
) -> PipelineRequest:
    # Resolved here (unless a caller — a test — hands one in) rather than at
    # import time, for the same reason `_build_parser` resolves its own copy:
    # AI Setup can change between runs. Two separate resolves (one for
    # argparse defaults, one here) rather than threading one value through is
    # deliberately simple — `ai_config.resolve()` is cheap file I/O, not a
    # network call.
    resolved_ai = ai if ai is not None else ai_config.resolve()
    return PipelineRequest(
        scan=Path(args.scan),
        output_dir=Path(args.out),
        qa_file=Path(args.qa) if args.qa else None,
        roster=Path(args.roster) if args.roster else None,
        blank_template=Path(args.blank_template) if args.blank_template else None,
        pages_per_submission=args.pages_per_submission,
        # --llm is deliberately tri-state (default=None): absent, the two
        # halves follow the two separate AI Setup switches (reading
        # handwriting and suggesting grades are genuinely different
        # decisions); given, --llm/--no-llm is a blunt override for both,
        # which is what a script or a CI run wants. `LlmOptions.enabled` is
        # still `read_handwriting or grade_written`, so nothing downstream
        # broke.
        llm=LlmOptions(
            read_handwriting=(
                args.llm if args.llm is not None else resolved_ai.read_handwriting
            ),
            grade_written=(
                args.llm if args.llm is not None else resolved_ai.grade_written
            ),
            vision_model=args.llm_vision_model,
            text_model=args.llm_text_model,
            host=args.ollama_host,
            auto_ollama=args.auto_ollama,
            min_confidence=args.llm_min_confidence,
        ),
        jobs=args.jobs,
        auto_grade=args.auto_grade,
        force=args.force,
        fresh=args.fresh,
    )


def _print_plan(request: PipelineRequest) -> None:
    print("Plan (nothing has been run or written):\n")
    for plan in plan_pipeline(request):
        verb = "run" if plan.will_run else _STATUS_LABEL.get(plan.status, "skip")
        print(f"  {plan.stage:<12} {verb:<8} {plan.reason}")
        if plan.argv:
            print(f"  {'':<12} {'':<8} $ {plan.command}")
        elif plan.will_run:
            print(f"  {'':<12} {'':<8} (in process — core/submissions/scan_check.py)")
    print(f"\n  cohort folder: {request.output_dir}")


def _print_summary(report: PipelineReport) -> None:
    print("\nSummary")
    for outcome in report.stages:
        glyph = _STATUS_GLYPH.get(outcome.status, "·")
        label = _STATUS_LABEL.get(outcome.status, outcome.status.value)
        print(f"  {glyph} {outcome.stage:<12} {label:<8} {outcome.message}")

    # A stage with no outcome at all is one the run never reached. Named
    # explicitly rather than left off the list, because a summary that simply
    # stops reads as "that was everything".
    not_reached = [stage for stage in PIPELINE_STAGES if report.stage(stage) is None]
    if not_reached:
        print(f"  · {'not reached':<12} {'':<8} {', '.join(not_reached)}")

    if report.ready_to_grade:
        print(
            f"\n{report.submission_count} submission(s) ready in "
            f"{report.submissions_dir}"
        )

    blocked = next(
        (o for o in report.stages if o.status is StageStatus.BLOCKED), None
    )
    if blocked is not None:
        print(
            "\nStopped before anything was graded. Fix the scan and re-run, or "
            "add --force to grade it as it is."
        )
        return
    if not report.ok:
        print("\nThe run stopped early; the stages above are what landed.")
        return
    if report.ready_to_grade:
        print("Open it with:")
        if report.qa_file is not None:
            print(
                f"  uv run tui --qa-file {report.qa_file} "
                f"--submissions {report.submissions_dir}"
            )
        else:
            print(f"  uv run tui   # then point it at {report.submissions_dir}")


def _report_to_dict(report: PipelineReport) -> dict[str, object]:
    return {
        "ok": report.ok,
        "ready_to_grade": report.ready_to_grade,
        "submission_count": report.submission_count,
        "submissions_dir": str(report.submissions_dir),
        "qa_file": str(report.qa_file) if report.qa_file is not None else None,
        "stages": [
            {
                "stage": outcome.stage,
                "status": outcome.status.value,
                "message": outcome.message,
                "detail": outcome.detail,
                "duration_seconds": round(outcome.duration_seconds, 3),
                "outputs": list(outcome.outputs),
            }
            for outcome in report.stages
        ],
    }


def exit_code(report: PipelineReport) -> int:
    """0 clean, 2 blocked, 1 failed.

    Blocked outranks failed because it is the more actionable of the two and,
    by construction, the run stops at whichever came first — so at most one of
    them is ever present.
    """
    if any(outcome.status is StageStatus.BLOCKED for outcome in report.stages):
        return 2
    return 0 if report.ok else 1


def run_cli(argv: list[str] | None = None) -> PipelineReport | None:
    """Run the pipeline and narrate it. ``None`` for a dry run.

    Returns the report so a caller with something better to do than parse
    stdout — the TUI, a test, a future web layer — can have it. The console
    entry point is :func:`main`, which throws it away; see there for why.
    """
    args = _build_parser().parse_args(argv)
    request = _build_request(args)

    if args.dry_run:
        _print_plan(request)
        return None

    # Under --json the narration moves to stderr rather than going away: a long
    # run still shows a human what it is doing, while stdout stays a single
    # parseable document. Interleaving the two on stdout would make --json
    # useless the moment a stage said anything.
    #
    # Redirecting stdout for the run itself is not belt-and-braces: the inner
    # CLIs print some lines directly rather than only through their status
    # callbacks (``automated_grading``'s ``emit`` always prints, and the
    # extractor announces its model and PDF count), so without this a --json run
    # emits "Found 2 submission PDF(s)" ahead of the opening brace and every
    # scripted caller fails to parse it.
    narrator = _Narrator(stream=sys.stderr if args.as_json else sys.stdout)
    if args.as_json:
        with redirect_stdout(sys.stderr):
            report = run_pipeline(request, progress=narrator.as_progress())
    else:
        report = run_pipeline(request, progress=narrator.as_progress())

    if args.as_json:
        print(json.dumps(_report_to_dict(report), indent=2))
    else:
        _print_summary(report)

    # --json is a machine-readable answer; handing that caller an interactive
    # full-screen app instead of returning would be a surprise it cannot recover
    # from, so it always wins over --tui however --tui was set.
    open_tui = args.tui if args.tui is not None else sys.stdout.isatty()
    if open_tui and not args.as_json and report.ok and report.ready_to_grade:
        _open_tui(report)

    return report


def _open_tui(report: PipelineReport) -> None:
    """Hand the finished cohort to the TUI, through its own entry point.

    ``tui/main.run_app`` is called rather than ``TuiApp`` directly so this path
    goes through the same graphics-protocol negotiation every other launch does
    — that has to happen before Textual owns stdin, and duplicating it here
    would be a second place for terminal detection to be got wrong.

    Skipped without a QA file: ``run_app`` requires ``--qa-file`` and
    ``--submissions`` together, and opening the app on nothing would lose the
    folder this run just built.
    """
    if report.qa_file is None:
        return
    from opengrader.tui.main import run_app

    run_app(
        [
            "--qa-file",
            str(report.qa_file),
            "--submissions",
            str(report.submissions_dir),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    """Console entry point.

    ``console_scripts`` does ``sys.exit(main())``, so an entry point aimed
    straight at :func:`run_cli` would print the report's ``repr`` and exit 1 on
    a *successful* run — the same latent defect already fixed in ``llm-eval``,
    ``grade-check`` and ``doctor``. The report is for programmatic callers; the
    console gets an exit code.
    """
    report = run_cli(argv)
    return 0 if report is None else exit_code(report)


if __name__ == "__main__":  # pragma: no cover - manual invocation
    sys.exit(main())
