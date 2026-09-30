"""Run the scan-check, split, extract, and grading stages."""

from __future__ import annotations

import json
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass, replace
from pathlib import Path
import shlex
from uuid import uuid4

import click

from opengrader.cli import CONTEXT_SETTINGS, ai_default, resolved_ai
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

EPILOG = """\
\b
Examples:
  uv run opengrader-run cs146-quiz1/                    # finds the scan, answer key, roster, template
  uv run opengrader-run cs146-quiz1/ --qa other-key.md  # any flag overrides what was found
  uv run opengrader-run --scan scans/bundle.pdf --qa qa.md --out cohort/
  uv run opengrader-run --scan scans/bundle.pdf --qa qa.md --out cohort/ --pages-per-submission 6
  uv run opengrader-run --scan split/ --qa qa.md --out cohort/          # already-split PDFs
  uv run opengrader-run --scan scans/bundle.pdf --out cohort/ --dry-run # show the plan, do nothing
  uv run opengrader-run --scan scans/bundle.pdf --qa qa.md --out cohort/ --llm --auto-ollama
  uv run opengrader-run --scan scans/bundle.pdf --qa qa.md --out cohort/ --json   # for scripts

\b
Class folder:
  Give a folder instead of --scan/--out and its inputs are found for you: a
  markdown answer key that parses, a tab-separated roster, a PDF named
  *template* or *blank*, and the remaining PDF as the scan (or a subfolder of
  PDFs when there is no bundle). Two files that could both be the scan or the
  answer key stop the run and are named; two rosters or templates are left
  out with a warning. The folder doubles as --out.

\b
Resuming:
  Re-run the same command. Nothing is repeated that does not need to be, and the
  summary says which stages were reused. If inputs change, use --fresh to process
  them as a new assignment while preserving previous grades.

\b
What it builds under --out:
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


def _print_egress_summary(report: PipelineReport) -> None:
    """What THIS run actually sent to a cloud provider — never a lifetime total.

    Silent for a local-only run (egress_summary is set but total_calls is 0,
    or the run never reached the point where a summary could be taken).
    """
    summary = report.egress_summary
    if summary is None or not summary.total_calls:
        return
    print(f"\nSent to the cloud ({summary.total_calls} call(s)):")
    for category, count in sorted(summary.calls_by_category.items()):
        print(f"  {category:<16} {count}")
    for host in sorted(summary.calls_by_host):
        print(f"  destination: {host}")
    if summary.calls_with_tokens:
        print(
            f"  tokens: {summary.total_input_tokens} in / "
            f"{summary.total_output_tokens} out"
        )
    if report.egress_cost is not None:
        print(f"  estimated cost: ${report.egress_cost:,.2f} ({report.egress_cost_basis})")
    else:
        basis = report.egress_cost_basis or "provider pricing not stated"
        print(f"  estimated cost: could not be estimated ({basis})")


def _print_summary(report: PipelineReport) -> None:
    print("\nSummary")
    for outcome in report.stages:
        glyph = _STATUS_GLYPH.get(outcome.status, "·")
        label = _STATUS_LABEL.get(outcome.status, outcome.status.value)
        print(f"  {glyph} {outcome.stage:<12} {label:<8} {outcome.message}")

    not_reached = [stage for stage in PIPELINE_STAGES if report.stage(stage) is None]
    if not_reached:
        print(f"  · {'not reached':<12} {'':<8} {', '.join(not_reached)}")

    _print_egress_summary(report)

    if report.ready_to_grade:
        print(
            f"\n{report.submission_count} submission(s) ready in "
            f"{report.submissions_dir}"
        )

    if report.cloud_confirmation_needed:
        # The pre-flight statement and how to confirm were already printed
        # by _resolve_cloud_confirmation before this summary ran.
        return

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


def _egress_summary_to_dict(report: PipelineReport) -> dict[str, object] | None:
    if report.egress_summary is None:
        return None
    body = report.egress_summary.to_dict()
    body["estimated_cost"] = report.egress_cost
    body["estimated_cost_basis"] = report.egress_cost_basis
    return body


def _report_to_dict(report: PipelineReport) -> dict[str, object]:
    return {
        "ok": report.ok,
        "ready_to_grade": report.ready_to_grade,
        "submission_count": report.submission_count,
        "submissions_dir": str(report.submissions_dir),
        "qa_file": str(report.qa_file) if report.qa_file is not None else None,
        "workspace": str(report.workspace) if report.workspace is not None else None,
        "assignment_id": report.assignment_id,
        "cloud_confirmation_needed": report.cloud_confirmation_needed,
        "cloud_preflight": (
            report.cloud_preflight.to_dict() if report.cloud_preflight is not None else None
        ),
        "egress": _egress_summary_to_dict(report),
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


@dataclass
class _Inputs:
    scan: str | None
    out: str | None
    qa: str | None
    roster: str | None
    blank_template: str | None
    pages_per_submission: int | None


_FOLDER_ROLE_FLAGS = (
    ("scan", "scan", "--scan"),
    ("qa", "qa", "--qa"),
    ("roster", "roster", "--roster"),
    ("template", "blank_template", "--blank-template"),
)

# Guessing either of these wrong grades the wrong thing; a missing roster or
# template only makes the run less precise, and the summary says so.
_BLOCKING_ROLES = frozenset({"scan", "qa"})


def _resolve_class_folder(folder: str | None, inputs: _Inputs, *, stream) -> None:
    if folder is None:
        if not inputs.scan or not inputs.out:
            raise click.UsageError("give a class folder, or both --scan and --out")
        return

    from opengrader.core.class_folder import (
        ROLE_LABELS,
        describe_class_folder,
        scan_class_folder,
    )

    try:
        found = scan_class_folder(folder)
    except NotADirectoryError as exc:
        raise click.UsageError(str(exc)) from None

    stream.write(f"Class folder {found.folder}\n")
    for line in describe_class_folder(found):
        stream.write(f"  {line}\n")

    blocking: list[str] = []
    for role, dest, flag in _FOLDER_ROLE_FLAGS:
        given = getattr(inputs, dest)
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
            setattr(inputs, dest, str(path))
    stream.write("\n")
    stream.flush()

    if blocking:
        raise click.UsageError(
            "more than one file in the class folder fits — choose with "
            + "; ".join(blocking)
        )
    if not inputs.scan:
        raise click.UsageError(
            f"no scanned bundle or folder of split PDFs found in {found.folder} "
            "— pass --scan"
        )
    if not inputs.out:
        inputs.out = str(found.folder)


def _resolve_cloud_confirmation(
    request: PipelineRequest,
    report: PipelineReport,
    narrator: "_Narrator",
    *,
    as_json: bool,
) -> tuple[PipelineRequest, PipelineReport]:
    """`report` is the refusal run_pipeline returns for an unconfirmed cloud
    run. Show the pre-flight statement and either get a yes and re-run, or
    refuse and leave the BLOCKED report as-is (exit_code() maps it to 2).

    A TTY without --json is asked interactively; anything else (a script, a
    redirected run, or --json for machine consumption) only gets the
    statement and the flag that would confirm it, then refuses.
    """
    stream = sys.stderr if as_json else sys.stdout
    preflight = report.cloud_preflight
    statement = preflight.statement() if preflight is not None else report.stages[0].detail
    stream.write(f"\n{statement}\n")

    interactive = sys.stdin.isatty() and not as_json
    if not interactive:
        stream.write(
            "\nRe-run with --yes-send-to-cloud to confirm, or turn off the AI "
            "feature that needs it.\n"
        )
        stream.flush()
        return request, report

    stream.flush()
    try:
        answer = input("Send this to the cloud? [y/N]: ").strip().lower()
    except EOFError:
        answer = ""
    if answer not in ("y", "yes"):
        stream.write("Not confirmed; nothing was sent.\n")
        stream.flush()
        return request, report

    confirmed_request = replace(request, send_to_cloud_confirmed=True)
    new_report = _run_pipeline(confirmed_request, narrator, as_json=as_json)
    return confirmed_request, new_report


def _run_pipeline(
    request: PipelineRequest, narrator: "_Narrator", *, as_json: bool
) -> PipelineReport:
    if as_json:
        with redirect_stdout(sys.stderr):
            return run_pipeline(request, progress=narrator.as_progress())
    return run_pipeline(request, progress=narrator.as_progress())


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


def _run_target(folder: str | None, inputs: _Inputs, assignment: str | None) -> Path:
    from opengrader.core.workspace.context import (
        assignment_for_directory,
        inputs_for_assignment,
        resolve_path,
    )
    from opengrader.core.workspace.store import Workspace, discover_workspace

    if folder:
        return Path(folder).expanduser().resolve()
    if inputs.scan or inputs.out:
        if not inputs.scan or not inputs.out:
            raise click.UsageError("give a class folder, or both --scan and --out")
        return Path(inputs.out).expanduser().resolve()
    root = discover_workspace(Path.cwd())
    if root is not None:
        workspace = Workspace(root)
        try:
            assignment_id = assignment_for_directory(
                workspace,
                Path.cwd(),
                assignment_id=assignment,
                include_pending=True,
            )
        except ValueError as exc:
            raise click.UsageError(str(exc)) from None
        if assignment_id is not None:
            saved = inputs_for_assignment(workspace, assignment_id)
            if saved is not None:
                return resolve_path(root, saved.folder)
    return Path.cwd().resolve()


def _fill_saved_inputs(inputs: _Inputs, workspace_root: Path, target: Path) -> None:
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
        value = getattr(inputs, dest)
        stored = getattr(saved, key)
        if not value and stored:
            setattr(inputs, dest, str(resolve_path(target, stored)))
    if inputs.pages_per_submission is None:
        inputs.pages_per_submission = saved.pages_per_submission


def _save_inputs(
    inputs: _Inputs, root: Path, target: Path, *, assignment: str | None, fresh: bool
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
    if saved is not None and assignment and assignment != saved.id:
        raise ValueError("This folder belongs to another assignment.")
    assignment_id = (
        saved.id if saved is not None else assignment or workspace.assignment_id_for_folder(target)
    )
    stored = AssignmentInputs(
        id=assignment_id,
        folder=stored_path(root, target),
        scan=stored_path(target, Path(inputs.scan)),
        output=stored_path(target, Path(inputs.out)),
        submissions=stored_path(target, Path(inputs.out) / "submissions"),
        qa=stored_path(target, Path(inputs.qa)) if inputs.qa else None,
        roster=stored_path(target, Path(inputs.roster)) if inputs.roster else None,
        blank_template=(
            stored_path(target, Path(inputs.blank_template))
            if inputs.blank_template else None
        ),
        pages_per_submission=inputs.pages_per_submission,
        input_state=input_state({
            "scan": Path(inputs.scan),
            "qa": Path(inputs.qa) if inputs.qa else None,
            "roster": Path(inputs.roster) if inputs.roster else None,
            "blank_template": Path(inputs.blank_template) if inputs.blank_template else None,
        }),
    )
    changed = saved is not None and stored != saved
    published = saved is not None and any(
        item.id == saved.id for item in workspace.assignments()
    )
    if changed and published:
        if not fresh:
            raise ValueError(
                "Assignment inputs changed. Pass --fresh to process them as a new assignment; existing grades stay in the previous assignment."
            )
        return stored.model_copy(update={"id": str(uuid4())}), saved.id
    workspace.save_assignment_inputs(stored)
    return stored, None


@click.command("run", context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.argument("folder", required=False, metavar="[CLASS_FOLDER]")
@click.option(
    "--scan",
    help=(
        "The bulk scan PDF, or a directory of already-split per-student "
        "PDFs (in which case the split stage is skipped). Required without "
        "a class folder."
    ),
)
@click.option(
    "--out",
    help=(
        "Cohort folder to build: split/, submissions/ and the run ledger. "
        "Required without a class folder."
    ),
)
@click.option(
    "--workspace",
    type=click.Path(path_type=Path),
    help="Workspace to receive processed submissions. Defaults to the folder's nearest workspace.",
)
@click.option("--assignment", help="Use this assignment ID in the workspace.")
@click.option(
    "--qa",
    help=(
        "Answer key markdown. Without it the run stops after extraction and "
        "auto-grading is skipped."
    ),
)
@click.option(
    "--roster",
    help="Tab-separated roster (ID<TAB>NAME); names and IDs are matched to it.",
)
@click.option(
    "--blank-template",
    metavar="PDF",
    help=(
        "Blank copy of the assignment. Supplies the question layout to the "
        "extractor and the true page sequence to the split stage."
    ),
)
@click.option(
    "--pages-per-submission",
    type=int,
    help=(
        "Pages each student's submission has. Also what lets the pre-flight "
        "check page order and page count at all."
    ),
)
@click.option(
    "--jobs",
    type=int,
    help="Extraction worker processes (default: the extractor's own choice).",
)
@click.option(
    "--llm/--no-llm",
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
@click.option(
    "--llm-vision-model",
    default="",
    help=(
        "Ollama vision model for transcription. Left blank, each stage "
        "resolves its own default from the AI Setup settings (saved setting "
        "> built-in default) — set it there once instead of "
        "passing it here on every run."
    ),
)
@click.option(
    "--llm-text-model",
    default="",
    help=(
        "Ollama chat model for grading free responses. Left blank, each "
        "stage resolves its own default from the AI Setup settings, same as "
        "--llm-vision-model above."
    ),
)
@click.option(
    "--ollama-host",
    default="",
    help=(
        "Ollama server URL. Left blank, each stage resolves its own default "
        "from the AI Setup settings, same as --llm-vision-model above."
    ),
)
@click.option(
    "--auto-ollama/--no-auto-ollama",
    default=ai_default("auto_start"),
    show_default="from AI Setup",
    help="Start 'ollama serve' and pull the models if they are not there.",
)
@click.option(
    "--llm-min-confidence",
    type=float,
    help=(
        "Only write AI grade suggestions at or above this confidence (0-1). "
        "Run 'llm-eval' on already-graded work to find the right value."
    ),
)
@click.option(
    "--auto-grade/--no-auto-grade",
    default=ai_default("auto_grade"),
    show_default="from AI Setup",
    help=(
        "Run the auto-grade stage automatically once extraction finishes. "
        "--no-auto-grade: extract only; leave every answer for the TUI."
    ),
)
@click.option(
    "--force",
    is_flag=True,
    help=(
        "Carry on past a high-severity pre-flight finding. The finding is "
        "still reported — this says you have read it and want it graded."
    ),
)
@click.option(
    "--fresh",
    is_flag=True,
    help="Redo every stage; changed inputs create a new assignment and preserve prior grades.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what each stage would do, then exit. Writes nothing.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Print the report as JSON instead of prose.",
)
@click.option(
    "--yes-send-to-cloud",
    is_flag=True,
    help=(
        "Confirm sending this run's data to a cloud AI provider — the scale "
        "and destination are shown first (see the pre-flight statement) and "
        "nothing is sent without this or an interactive yes. Never implied "
        "by --llm or any other AI toggle."
    ),
)
def command(
    *,
    folder: str | None,
    scan: str | None,
    out: str | None,
    workspace: Path | None,
    assignment: str | None,
    qa: str | None,
    roster: str | None,
    blank_template: str | None,
    pages_per_submission: int | None,
    jobs: int | None,
    llm: bool | None,
    llm_vision_model: str,
    llm_text_model: str,
    ollama_host: str,
    auto_ollama: bool,
    llm_min_confidence: float | None,
    auto_grade: bool,
    force: bool,
    fresh: bool,
    dry_run: bool,
    as_json: bool,
    yes_send_to_cloud: bool,
) -> PipelineReport | None:
    """Run the whole pre-grading pipeline — scan check, split, extract,
    auto-grade — with one command. Re-runs resume instead of repeating.

    CLASS_FOLDER holds the scan and, optionally, the answer key, roster and
    blank template — each is found automatically, and any flag below
    overrides what was found. Also the --out folder unless --out is given.
    """
    from opengrader.core.workspace.store import discover_workspace

    inputs = _Inputs(
        scan=scan,
        out=out,
        qa=qa,
        roster=roster,
        blank_template=blank_template,
        pages_per_submission=pages_per_submission,
    )
    try:
        target = _run_target(folder, inputs, assignment)
        root = (
            workspace.expanduser().resolve()
            if workspace is not None
            else discover_workspace(target) or target
        )
        _fill_saved_inputs(inputs, root, target)
    except (OSError, ValueError) as exc:
        raise click.UsageError(str(exc)) from None
    if folder is None and not inputs.scan:
        folder = str(target)
    _resolve_class_folder(folder, inputs, stream=sys.stderr if as_json else sys.stdout)

    ai = resolved_ai()
    request = PipelineRequest(
        scan=Path(inputs.scan),
        output_dir=Path(inputs.out),
        qa_file=Path(inputs.qa) if inputs.qa else None,
        roster=Path(inputs.roster) if inputs.roster else None,
        blank_template=Path(inputs.blank_template) if inputs.blank_template else None,
        pages_per_submission=inputs.pages_per_submission,
        llm=LlmOptions(
            read_handwriting=llm if llm is not None else ai.read_handwriting,
            grade_written=llm if llm is not None else ai.grade_written,
            vision_model=llm_vision_model,
            text_model=llm_text_model,
            host=ollama_host,
            auto_ollama=auto_ollama,
            min_confidence=llm_min_confidence,
        ),
        jobs=jobs,
        auto_grade=auto_grade,
        force=force,
        fresh=fresh,
        send_to_cloud_confirmed=yes_send_to_cloud,
    )

    if dry_run:
        _print_plan(request)
        return None

    try:
        saved, previous_id = _save_inputs(
            inputs, root, target, assignment=assignment, fresh=fresh
        )
    except (OSError, ValueError) as exc:
        raise click.UsageError(str(exc)) from None

    narrator = _Narrator(stream=sys.stderr if as_json else sys.stdout)
    report = _run_pipeline(request, narrator, as_json=as_json)
    if report.cloud_confirmation_needed:
        request, report = _resolve_cloud_confirmation(
            request, report, narrator, as_json=as_json
        )

    report = replace(report, workspace=root)
    if report.ok and report.ready_to_grade and request.qa_file is not None:
        narrator.status("Publishing results to the workspace.")
        report = _publish_results(report, root, saved.id)
    if report.ok and report.assignment_id == saved.id and previous_id is not None:
        from opengrader.core.workspace.store import Workspace

        Workspace(root).replace_assignment_inputs(previous_id, saved)

    if as_json:
        print(json.dumps(_report_to_dict(report), indent=2))
    else:
        _print_summary(report)

    code = exit_code(report)
    if code:
        sys.exit(code)
    return report


if __name__ == "__main__":  # pragma: no cover
    command()
