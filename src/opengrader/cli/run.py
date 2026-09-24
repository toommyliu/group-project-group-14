"""Run the scan-check, split, extract, and grading stages."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, BooleanOptionalAction, RawDescriptionHelpFormatter
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
import shlex
from uuid import uuid4

from opengrader.core import app_config
from opengrader.core.workspace.models import AssignmentInputs
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
  uv run opengrader-run cs146-quiz1/                    # finds the scan, answer key, roster, template
  uv run opengrader-run cs146-quiz1/ --qa other-key.md  # any flag overrides what was found
  uv run opengrader-run --scan scans/bundle.pdf --qa qa.md --out cohort/
  uv run opengrader-run --scan scans/bundle.pdf --qa qa.md --out cohort/ --pages-per-submission 6
  uv run opengrader-run --scan split/ --qa qa.md --out cohort/          # already-split PDFs
  uv run opengrader-run --scan scans/bundle.pdf --out cohort/ --dry-run # show the plan, do nothing
  uv run opengrader-run --scan scans/bundle.pdf --qa qa.md --out cohort/ --llm --auto-ollama
  uv run opengrader-run --scan scans/bundle.pdf --qa qa.md --out cohort/ --json   # for scripts

class folder:
  Give a folder instead of --scan/--out and its inputs are found for you: a
  markdown answer key that parses, a tab-separated roster, a PDF named
  *template* or *blank*, and the remaining PDF as the scan (or a subfolder of
  PDFs when there is no bundle). Two files that could both be the scan or the
  answer key stop the run and are named; two rosters or templates are left
  out with a warning. The folder doubles as --out.

resuming:
  Re-run the same command. Nothing is repeated that does not need to be, and the
  summary says which stages were reused. If inputs change, use --fresh to process
  them as a new assignment while preserving previous grades.

what it builds under --out:
  split/                       per-student PDFs (skipped when --scan is a directory)
  submissions/                 the *.submission.md folder for file utilities
  .opengrader/run-ledger.jsonl the stage ledger that makes a re-run resumable

With an answer key, completed results are published to the assignment's workspace.
Re-runs preserve saved grades.
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
    ai = app_config.resolve()
    parser = ArgumentParser(
        description=(
            "Run the whole pre-grading pipeline — scan check, split, extract, "
            "auto-grade — with one command. Re-runs resume instead of repeating."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "folder",
        nargs="?",
        default=None,
        metavar="CLASS_FOLDER",
        help=(
            "A class folder holding the scan and, optionally, the answer key, "
            "roster and blank template — each is found automatically, and any "
            "flag below overrides what was found. Also the --out folder unless "
            "--out is given."
        ),
    )
    parser.add_argument(
        "--scan",
        default=None,
        help=(
            "The bulk scan PDF, or a directory of already-split per-student "
            "PDFs (in which case the split stage is skipped). Required without "
            "a class folder."
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        help=(
            "Cohort folder to build: split/, submissions/ and the run ledger. "
            "Required without a class folder."
        ),
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Workspace to receive processed submissions. Defaults to the folder's nearest workspace.",
    )
    parser.add_argument("--assignment", help="Use this assignment ID in the workspace.")
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
            "resolves its own default from the AI Setup settings (saved setting "
            "> built-in default) — set it there once instead of "
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
        help="Redo every stage; changed inputs create a new assignment and preserve prior grades.",
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
        help="Print the report as JSON instead of prose.",
    )
    return parser


def _build_request(
    args, ai: "app_config.ResolvedAI | None" = None
) -> PipelineRequest:
    resolved_ai = ai if ai is not None else app_config.resolve()
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

    blocked = next((o for o in report.stages if o.status is StageStatus.BLOCKED), None)
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
        if (
            report.qa_file is not None
            and report.assignment_id is not None
            and report.workspace is not None
        ):
            print(
                "Open for grading: "
                + shlex.join(
                    [
                        "uv",
                        "run",
                        "opengrader",
                        "tui",
                        "--workspace",
                        str(report.workspace),
                        "--assignment",
                        report.assignment_id,
                    ]
                )
            )
        else:
            print(
                "Add --qa <answer-key.md> and re-run to make these submissions available for grading in the workspace."
            )


def _report_to_dict(report: PipelineReport) -> dict[str, object]:
    return {
        "ok": report.ok,
        "ready_to_grade": report.ready_to_grade,
        "submission_count": report.submission_count,
        "submissions_dir": str(report.submissions_dir),
        "qa_file": str(report.qa_file) if report.qa_file is not None else None,
        "workspace": str(report.workspace) if report.workspace is not None else None,
        "assignment_id": report.assignment_id,
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


_FOLDER_ROLE_FLAGS = (
    ("scan", "scan", "--scan"),
    ("qa", "qa", "--qa"),
    ("roster", "roster", "--roster"),
    ("template", "blank_template", "--blank-template"),
)

# Guessing either of these wrong grades the wrong thing; a missing roster or
# template only makes the run less precise, and the summary says so.
_BLOCKING_ROLES = frozenset({"scan", "qa"})


def _resolve_class_folder(parser: ArgumentParser, args, *, stream) -> None:
    if args.folder is None:
        if not args.scan or not args.out:
            parser.error("give a class folder, or both --scan and --out")
        return

    from opengrader.core.class_folder import (
        ROLE_LABELS,
        describe_class_folder,
        scan_class_folder,
    )

    try:
        found = scan_class_folder(args.folder)
    except NotADirectoryError as exc:
        parser.error(str(exc))

    stream.write(f"Class folder {found.folder}\n")
    for line in describe_class_folder(found):
        stream.write(f"  {line}\n")

    blocking: list[str] = []
    for role, dest, flag in _FOLDER_ROLE_FLAGS:
        given = getattr(args, dest)
        if given:
            stream.write(f"  = {ROLE_LABELS[role]:<15} {given} (from {flag})\n")
            continue
        ambiguity = found.ambiguity(role)
        if ambiguity is not None:
            if role in _BLOCKING_ROLES:
                names = ", ".join(str(path) for path in ambiguity.candidates)
                blocking.append(f"{flag} (one of: {names})")
            else:
                stream.write(
                    f"  ! {ROLE_LABELS[role]} not used — pass {flag} to choose one\n"
                )
            continue
        path = found.found(role)
        if path is not None:
            setattr(args, dest, str(path))
    stream.write("\n")
    stream.flush()

    if blocking:
        parser.error(
            "more than one file in the class folder fits — choose with "
            + "; ".join(blocking)
        )
    if not args.scan:
        parser.error(
            f"no scanned bundle or folder of split PDFs found in {found.folder} "
            "— pass --scan"
        )
    if not args.out:
        args.out = str(found.folder)


def _publish_results(
    report: PipelineReport, root: Path, assignment_id: str
) -> PipelineReport:
    from opengrader.core.workspace.models import PipelineOutput, PipelinePublication
    from opengrader.server.workspace.client import WorkspaceClient, ensure_service

    try:
        if report.qa_file is None:
            raise ValueError("An answer key is required to publish submissions.")
        output = PipelineOutput(
            qa_markdown=report.qa_file.read_text(encoding="utf-8"),
            submissions_dir=str(report.submissions_dir.resolve()),
        )
        client = WorkspaceClient(ensure_service(root))
        result = PipelinePublication.model_validate_json(
            client.request(
                "POST",
                f"/api/assignments/{assignment_id}/imports",
                output,
                timeout=300,
            )
        )
    except (OSError, ValueError, RuntimeError) as exc:
        outcome = StageOutcome(
            stage="publish",
            status=StageStatus.FAILED,
            message=f"Could not publish results: {exc}. Re-run the same command to retry.",
        )
        return replace(report, stages=(*report.stages, outcome))
    outcome = StageOutcome(
        stage="publish",
        status=StageStatus.RAN,
        message=f"{result.submission_count} submission(s) available in {result.assignment.title}.",
    )
    return replace(
        report,
        stages=(*report.stages, outcome),
        workspace=root,
        assignment_id=result.assignment.id,
    )


def _run_target(parser: ArgumentParser, args) -> Path:
    from opengrader.core.workspace.context import (
        assignment_for_directory,
        inputs_for_assignment,
        resolve_path,
    )
    from opengrader.core.workspace.store import Workspace, discover_workspace

    if args.folder:
        return Path(args.folder).expanduser().resolve()
    if args.scan or args.out:
        if not args.scan or not args.out:
            parser.error("give a class folder, or both --scan and --out")
        return Path(args.out).expanduser().resolve()
    root = discover_workspace(Path.cwd())
    if root is not None:
        workspace = Workspace(root)
        try:
            assignment_id = assignment_for_directory(
                workspace,
                Path.cwd(),
                assignment_id=args.assignment,
                include_pending=True,
            )
        except ValueError as exc:
            parser.error(str(exc))
        if assignment_id is not None:
            saved = inputs_for_assignment(workspace, assignment_id)
            if saved is not None:
                return resolve_path(root, saved.folder)
    return Path.cwd().resolve()


def _fill_saved_inputs(args, workspace_root: Path, target: Path) -> None:
    from opengrader.core.workspace.context import resolve_path
    from opengrader.core.workspace.store import DATABASE, Workspace

    if not (workspace_root / DATABASE).is_file():
        return
    saved = next(
        (
            item
            for item in Workspace(workspace_root).assignment_inputs()
            if resolve_path(workspace_root, item.folder) == target
        ),
        None,
    )
    if saved is None:
        return
    for key in ("scan", "output", "qa", "roster", "blank_template"):
        dest = "out" if key == "output" else key
        value = getattr(args, dest)
        stored = getattr(saved, key)
        if not value and stored:
            setattr(args, dest, str(resolve_path(target, stored)))
    if args.pages_per_submission is None:
        args.pages_per_submission = saved.pages_per_submission


def _save_inputs(
    args, root: Path, target: Path
) -> tuple[AssignmentInputs, str | None]:
    from opengrader.core.workspace.context import input_state, resolve_path, stored_path
    from opengrader.core.workspace.store import DATABASE, Workspace

    workspace = Workspace(root) if (root / DATABASE).is_file() else Workspace.create(root)
    saved = next(
        (
            item for item in workspace.assignment_inputs()
            if resolve_path(root, item.folder) == target
        ),
        None,
    )
    if saved is not None and args.assignment and args.assignment != saved.id:
        raise ValueError("This folder belongs to another assignment.")
    assignment_id = (
        saved.id if saved is not None else args.assignment or workspace.assignment_id_for_folder(target)
    )
    inputs = AssignmentInputs(
        id=assignment_id,
        folder=stored_path(root, target),
        scan=stored_path(target, Path(args.scan)),
        output=stored_path(target, Path(args.out)),
        submissions=stored_path(target, Path(args.out) / "submissions"),
        qa=stored_path(target, Path(args.qa)) if args.qa else None,
        roster=stored_path(target, Path(args.roster)) if args.roster else None,
        blank_template=(
            stored_path(target, Path(args.blank_template))
            if args.blank_template else None
        ),
        pages_per_submission=args.pages_per_submission,
        input_state=input_state({
            "scan": Path(args.scan),
            "qa": Path(args.qa) if args.qa else None,
            "roster": Path(args.roster) if args.roster else None,
            "blank_template": Path(args.blank_template) if args.blank_template else None,
        }),
    )
    changed = saved is not None and inputs != saved
    published = saved is not None and any(
        assignment.id == saved.id for assignment in workspace.assignments()
    )
    if changed and published:
        if not args.fresh:
            raise ValueError(
                "Assignment inputs changed. Pass --fresh to process them as a new assignment; existing grades stay in the previous assignment."
            )
        return inputs.model_copy(update={"id": str(uuid4())}), saved.id
    workspace.save_assignment_inputs(inputs)
    return inputs, None


def run_cli(argv: list[str] | None = None) -> PipelineReport | None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    from opengrader.core.workspace.store import discover_workspace

    try:
        target = _run_target(parser, args)
        root = (
            args.workspace.expanduser().resolve()
            if args.workspace is not None
            else discover_workspace(target) or target
        )
        _fill_saved_inputs(args, root, target)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.folder is None and not args.scan:
        args.folder = str(target)
    _resolve_class_folder(
        parser, args, stream=sys.stderr if args.as_json else sys.stdout
    )
    request = _build_request(args)

    if args.dry_run:
        _print_plan(request)
        return None

    try:
        inputs, previous_id = _save_inputs(args, root, target)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    narrator = _Narrator(stream=sys.stderr if args.as_json else sys.stdout)
    if args.as_json:
        with redirect_stdout(sys.stderr):
            report = run_pipeline(request, progress=narrator.as_progress())
    else:
        report = run_pipeline(request, progress=narrator.as_progress())

    report = replace(report, workspace=root)
    if report.ok and report.ready_to_grade and request.qa_file is not None:
        narrator.status("Publishing results to the workspace.")
        report = _publish_results(report, root, inputs.id)
    if report.ok and report.assignment_id == inputs.id and previous_id is not None:
        from opengrader.core.workspace.store import Workspace

        Workspace(root).replace_assignment_inputs(previous_id, inputs)

    if args.as_json:
        print(json.dumps(_report_to_dict(report), indent=2))
    else:
        _print_summary(report)

    return report


def main(argv: list[str] | None = None) -> int:
    report = run_cli(argv)
    return 0 if report is None else exit_code(report)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
