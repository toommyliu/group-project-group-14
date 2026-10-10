from __future__ import annotations

import shlex
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

from opengrader.core import egress
from opengrader.core.roster import parse_roster_arg
from opengrader.core.submissions.manifest import (
    RUN_LEDGER_FILENAME,
    ExtractionRecord,
    append_record,
    file_fingerprint,
    input_fingerprint,
    read_manifest,
    record_is_reusable,
    relative_outputs,
    settings_fingerprint,
    utc_timestamp,
)

if TYPE_CHECKING:  # pragma: no cover
    from opengrader.core.app_config import ResolvedAI
    from opengrader.core.cloud_preflight import CloudPreflight
    from opengrader.core.egress import EgressSummary
    from opengrader.core.submissions.scan_check import ScanReport

STAGE_SCAN_CHECK = "scan-check"
STAGE_SPLIT = "split"
STAGE_EXTRACT = "extract"
STAGE_DRAFT_RUBRICS = "draft-rubrics"
STAGE_AUTO_GRADE = "auto-grade"

PIPELINE_STAGES: tuple[str, ...] = (
    STAGE_SCAN_CHECK,
    STAGE_SPLIT,
    STAGE_EXTRACT,
    STAGE_DRAFT_RUBRICS,
    STAGE_AUTO_GRADE,
)

_STAGE_COMMANDS = {
    STAGE_SPLIT: "submission-splitter",
    STAGE_EXTRACT: "submission-extractor",
    STAGE_DRAFT_RUBRICS: "draft-rubric",
    STAGE_AUTO_GRADE: "automated-grading",
}


# Prefix of the PAUSED extract outcome's detail when page order needs confirming.
PAGE_ORDER_DETAIL = "page-order:"
# …and when the scans cannot be read until there is an answer key.
ANSWER_KEY_DETAIL = "answer-key:"


class StageStatus(str, Enum):
    RAN = "ran"
    REUSED = "reused"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    FAILED = "failed"
    # Stopped on purpose for a person to review (drafted rubrics, or pages that
    # look out of order): not a failure, but nothing after it runs and nothing
    # is published.
    PAUSED = "paused"


@dataclass(frozen=True)
class LlmOptions:
    read_handwriting: bool = False
    grade_written: bool = False
    vision_model: str = ""
    text_model: str = ""
    host: str = ""
    auto_ollama: bool = False
    min_confidence: float | None = None

    @property
    def enabled(self) -> bool:
        return self.read_handwriting or self.grade_written


@dataclass(frozen=True)
class PipelineRequest:
    scan: Path
    output_dir: Path
    qa_file: Path | None = None
    roster: Path | None = None
    # A mixed stack of several class sections: one `TSV[=SECTION]` entry per
    # roster, exactly as submission-extractor's --id-file takes them. Each
    # student is routed to the section whose roster holds their ID. When given,
    # these are used instead of `roster`.
    section_rosters: tuple[str, ...] = ()
    blank_template: Path | None = None
    # Answer boxes for an assignment Opengrader did not generate. It answers
    # the same question as `blank_template` (where are the answers?), so the
    # two cannot be combined — the extractor refuses the pair.
    region_map: Path | None = None
    pages_per_submission: int | None = None
    # Sort each submission's pages. Off by default: a scan's pages are left as
    # scanned and a submission that looks out of order is reported instead.
    reorder_pages: bool = False
    # The answer to "these submissions look out of order" given up front: leave
    # them as scanned and carry on. Without it (or reorder_pages) the run pauses
    # before extraction for a person to confirm, sort, or rearrange them.
    keep_page_order: bool = False
    # The answer to "these scans need an answer key to find their questions"
    # given up front: read them anyway, by whatever numbers the pages print.
    # One-shot, like `force`: never remembered.
    read_without_key: bool = False
    llm: LlmOptions = LlmOptions()
    jobs: int | None = None
    auto_grade: bool = True
    force: bool = False
    fresh: bool = False
    # Draft rubric items for written questions that have none, then stop
    # before auto-grading so a person reviews them. Never on by default.
    draft_rubrics: bool = False
    # A run that would send anything to the cloud refuses unless this is True
    # (interactively confirmed, or --yes-send-to-cloud). Never defaulted true
    # by anything else, and never implied by turning on an AI feature. Student
    # names and IDs are never sent to the cloud — there is no separate
    # override for that, and none should be added here.
    send_to_cloud_confirmed: bool = False

    def __post_init__(self) -> None:
        if self.blank_template is not None and self.region_map is not None:
            raise ValueError(
                "A blank template and a region map both say where the answers "
                "are; give one or the other."
            )

    @property
    def split_dir(self) -> Path:
        return self.output_dir / "split"

    @property
    def submissions_dir(self) -> Path:
        return self.output_dir / "submissions"

    @property
    def input_is_split(self) -> bool:
        return self.scan.is_dir()

    @property
    def extract_source(self) -> Path:
        return self.scan if self.input_is_split else self.split_dir


@dataclass(frozen=True)
class StagePlan:
    stage: str
    will_run: bool
    reason: str
    argv: tuple[str, ...] = ()
    status: StageStatus = StageStatus.RAN

    @property
    def command(self) -> str:
        return shlex.join(self.argv) if self.argv else ""


@dataclass(frozen=True)
class StageOutcome:
    stage: str
    status: StageStatus
    message: str
    detail: str = ""
    duration_seconds: float = 0.0
    outputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class PipelineReport:
    stages: tuple[StageOutcome, ...]
    submissions_dir: Path
    qa_file: Path | None
    workspace: Path | None = None
    assignment_id: str | None = None
    # What this run itself sent, never a lifetime total —
    # egress.summarize(folder, since=<this run's start>). None when the run
    # never got past the cloud pre-flight check (see
    # PipelineReport.cloud_confirmation_needed) or otherwise never started.
    egress_summary: "EgressSummary | None" = None
    egress_cost: float | None = None
    egress_cost_basis: str = ""
    # Set only on the cloud-confirmation refusal below, so a caller (the TUI's
    # pipeline card, in particular) can show the scale/destination/cost
    # statement without parsing it back out of a BLOCKED outcome's detail text.
    cloud_preflight: "CloudPreflight | None" = None

    @property
    def cloud_confirmation_needed(self) -> bool:
        """True when this report is the refusal run_pipeline returns because a
        cloud run had not been confirmed, rather than the result of a run that
        actually executed any stage."""
        return any(
            outcome.status is StageStatus.BLOCKED and outcome.detail.startswith("cloud-preflight:")
            for outcome in self.stages
        )

    @property
    def ok(self) -> bool:
        return not any(
            outcome.status in (StageStatus.FAILED, StageStatus.BLOCKED)
            for outcome in self.stages
        )

    @property
    def submission_count(self) -> int:
        return sum(1 for _ in self.submissions_dir.rglob("*.submission.md"))

    @property
    def ready_to_grade(self) -> bool:
        return self.submission_count > 0

    @property
    def paused_for_review(self) -> bool:
        return any(outcome.status is StageStatus.PAUSED for outcome in self.stages)

    @property
    def page_order_review_needed(self) -> bool:
        """True when the run paused before extraction because some split
        submissions look out of order and nobody has confirmed them yet."""
        return any(
            outcome.status is StageStatus.PAUSED and outcome.detail.startswith(PAGE_ORDER_DETAIL)
            for outcome in self.stages
        )

    @property
    def answer_key_needed(self) -> bool:
        """True when the run stopped before extraction because nothing says
        where the questions are on these scans, and there is no answer key."""
        return any(
            outcome.status is StageStatus.PAUSED and outcome.detail.startswith(ANSWER_KEY_DETAIL)
            for outcome in self.stages
        )

    def stage(self, name: str) -> StageOutcome | None:
        for outcome in self.stages:
            if outcome.stage == name:
                return outcome
        return None


@dataclass
class PipelineProgress:
    on_stage_start: Callable[[str], None] | None = None
    on_stage_finish: Callable[[StageOutcome], None] | None = None
    on_status: Callable[[str], None] | None = None
    on_file_start: Callable[[str, int], None] | None = None
    on_response: Callable[[str, str], None] | None = None

    def stage_start(self, stage: str) -> None:
        if self.on_stage_start is not None:
            self.on_stage_start(stage)

    def stage_finish(self, outcome: StageOutcome) -> None:
        if self.on_stage_finish is not None:
            self.on_stage_finish(outcome)

    def status(self, message: str) -> None:
        if self.on_status is not None:
            self.on_status(message)

    def file_start(self, name: str, count: int) -> None:
        if self.on_file_start is not None:
            self.on_file_start(name, count)

    def response(self, name: str, item: str) -> None:
        if self.on_response is not None:
            self.on_response(name, item)


def plan_pipeline(request: PipelineRequest) -> tuple[StagePlan, ...]:
    """A forecast only: execution replans after each stage as earlier outputs invalidate reuse."""
    ledger = _read_ledger(request)
    return tuple(_plan_stage(request, stage, ledger) for stage in PIPELINE_STAGES)


def _plan_stage(
    request: PipelineRequest,
    stage: str,
    ledger: dict[str, ExtractionRecord],
) -> StagePlan:
    argv = _stage_argv(request, stage)

    if stage == STAGE_SPLIT and request.input_is_split:
        return StagePlan(
            stage=stage,
            will_run=False,
            reason=(
                "the input is already a directory of per-student PDFs; "
                "extraction reads it directly"
            ),
            status=StageStatus.SKIPPED,
        )

    if stage == STAGE_AUTO_GRADE:
        if not request.auto_grade:
            return StagePlan(
                stage=stage,
                will_run=False,
                reason="auto-grading turned off for this run",
                status=StageStatus.SKIPPED,
            )
        if request.qa_file is None:
            return StagePlan(
                stage=stage,
                will_run=False,
                reason="no answer key given, so there is nothing to grade against",
                status=StageStatus.SKIPPED,
            )

    if stage == STAGE_EXTRACT:
        return StagePlan(
            stage=stage,
            will_run=True,
            reason="always runs; --resume skips the individual PDFs already done",
            argv=argv,
        )

    if stage == STAGE_DRAFT_RUBRICS:
        if not request.draft_rubrics:
            return StagePlan(
                stage=stage,
                will_run=False,
                reason="not requested (--draft-rubrics drafts missing rubric items)",
                status=StageStatus.SKIPPED,
            )
        if request.qa_file is None:
            return StagePlan(
                stage=stage,
                will_run=False,
                reason="no answer key given, so there are no rubrics to draft",
                status=StageStatus.SKIPPED,
            )
        # No ledger reuse: once its drafts are written the questions have
        # partial credit, so a re-run finds nothing to draft and moves on.
        return StagePlan(
            stage=stage,
            will_run=True,
            reason=(
                "drafts rubric items for written questions that have none, "
                "and pauses for review if it writes any"
            ),
            argv=argv,
        )

    source, settings_id = _stage_ledger_key(request, stage)
    record = ledger.get(stage)
    if record_is_reusable(
        record,
        output_dir=request.output_dir,
        source_sha256=source,
        settings_id=settings_id,
    ):
        return StagePlan(
            stage=stage,
            will_run=False,
            reason=_reuse_reason(stage, record),
            argv=argv,
            status=StageStatus.REUSED,
        )

    return StagePlan(
        stage=stage,
        will_run=True,
        reason=_run_reason(request, record),
        argv=argv,
    )


def _stage_argv(request: PipelineRequest, stage: str) -> tuple[str, ...]:
    if stage == STAGE_SPLIT:
        return _split_argv(request)
    if stage == STAGE_EXTRACT:
        return _extract_argv(request)
    if stage == STAGE_DRAFT_RUBRICS:
        return _draft_rubrics_argv(request)
    if stage == STAGE_AUTO_GRADE:
        return _auto_grade_argv(request)
    return ()


def _split_argv(request: PipelineRequest) -> tuple[str, ...]:
    argv = [
        _STAGE_COMMANDS[STAGE_SPLIT],
        "--input",
        str(request.scan),
        "--output",
        str(request.split_dir),
        "--fix-rotation",
        "--fix-order",
        "--extra-work-pages",
    ]
    if request.pages_per_submission:
        argv += ["--pages-per-submission", str(request.pages_per_submission)]
    if request.blank_template is not None:
        argv += ["--blank-template", str(request.blank_template)]
    if request.reorder_pages:
        argv.append("--reorder")
    # No --yes-send-to-cloud here: the splitter's only vision calls are
    # whole-page reads (page numbers, Name/ID headers), which
    # GuardedVisionReader always routes to a local reader — a split run
    # never sends anything to the cloud, so the splitter doesn't accept the
    # flag at all (see cli/submission_splitter.py).
    return tuple(argv)


def _extract_argv(request: PipelineRequest) -> tuple[str, ...]:
    argv = [
        _STAGE_COMMANDS[STAGE_EXTRACT],
        "--input",
        str(request.extract_source),
        "--output",
        str(request.submissions_dir),
        "--resume",
    ]
    if request.section_rosters:
        for entry in request.section_rosters:
            argv += ["--id-file", entry]
    elif request.roster is not None:
        argv += ["--id-file", str(request.roster)]
    if request.blank_template is not None:
        argv += ["--blank-template", str(request.blank_template)]
    if request.region_map is not None:
        argv += ["--region-map", str(request.region_map)]
    if (
        request.qa_file is not None
        and request.blank_template is None
        and request.region_map is None
    ):
        # Something has to say where the questions are; with neither of the
        # above it is the answer key (scans with their own labels ignore it).
        argv += ["--qa-file", str(request.qa_file)]
    if request.jobs:
        argv += ["--jobs", str(request.jobs)]
    if request.llm.read_handwriting:
        argv.append("--llm-escalate")
        if request.llm.vision_model:
            argv += ["--ollama-model", request.llm.vision_model]
        if request.llm.host:
            argv += ["--ollama-host", request.llm.host]
        if request.llm.auto_ollama:
            argv.append("--auto-ollama")
    if request.send_to_cloud_confirmed:
        argv.append("--yes-send-to-cloud")
    return tuple(argv)


def refine_argv(request: PipelineRequest, *, dpi: int | None = None) -> tuple[str, ...]:
    """A second reading of this request's low-confidence answers only.

    The extract stage's own arguments with --resume swapped for --refine (the
    extractor refuses the pair), so the second pass crops against the same
    template, region map and rosters as the first. Not a pipeline stage: it
    only makes sense on an extraction a person has already looked at.
    """
    argv = [arg for arg in _extract_argv(request) if arg != "--resume"]
    argv.append("--refine")
    if dpi:
        argv += ["--refine-dpi", str(dpi)]
    return tuple(argv)


def _draft_rubrics_argv(request: PipelineRequest) -> tuple[str, ...]:
    argv = [
        _STAGE_COMMANDS[STAGE_DRAFT_RUBRICS],
        "--solutions",
        str(request.qa_file),
        "--input",
        str(request.submissions_dir),
        "--write",
    ]
    if request.llm.text_model:
        argv += ["--llm-model", request.llm.text_model]
    if request.llm.host:
        argv += ["--llm-host", request.llm.host]
    if request.send_to_cloud_confirmed:
        argv.append("--yes-send-to-cloud")
    return tuple(argv)


def _auto_grade_argv(request: PipelineRequest) -> tuple[str, ...]:
    argv = [
        _STAGE_COMMANDS[STAGE_AUTO_GRADE],
        "--solutions",
        str(request.qa_file),
        "--input",
        str(request.submissions_dir),
    ]
    if request.llm.grade_written:
        argv.append("--llm")
        if request.llm.text_model:
            argv += ["--llm-model", request.llm.text_model]
        if request.llm.host:
            argv += ["--llm-host", request.llm.host]
        if request.llm.auto_ollama:
            argv.append("--auto-ollama")
        if request.llm.min_confidence is not None:
            argv += ["--llm-min-confidence", str(request.llm.min_confidence)]
        if request.send_to_cloud_confirmed:
            argv.append("--yes-send-to-cloud")
    return tuple(argv)


def _read_ledger(request: PipelineRequest) -> dict[str, ExtractionRecord]:
    if request.fresh:
        return {}
    return read_manifest(request.output_dir, filename=RUN_LEDGER_FILENAME)


def auto_grade_pending(output_dir: str | Path) -> bool:
    """True when a run here read the scans but none has ever auto-graded them.

    That is a folder prepared before it had an answer key (or with
    auto-grading off, or stopped for a rubric review). Only the run ledger is
    read, so this is cheap enough to ask every time a folder is opened.
    """
    ledger = read_manifest(output_dir, filename=RUN_LEDGER_FILENAME)
    return STAGE_EXTRACT in ledger and STAGE_AUTO_GRADE not in ledger


def _stage_ledger_key(request: PipelineRequest, stage: str) -> tuple[str, str]:
    llm = request.llm
    template = (
        file_fingerprint(request.blank_template)
        if request.blank_template is not None
        else ""
    )

    if stage == STAGE_SCAN_CHECK:
        return (
            input_fingerprint(request.scan),
            settings_fingerprint(
                {
                    "pages_per_submission": request.pages_per_submission,
                    "blank_template": template,
                }
            ),
        )

    if stage == STAGE_SPLIT:
        settings = {
            "pages_per_submission": request.pages_per_submission,
            "blank_template": template,
            "fix_rotation": True,
            "fix_order": True,
            "extra_work_pages": True,
        }
        # Only when on, so ledgers written before the option existed still match.
        if request.reorder_pages:
            settings["reorder"] = True
        return (input_fingerprint(request.scan), settings_fingerprint(settings))

    if stage == STAGE_EXTRACT:
        settings = {
            "roster": (
                file_fingerprint(request.roster)
                if request.roster is not None
                else ""
            ),
            "blank_template": template,
            "llm_escalate": llm.read_handwriting,
            "vision_model": llm.vision_model,
        }
        # Only when given, so ledgers written before these options existed still match.
        if request.section_rosters:
            settings["section_rosters"] = [
                [section or "", file_fingerprint(path)]
                for path, section in map(parse_roster_arg, request.section_rosters)
            ]
        if request.region_map is not None:
            settings["region_map"] = file_fingerprint(request.region_map)
        return (
            input_fingerprint(request.extract_source),
            settings_fingerprint(settings),
        )

    return (
        file_fingerprint(request.qa_file) if request.qa_file is not None else "",
        settings_fingerprint(
            {
                "llm": llm.grade_written,
                "text_model": llm.text_model,
                "min_confidence": llm.min_confidence,
                "cohort": _cohort_fingerprint(request.submissions_dir),
            }
        ),
    )


def _stage_source(request: PipelineRequest, stage: str) -> Path:
    if stage == STAGE_EXTRACT:
        return request.extract_source
    if stage == STAGE_AUTO_GRADE and request.qa_file is not None:
        return request.qa_file
    return request.scan


def _cohort_fingerprint(submissions_dir: Path) -> str:
    names = sorted(
        str(path.relative_to(submissions_dir))
        for path in submissions_dir.rglob("*.submission.md")
    )
    return settings_fingerprint({"submissions": names})


def _record_stage(
    request: PipelineRequest,
    stage: str,
    *,
    source: str,
    settings_id: str,
    outputs: Sequence[Path],
    duration_seconds: float,
) -> None:
    append_record(
        request.output_dir,
        ExtractionRecord(
            key=stage,
            source=str(_stage_source(request, stage)),
            source_sha256=source,
            settings_id=settings_id,
            outputs=relative_outputs(request.output_dir, outputs),
            completed_at=utc_timestamp(),
            duration_seconds=duration_seconds,
        ),
        filename=RUN_LEDGER_FILENAME,
    )


def _reuse_reason(stage: str, record: ExtractionRecord | None) -> str:
    count = len(record.outputs) if record is not None else 0
    if stage == STAGE_SCAN_CHECK:
        return "reused the previous pre-flight — this scan has not changed"
    if stage == STAGE_SPLIT:
        return f"reused {count} split PDF(s) from the previous run"
    if stage == STAGE_AUTO_GRADE:
        return (
            "reused the previous auto-grading pass — same answer key, same "
            "settings, same submissions"
        )
    return "reused the previous run's work"


def _run_reason(request: PipelineRequest, record: ExtractionRecord | None) -> str:
    if request.fresh:
        return "--fresh: the ledger is ignored and every stage redone"
    if record is None:
        return "no record of this stage having run here before"
    return "the input or the settings changed since the last run"


def _cloud_refusal(request: PipelineRequest, preflight: "CloudPreflight") -> PipelineReport:
    """A clear refusal, in the same shape (StageStatus.BLOCKED) the scan-check
    high-severity block already uses — the CLI already maps BLOCKED to exit 2, and
    the TUI can show it the same way it shows any other blocked stage."""
    message = (
        f"this run would send data to {preflight.host} — re-run with "
        "--yes-send-to-cloud to confirm, or turn off the AI feature that needs it"
    )
    outcome = StageOutcome(
        stage=PIPELINE_STAGES[0],
        status=StageStatus.BLOCKED,
        message=message,
        # The "cloud-preflight:" prefix is how PipelineReport.cloud_confirmation_needed
        # tells this refusal apart from an ordinary scan-check block.
        detail="cloud-preflight:\n" + preflight.statement(),
    )
    return PipelineReport(
        stages=(outcome,),
        submissions_dir=request.submissions_dir,
        qa_file=request.qa_file,
        cloud_preflight=preflight,
    )


def _resolved_for_request(request: PipelineRequest) -> "ResolvedAI":
    from opengrader.core import app_config

    return app_config.with_overrides(
        app_config.resolve(),
        host=request.llm.host or None,
        vision_model=request.llm.vision_model or None,
        text_model=request.llm.text_model or None,
    )


def run_pipeline(
    request: PipelineRequest, *, progress: PipelineProgress | None = None
) -> PipelineReport:
    from opengrader.core.cloud_preflight import plan_cloud_egress

    reporter = progress or PipelineProgress()
    request.output_dir.mkdir(parents=True, exist_ok=True)

    resolved = _resolved_for_request(request)
    preflight = plan_cloud_egress(request, resolved)
    if preflight is not None:
        if preflight.sends_anything and not request.send_to_cloud_confirmed:
            return _cloud_refusal(request, preflight)

    run_start = utc_timestamp()
    with egress.recording(request.submissions_dir):
        outcomes: list[StageOutcome] = []
        for stage in PIPELINE_STAGES:
            plan = _plan_stage(request, stage, _read_ledger(request))
            reporter.stage_start(stage)
            started = time.monotonic()

            if not plan.will_run:
                outcome = StageOutcome(
                    stage=stage,
                    status=plan.status,
                    message=plan.reason,
                    outputs=_recorded_outputs(request, stage, plan),
                )
            else:
                try:
                    outcome = _execute(request, plan, reporter, started)
                except KeyboardInterrupt:
                    raise
                except (SystemExit, Exception) as exc:  # noqa: BLE001
                    outcome = StageOutcome(
                        stage=stage,
                        status=StageStatus.FAILED,
                        message=_failure_message(exc),
                        detail=plan.command,
                        duration_seconds=time.monotonic() - started,
                    )

            outcomes.append(outcome)
            reporter.stage_finish(outcome)
            if outcome.status in (StageStatus.FAILED, StageStatus.BLOCKED, StageStatus.PAUSED):
                break

        # What THIS run sent — never a lifetime total — plus its cost.
        summary = egress.summarize(request.submissions_dir, since=run_start)
        cost, cost_basis = (None, "")
        if summary.total_calls:
            cost = egress.estimate_cost(
                summary.total_input_tokens, summary.total_output_tokens, resolved.provider.pricing
            )
            cost_basis = (
                "measured from this run's own calls"
                if cost is not None
                else "provider pricing not stated"
            )

    return PipelineReport(
        stages=tuple(outcomes),
        submissions_dir=request.submissions_dir,
        qa_file=request.qa_file,
        egress_summary=summary,
        egress_cost=cost,
        egress_cost_basis=cost_basis,
        cloud_preflight=preflight,
    )


def _execute(
    request: PipelineRequest,
    plan: StagePlan,
    progress: PipelineProgress,
    started: float,
) -> StageOutcome:
    if plan.stage == STAGE_SCAN_CHECK:
        return _run_scan_check(request, plan, progress, started)
    if plan.stage == STAGE_SPLIT:
        return _run_split(request, plan, progress, started)
    if plan.stage == STAGE_EXTRACT:
        return _run_extract(request, plan, progress, started)
    if plan.stage == STAGE_DRAFT_RUBRICS:
        return _run_draft_rubrics(request, plan, progress, started)
    return _run_auto_grade(request, plan, progress, started)


def _run_scan_check(
    request: PipelineRequest,
    plan: StagePlan,
    progress: PipelineProgress,
    started: float,
) -> StageOutcome:
    from opengrader.core.submissions.scan_check import (
        analyze_scan_pdf,
        discover_scan_pdfs,
        summarize_issues,
    )

    paths = discover_scan_pdfs(request.scan)
    if not paths:
        return StageOutcome(
            stage=plan.stage,
            status=StageStatus.FAILED,
            message=f"no PDF found at {request.scan}",
            duration_seconds=time.monotonic() - started,
        )

    is_bundle = not request.input_is_split
    bundle_pages = request.pages_per_submission if is_bundle else None
    per_file_pages = request.pages_per_submission if not is_bundle else None
    if request.blank_template is not None:
        # The template is one submission long: that is a bundle's submission
        # length, never its whole page count.
        template_pages = _template_page_count(request.blank_template)
        if is_bundle:
            bundle_pages = bundle_pages or template_pages
        else:
            per_file_pages = template_pages

    reports = []
    for index, path in enumerate(paths, start=1):
        progress.status(f"Checking {path.name} ({index}/{len(paths)})…")
        reports.append(
            analyze_scan_pdf(
                path,
                pages_per_submission=bundle_pages,
                expected_page_count=per_file_pages,
                single_submission=not is_bundle,
                progress_callback=progress.status,
            )
        )

    issues = [issue for report in reports for issue in report.issues]
    high = [issue for issue in issues if issue.severity == "high"]
    pages = sum(report.page_count for report in reports)
    detail = _scan_detail(reports)
    duration = time.monotonic() - started

    if high and not request.force:
        return StageOutcome(
            stage=plan.stage,
            status=StageStatus.BLOCKED,
            message=(
                f"{len(high)} high-severity finding(s) in {pages} page(s) — fix "
                "the scan, or re-run with --force to grade it as it is"
            ),
            detail=detail,
            duration_seconds=duration,
        )

    if high:
        message = (
            f"{pages} page(s) checked — {summarize_issues(issues)}; continuing "
            "because --force was given"
        )
    elif issues:
        message = f"{pages} page(s) checked — {summarize_issues(issues)}"
    else:
        message = f"{pages} page(s) checked — no problems found"
        source, settings_id = _stage_ledger_key(request, plan.stage)
        _record_stage(
            request,
            plan.stage,
            source=source,
            settings_id=settings_id,
            outputs=(),
            duration_seconds=duration,
        )

    return StageOutcome(
        stage=plan.stage,
        status=StageStatus.RAN,
        message=message,
        detail=detail,
        duration_seconds=duration,
    )


def _run_split(
    request: PipelineRequest,
    plan: StagePlan,
    progress: PipelineProgress,
    started: float,
) -> StageOutcome:
    from opengrader.cli import submission_splitter

    source, settings_id = _stage_ledger_key(request, plan.stage)
    submission_splitter.run_cli(
        list(plan.argv[1:]), status_callback=progress.status
    )

    outputs = sorted(request.split_dir.glob("*.pdf"))
    duration = time.monotonic() - started
    _record_stage(
        request,
        plan.stage,
        source=source,
        settings_id=settings_id,
        outputs=outputs,
        duration_seconds=duration,
    )
    return StageOutcome(
        stage=plan.stage,
        status=StageStatus.RAN,
        message=f"split into {len(outputs)} per-student PDF(s)",
        duration_seconds=duration,
        outputs=tuple(str(path) for path in outputs),
    )


def _run_extract(
    request: PipelineRequest,
    plan: StagePlan,
    progress: PipelineProgress,
    started: float,
) -> StageOutcome:
    """The per-PDF extraction manifest owns reuse; the pipeline ledger is informational."""
    from opengrader.cli import submission_extractor
    from opengrader.core.submissions.processor import ExtractionProgress

    paused = _answer_key_gate(request, plan, started) or _page_order_gate(
        request, plan, progress, started
    )
    if paused is not None:
        return paused

    source, settings_id = _stage_ledger_key(request, plan.stage)
    submission_extractor.run_cli(
        list(plan.argv[1:]),
        progress=ExtractionProgress(
            on_file_start=progress.on_file_start,
            on_response=progress.on_response,
        ),
        status_callback=progress.status,
    )

    outputs = sorted(request.submissions_dir.rglob("*.submission.md"))
    duration = time.monotonic() - started
    _record_stage(
        request,
        plan.stage,
        source=source,
        settings_id=settings_id,
        outputs=outputs,
        duration_seconds=duration,
    )
    return StageOutcome(
        stage=plan.stage,
        status=StageStatus.RAN,
        message=f"{len(outputs)} submission(s) ready to grade",
        duration_seconds=duration,
        outputs=tuple(str(path) for path in outputs),
    )


def _answer_key_gate(
    request: PipelineRequest, plan: StagePlan, started: float
) -> StageOutcome | None:
    """Extraction waits for an answer key when the key is the only thing that
    can say where the questions are: no blank template, no region map, and
    scans that do not print Opengrader's own labels. The scan is checked and
    split by then, so the wait costs only the reading — and reading without
    the key would have to be done again once there is one."""
    if (
        request.qa_file is not None
        or request.blank_template is not None
        or request.region_map is not None
        or request.read_without_key
        or not request.extract_source.is_dir()
    ):
        return None
    from opengrader.core.pages.catalog import scans_need_answer_key

    if not scans_need_answer_key(request.extract_source):
        return None
    return StageOutcome(
        stage=plan.stage,
        status=StageStatus.PAUSED,
        message="the scans are split and waiting for an answer key to find their questions",
        detail="\n".join(
            [
                ANSWER_KEY_DETAIL,
                "These pages do not print their points beside each question "
                "number, so the answer key is what says which questions and "
                "parts to look for.",
                "Create the answer key (opengrader create-answer-key) in "
                f"{request.output_dir} and run again; the split is reused.",
                "To read them by whatever numbers the pages print instead, "
                "run again with --read-without-key.",
            ]
        ),
        duration_seconds=time.monotonic() - started,
    )


def _page_order_gate(
    request: PipelineRequest,
    plan: StagePlan,
    progress: PipelineProgress,
    started: float,
) -> StageOutcome | None:
    """Extraction waits until every submission that looks out of order has been
    confirmed, sorted, or rearranged by hand. Sorting afterwards would mean
    extracting again, since an answer's source page is recorded by position."""
    from opengrader.core.pages.order_review import (
        describe_pending,
        keep_pages,
        pending_findings,
        sort_pages,
    )

    split_dir = request.extract_source
    if not split_dir.is_dir():
        return None
    progress.status("Checking for submissions whose page order needs confirming…")
    pending = pending_findings(split_dir)
    if not pending:
        return None
    names = [finding.name for finding in pending]
    if request.reorder_pages:
        sort_pages(split_dir, names)
        return None
    if request.keep_page_order:
        keep_pages(split_dir, names)
        return None
    return StageOutcome(
        stage=plan.stage,
        status=StageStatus.PAUSED,
        message=(
            f"{len(pending)} submission(s) look out of order — confirm their page "
            "order before extraction"
        ),
        detail="\n".join([PAGE_ORDER_DETAIL, *describe_pending(pending), f"in {split_dir}"]),
        duration_seconds=time.monotonic() - started,
    )


def _run_draft_rubrics(
    request: PipelineRequest,
    plan: StagePlan,
    progress: PipelineProgress,
    started: float,
) -> StageOutcome:
    from opengrader.cli import draft_rubric
    from opengrader.core.grading.rubric_draft import render_rubric_markdown

    progress.status("Drafting rubric items for written questions that have none…")
    results = draft_rubric.run_cli(list(plan.argv[1:])) or []
    duration = time.monotonic() - started
    written = [result for result in results if result.written]
    failed = [result for result in results if result.status == "failed"]
    key_name = request.qa_file.name if request.qa_file is not None else "the answer key"

    if written:
        names = ", ".join(result.question_id for result in written)
        detail_lines: list[str] = []
        for result in written:
            detail_lines.append(f"{result.question_id}:")
            detail_lines.extend(
                f"  {line}" for line in render_rubric_markdown(result.options).splitlines()
            )
        if failed:
            detail_lines.append(
                "could not draft: " + ", ".join(result.question_id for result in failed)
            )
        return StageOutcome(
            stage=plan.stage,
            status=StageStatus.PAUSED,
            message=(
                f"drafted rubric items for {names} into {key_name} — review them, "
                "then run again to auto-grade"
            ),
            detail="\n".join(detail_lines),
            duration_seconds=duration,
            outputs=(str(request.qa_file),) if request.qa_file is not None else (),
        )

    if failed:
        message = (
            "could not draft rubric items for "
            + ", ".join(result.question_id for result in failed)
            + "; auto-grading uses their existing rubric"
        )
    else:
        message = "every written question already has partial-credit items"
    return StageOutcome(
        stage=plan.stage,
        status=StageStatus.RAN,
        message=message,
        duration_seconds=duration,
    )


def _run_auto_grade(
    request: PipelineRequest,
    plan: StagePlan,
    progress: PipelineProgress,
    started: float,
) -> StageOutcome:
    from opengrader.cli import automated_grading

    source, settings_id = _stage_ledger_key(request, plan.stage)
    automated_grading.run_cli(
        list(plan.argv[1:]),
        progress=automated_grading.GradingProgress(
            on_file_start=(
                (lambda name, index, total: progress.file_start(name, total))
                if progress.on_file_start is not None
                else None
            ),
            on_question=progress.on_response,
        ),
        status_callback=progress.status,
    )

    outputs = sorted(request.submissions_dir.rglob("*.submission.md"))
    duration = time.monotonic() - started
    _record_stage(
        request,
        plan.stage,
        source=source,
        settings_id=settings_id,
        outputs=outputs,
        duration_seconds=duration,
    )
    key_name = request.qa_file.name if request.qa_file is not None else "the answer key"
    return StageOutcome(
        stage=plan.stage,
        status=StageStatus.RAN,
        message=f"graded {len(outputs)} submission(s) against {key_name}",
        duration_seconds=duration,
        outputs=tuple(str(path) for path in outputs),
    )


def _template_page_count(template: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(template)).pages)


def _scan_detail(reports: Sequence["ScanReport"]) -> str:
    lines: list[str] = []
    for report in reports:
        lines.append(f"{Path(report.source).name}: {report.page_count} page(s)")
        if not report.orientation_available:
            lines.append(f"  ! {report.orientation_note}")
        order = report.page_order_plan
        if not order.checked and order.note:
            lines.append(f"  ! {order.note}")
        for issue in report.issues:
            where = f"page {issue.page}" if issue.page is not None else "document"
            lines.append(f"  [{issue.severity.upper()}] {where}: {issue.detail}")
        if not report.issues:
            lines.append("  no problems found")
    return "\n".join(lines)


def _recorded_outputs(
    request: PipelineRequest, stage: str, plan: StagePlan
) -> tuple[str, ...]:
    if plan.status is not StageStatus.REUSED:
        return ()
    record = _read_ledger(request).get(stage)
    if record is None:
        return ()
    base = request.output_dir
    return tuple(
        str(path if path.is_absolute() else base / path)
        for path in (Path(item) for item in record.outputs)
    )


def _failure_message(exc: BaseException) -> str:
    if isinstance(exc, SystemExit):
        code = exc.code
        if isinstance(code, str) and code.strip():
            return code.strip()
        return f"exited with status {code if code is not None else 0} (see the output above)"
    text = str(exc).strip()
    return f"{exc.__class__.__name__}: {text}" if text else exc.__class__.__name__
