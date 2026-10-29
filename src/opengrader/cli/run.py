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
    AI_UNAVAILABLE_DETAIL,
    ANSWER_KEY_DETAIL,
    PAGE_ORDER_DETAIL,
    PIPELINE_STAGES,
    SPLIT_REVIEW_DETAIL,
    LlmOptions,
    PipelineProgress,
    PipelineReport,
    PipelineRequest,
    StageOutcome,
    StageStatus,
    plan_pipeline,
    run_pipeline,
    section_requests,
)

# The first line of a pause's detail says which question it is; it is for
# PipelineReport to read, not for a person.
_PAUSE_TAGS = frozenset(
    {AI_UNAVAILABLE_DETAIL, ANSWER_KEY_DETAIL, PAGE_ORDER_DETAIL, SPLIT_REVIEW_DETAIL}
)

EPILOG = """\
\b
Examples:
  opengrader run cs146-quiz1/                    # finds the scan, answer key, roster, template
  opengrader run cs146-quiz1/ --qa other-key.md  # any flag overrides what was found
  opengrader run --scan scans/bundle.pdf --qa qa.md --out cohort/
  opengrader run --scan scans/bundle.pdf --qa qa.md --out cohort/ --pages-per-submission 6
  opengrader run --scan split/ --qa qa.md --out cohort/          # already-split PDFs
  opengrader run --scan scans/bundle.pdf --out cohort/ --dry-run # show the plan, do nothing
  opengrader run --scan scans/bundle.pdf --qa qa.md --out cohort/ --llm
  opengrader run --scan scans/bundle.pdf --qa qa.md --out cohort/ --json   # for scripts
  opengrader run cs146-quiz1/ --draft-rubrics    # AI drafts missing rubric items, then pauses
  opengrader run --section-scan s1.pdf --section-scan s2.pdf --qa qa.md --out quiz1/

\b
Class folder:
  Give a folder instead of --scan/--out and its inputs are found for you: a
  markdown answer key that parses, a tab-separated roster, a PDF named
  *template* or *blank*, and the remaining PDF as the scan (or a subfolder of
  PDFs when there is no bundle). Two files that could both be the scan or the
  answer key stop the run and are named; two rosters or templates are left
  out with a warning. The folder doubles as --out.

\b
One scan per section:
  Several scans whose names each say their section (section-01.pdf,
  Sec B.pdf — in the folder or in one subfolder of it) are one assignment
  scanned a section at a time. Each is checked, split and read on its own,
  every submission is tagged with its section, and all of them are graded
  together in submissions/. Rosters named the same way are paired with them.
  By hand: --section-scan PDF[=SECTION], once per section.

\b
Resuming:
  Re-run the same command. Nothing is repeated that does not need to be, and the
  summary says which stages were reused. If inputs change, use --fresh to process
  them as a new assignment while preserving previous grades.

\b
Drafting rubrics (--draft-rubrics):
  After extraction the AI drafts partial-credit rubric items for written
  questions that have none, writes them into the answer key, and stops before
  auto-grading and before publishing. Review the drafts, then run the same
  command again: nothing is left to draft, so it goes on to grade with them.

\b
What it builds under --out:
  student-pdfs/                one PDF per student (skipped when --scan is a directory)
  student-pdfs/scan-notes/     what the split noticed about each PDF (warnings, page matches)
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
    StageStatus.PAUSED: "paused",
}

_STATUS_GLYPH = {
    StageStatus.RAN: "✓",
    StageStatus.REUSED: "↺",
    StageStatus.SKIPPED: "·",
    StageStatus.BLOCKED: "●",
    StageStatus.FAILED: "✗",
    StageStatus.PAUSED: "‖",
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
        if (
            outcome.status in (StageStatus.BLOCKED, StageStatus.FAILED, StageStatus.PAUSED)
            and outcome.detail
        ):
            for line in outcome.detail.splitlines():
                if line not in _PAUSE_TAGS:
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

    if report.split_review_needed:
        paused = next(o for o in report.stages if o.status is StageStatus.PAUSED)
        print("\nPaused before extraction: the split looks wrong in some places.")
        for line in paused.detail.splitlines()[1:]:
            print(f"  {line}")
        print(
            "Nothing was changed or extracted. Run the same command again with one of:\n"
            "  --fix-split   apply the proposed fixes (a problem with no fix still waits)\n"
            "  --keep-split  leave the split as it is; those submissions are right\n"
            "or fix those PDFs yourself and run again: a submission you mend is not\n"
            "asked about, and in a terminal you are asked here instead."
        )
        return

    if report.page_order_review_needed:
        paused = next(o for o in report.stages if o.status is StageStatus.PAUSED)
        print("\nPaused before extraction: some submissions look out of order.")
        for line in paused.detail.splitlines()[1:]:
            print(f"  {line}")
        print(
            "Nothing was moved or extracted. Run the same command again with one of:\n"
            "  --reorder          sort them (into the blank template's order when one is given)\n"
            "  --keep-page-order  leave them as scanned; they are in order, or it does not matter\n"
            "or rearrange those PDFs yourself and run again: pages you put in order are\n"
            "not asked about, and in a terminal you can choose submission by submission."
        )
        return

    if report.ai_unavailable:
        paused = next(o for o in report.stages if o.status is StageStatus.PAUSED)
        print("\nPaused before auto-grading: the AI can't be reached.")
        for line in paused.detail.splitlines()[1:]:
            print(f"  {line}")
        print(
            "Nothing was graded or published. Run the same command again with one of:\n"
            "  --grade-without-ai  grade only what can be checked exactly against the key\n"
            "  --no-auto-grade     skip auto-grading and grade everything yourself\n"
            "or start the AI first. In a terminal you are asked here instead."
        )
        return

    if report.answer_key_needed:
        paused = next(o for o in report.stages if o.status is StageStatus.PAUSED)
        print("\nPaused before extraction: these scans need an answer key.")
        for line in paused.detail.splitlines()[1:]:
            print(f"  {line}")
        return

    if report.paused_for_review:
        print(
            "\nPaused for review: the AI drafted rubric items into "
            f"{report.qa_file}. Check them (edit the file, or press e on the "
            "question in 'opengrader classic'), then run the same command again "
            "to auto-grade with them. Nothing was published yet."
        )
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
        "paused_for_review": report.paused_for_review,
        "ai_unavailable": report.ai_unavailable,
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
    section_scans: tuple[str, ...] = ()
    section_rosters: tuple[str, ...] = ()


def _scans_folder(entries: tuple[str, ...]) -> str:
    """The folder several section scans are in: what `scan` names for them."""
    import os

    from opengrader.core.roster import parse_roster_arg

    parents = [str(parse_roster_arg(entry)[0].expanduser().resolve().parent) for entry in entries]
    return os.path.commonpath(parents)


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

    if found.section_scans and not inputs.scan and not inputs.section_scans:
        inputs.section_scans = tuple(
            f"{item.path}={item.section}" for item in found.section_scans
        )
        inputs.scan = _scans_folder(inputs.section_scans)
        if found.section_rosters and not inputs.roster and not inputs.section_rosters:
            inputs.section_rosters = tuple(
                f"{item.path}={item.section}" for item in found.section_rosters
            )

    blocking: list[str] = []
    for role, dest, flag in _FOLDER_ROLE_FLAGS:
        if dest == "scan" and inputs.section_scans:
            # The scans are listed above, one per section.
            continue
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


def _resolve_split_review(
    request: PipelineRequest,
    report: PipelineReport,
    narrator: "_Narrator",
    *,
    as_json: bool,
) -> PipelineReport:
    """`report` paused before extraction because the split looks wrong
    somewhere. A TTY without --json is asked what to do and the run carries
    on; anything else is told how to answer and stays paused."""
    from opengrader.core.pages.split_review import (
        pending_split_findings,
        review_split_in_terminal,
    )
    from opengrader.core.pipeline import answer_split_review

    stream = sys.stderr if as_json else sys.stdout

    def write(line: str) -> None:
        stream.write(f"{line}\n")
        stream.flush()

    if not (sys.stdin.isatty() and not as_json):
        return report
    # With one scan per section, the section the run stopped at.
    review_dir = next(
        (folder for folder in request.review_dirs if pending_split_findings(folder)),
        request.extract_source,
    )
    if not review_split_in_terminal(
        review_dir,
        ask=input,
        write=write,
        answer=lambda fix, keep: answer_split_review(request, review_dir, fix=fix, keep=keep),
    ):
        return report
    return _run_pipeline(request, narrator, as_json=as_json)


def _resolve_page_order(
    request: PipelineRequest,
    report: PipelineReport,
    narrator: "_Narrator",
    *,
    as_json: bool,
) -> PipelineReport:
    """`report` paused before extraction because some submissions look out of
    order. A TTY without --json is asked what to do and the run carries on;
    anything else is told how to answer and stays paused."""
    from opengrader.core.pages.order_review import review_in_terminal

    stream = sys.stderr if as_json else sys.stdout

    def write(line: str) -> None:
        stream.write(f"{line}\n")
        stream.flush()

    if not (sys.stdin.isatty() and not as_json):
        return report
    from opengrader.core.pages.order_review import pending_findings

    # With one scan per section, the section the run stopped at.
    review_dir = next(
        (folder for folder in request.review_dirs if pending_findings(folder)),
        request.extract_source,
    )
    if not review_in_terminal(review_dir, ask=input, write=write):
        return report
    return _run_pipeline(request, narrator, as_json=as_json)


def _resolve_ai_unavailable(
    request: PipelineRequest,
    report: PipelineReport,
    narrator: "_Narrator",
    *,
    as_json: bool,
) -> PipelineReport:
    """`report` paused before auto-grading because the AI that grades written
    answers can't be reached. A TTY without --json is asked whether to grade
    without it (yes, the default) or skip auto-grading, and the run carries
    on; anything else is told how to answer and stays paused."""
    stream = sys.stderr if as_json else sys.stdout
    if not (sys.stdin.isatty() and not as_json):
        return report
    stream.write(
        "\n  Yes: grade without AI now. Only answers that can be checked exactly\n"
        "       against the answer key are scored; the rest are left for you.\n"
        "  No:  skip auto-grading. Start the AI and run this again to auto-grade.\n"
    )
    stream.flush()
    try:
        answer = input("Auto-grade without AI? [Y/n] ").strip().lower()
    except EOFError:
        return report
    if answer in ("", "y", "yes"):
        return _run_pipeline(replace(request, grade_without_ai=True), narrator, as_json=as_json)
    if answer in ("n", "no"):
        return _run_pipeline(replace(request, auto_grade=False), narrator, as_json=as_json)
    return report


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
        "Cohort folder to build: student-pdfs/, submissions/ and the run ledger. "
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
    "--section-scan",
    "section_scans",
    multiple=True,
    metavar="PDF[=SECTION]",
    help=(
        "One scan per class section, instead of --scan: repeat once per "
        "section. SECTION defaults to what the file's name says "
        "(section-01.pdf is 'Section 01'). Every submission is tagged with its "
        "section; all are graded together."
    ),
)
@click.option(
    "--section-roster",
    "section_rosters",
    multiple=True,
    metavar="TSV[=SECTION]",
    help=(
        "A section's own roster, repeatable. With --section-scan each section "
        "uses the roster naming the same section (else --roster); with one "
        "--scan, students are routed to the section whose roster holds them."
    ),
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
    "--fixed",
    "fixed_split",
    is_flag=True,
    help=(
        "Split the scan every --pages-per-submission pages instead of matching "
        "pages to find where each student starts."
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
    "--llm-min-confidence",
    type=float,
    help=(
        "Only write AI grade suggestions at or above this confidence (0-1). "
        "Run 'opengrader llm-eval' on already-graded work to find the right value."
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
    "--draft-rubrics",
    is_flag=True,
    help=(
        "Before auto-grading, let the AI draft partial-credit rubric items for "
        "written questions that have none, write them into the answer key, and "
        "stop so you can review them. Run again afterwards to grade with them."
    ),
)
@click.option(
    "--reorder",
    "reorder_pages",
    is_flag=True,
    help=(
        "Sort each submission's pages when splitting. Without it pages are "
        "left as scanned and a submission that looks out of order is "
        "reported, because a misread page number would move a page that was "
        "already right. With --blank-template the template supplies the order."
    ),
)
@click.option(
    "--keep-page-order",
    is_flag=True,
    help=(
        "Leave submissions that look out of order as scanned and carry on. "
        "Without this or --reorder the run pauses before extraction so you "
        "can confirm, sort, or rearrange them (asked here in a terminal)."
    ),
)
@click.option(
    "--fix-split",
    is_flag=True,
    help=(
        "Where the split looks wrong (a submission of the wrong length, say "
        "a six-page assignment split 5 + 1) and a fix can be worked out, "
        "apply it and carry on. A problem with no fix still pauses the run."
    ),
)
@click.option(
    "--keep-split",
    is_flag=True,
    help=(
        "Leave the split as it is where it looks wrong, and carry on. Without "
        "this or --fix-split the run pauses before extraction so you can "
        "check (asked here in a terminal)."
    ),
)
@click.option(
    "--grade-without-ai",
    is_flag=True,
    help=(
        "If the AI that grades written answers can't be reached (Ollama not "
        "running, say), auto-grade without it: only answers that can be checked "
        "exactly against the answer key are scored, the rest are left for you. Without this "
        "the run pauses before auto-grading (asked here in a terminal)."
    ),
)
@click.option(
    "--read-without-key",
    is_flag=True,
    help=(
        "Read scans that print no points beside their question numbers "
        "without an answer key, by whatever numbers the pages print. "
        "Without this (and without --qa, --blank-template) the run pauses "
        "after splitting them, because the answer key is what finds their "
        "questions and reading them twice is the slow part."
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
    section_scans: tuple[str, ...],
    section_rosters: tuple[str, ...],
    blank_template: str | None,
    pages_per_submission: int | None,
    fixed_split: bool,
    jobs: int | None,
    llm: bool | None,
    llm_vision_model: str,
    llm_text_model: str,
    ollama_host: str,
    llm_min_confidence: float | None,
    auto_grade: bool,
    draft_rubrics: bool,
    reorder_pages: bool,
    keep_page_order: bool,
    fix_split: bool,
    keep_split: bool,
    grade_without_ai: bool,
    read_without_key: bool,
    force: bool,
    fresh: bool,
    dry_run: bool,
    as_json: bool,
    yes_send_to_cloud: bool,
) -> PipelineReport | None:
    """Run the whole pre-grading pipeline — scan check, split, extract,
    (optionally draft rubrics,) auto-grade — with one command. Re-runs resume instead of repeating.

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
        section_scans=tuple(section_scans),
        section_rosters=tuple(section_rosters),
    )
    if inputs.section_scans:
        if inputs.scan:
            raise click.UsageError("give --scan or --section-scan, not both")
        inputs.scan = _scans_folder(inputs.section_scans)
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

    if fixed_split and not inputs.pages_per_submission:
        raise click.UsageError("--fixed needs --pages-per-submission.")

    ai = resolved_ai()
    request = PipelineRequest(
        scan=Path(inputs.scan),
        output_dir=Path(inputs.out),
        qa_file=Path(inputs.qa) if inputs.qa else None,
        roster=Path(inputs.roster) if inputs.roster else None,
        section_rosters=inputs.section_rosters,
        section_scans=inputs.section_scans,
        blank_template=Path(inputs.blank_template) if inputs.blank_template else None,
        pages_per_submission=inputs.pages_per_submission,
        fixed_split=fixed_split,
        reorder_pages=reorder_pages,
        keep_page_order=keep_page_order,
        fix_split=fix_split,
        keep_split=keep_split,
        grade_without_ai=grade_without_ai,
        read_without_key=read_without_key,
        llm=LlmOptions(
            read_handwriting=llm if llm is not None else ai.read_handwriting,
            grade_written=llm if llm is not None else ai.grade_written,
            vision_model=llm_vision_model,
            text_model=llm_text_model,
            host=ollama_host,
            min_confidence=llm_min_confidence,
        ),
        jobs=jobs,
        auto_grade=auto_grade,
        force=force,
        fresh=fresh,
        draft_rubrics=draft_rubrics,
        send_to_cloud_confirmed=yes_send_to_cloud,
    )

    try:
        section_requests(request)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from None

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

    # Asked in a terminal one at a time; each answer re-runs, and the re-run
    # may stop at the next question (another section, or the page order).
    while report.split_review_needed:
        answered = _resolve_split_review(request, report, narrator, as_json=as_json)
        if answered is report:
            break
        report = answered

    if report.page_order_review_needed:
        report = _resolve_page_order(request, report, narrator, as_json=as_json)

    if report.ai_unavailable:
        report = _resolve_ai_unavailable(request, report, narrator, as_json=as_json)

    report = replace(report, workspace=root)
    # A paused run published nothing on purpose: the answer key is about to be
    # reviewed, and publishing first would make that edit a "changed input".
    if (
        report.ok
        and not report.paused_for_review
        and report.ready_to_grade
        and request.qa_file is not None
    ):
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
