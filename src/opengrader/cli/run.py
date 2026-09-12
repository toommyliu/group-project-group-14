"""Run the scan-check, split, extract, and grading stages."""

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
    ai = ai_config.resolve()
    parser = ArgumentParser(
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
    resolved_ai = ai if ai is not None else ai_config.resolve()
    return PipelineRequest(
        scan=Path(args.scan),
        output_dir=Path(args.out),
        qa_file=Path(args.qa) if args.qa else None,
        roster=Path(args.roster) if args.roster else None,
        blank_template=Path(args.blank_template) if args.blank_template else None,
        pages_per_submission=args.pages_per_submission,
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
    if any(outcome.status is StageStatus.BLOCKED for outcome in report.stages):
        return 2
    return 0 if report.ok else 1


def run_cli(argv: list[str] | None = None) -> PipelineReport | None:
    args = _build_parser().parse_args(argv)
    request = _build_request(args)

    if args.dry_run:
        _print_plan(request)
        return None

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

    open_tui = args.tui if args.tui is not None else sys.stdout.isatty()
    if open_tui and not args.as_json and report.ok and report.ready_to_grade:
        _open_tui(report)

    return report


def _open_tui(report: PipelineReport) -> None:
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
    report = run_cli(argv)
    return 0 if report is None else exit_code(report)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
