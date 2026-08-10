"""One command for the whole pre-grading pipeline, with resumable stages.

Opengrader is nine composable CLIs, and composability is the right shape for
the tool. It is the wrong shape for the person it is *for*. A professor with a
stack of scans currently has to know that ``scan-check`` runs before
``submission-splitter``, that the splitter's ``--output`` is the extractor's
``--input`` but not its ``--output``, that ``--fix-rotation`` exists at all, and
that ``automated-grading`` wants the answer key the extractor never saw. Every
one of those is a place to get it subtly wrong, and the characteristic failure
of this pipeline is not a crash — it is a cohort of quiet zeros.

This module is the single ordered path through those tools::

    scan-check → split → extract → auto-grade

**It adds no grading logic whatsoever.** Every stage is an existing engine or
CLI called with the arguments that tool already understands; what is new here
is the ordering, the wiring, and the ledger.

**The ledger is the reason this is a module and not a shell script.** Extracting
and auto-grading a class takes real time, and the run that matters is almost
never the first one — it is the re-run after a scan was re-fed, a roster
arrived, or the laptop slept. So each stage records what it did in
``<output-dir>/.opengrader/run-ledger.jsonl`` and a later run *resumes* rather
than repeats. That ledger is ``core/submissions/manifest.py``, unchanged: it
already answers "same input, same output-shaping settings, outputs still on
disk?" correctly, including the subtle part (never invalidating on *changed*
outputs, because the TUI edits them the moment anyone grades). A second
resume model would have been a second place to get that wrong.

Two properties are load-bearing for the callers:

* **Core never prints.** Everything a UI needs comes back through
  :class:`PipelineProgress` callbacks and the returned :class:`PipelineReport`,
  so the same engine drives ``opengrader-run``'s terminal narration and a TUI
  screen without either owning the other's output.
* **A failed run still returns its report.** A stage that raises stops the run,
  but :func:`run_pipeline` returns the stages that *did* land rather than
  propagating. The partial report is the useful artifact: someone whose
  extraction died at submission 180 needs to see that the split succeeded, not
  a traceback.

:func:`plan_pipeline` is the same decision procedure with nothing executed —
what backs ``--dry-run`` and lets a UI show the plan before committing to it.
"""

from __future__ import annotations

import shlex
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

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

if TYPE_CHECKING:  # pragma: no cover - typing only
    from opengrader.core.submissions.scan_check import ScanReport

STAGE_SCAN_CHECK = "scan-check"
STAGE_SPLIT = "split"
STAGE_EXTRACT = "extract"
STAGE_AUTO_GRADE = "auto-grade"

#: The stages in run order. Every consumer iterates this rather than hard-coding
#: the sequence, so inserting a stage later is one edit.
PIPELINE_STAGES: tuple[str, ...] = (
    STAGE_SCAN_CHECK,
    STAGE_SPLIT,
    STAGE_EXTRACT,
    STAGE_AUTO_GRADE,
)

# The console-script name each stage would be typed as. It is ``argv[0]`` of a
# StagePlan's argv so a dry-run prints a command a human could paste, while the
# stage itself hands ``argv[1:]`` to that CLI's ``run_cli`` in-process — which is
# what stops the plan and the run from ever drifting apart.
_STAGE_COMMANDS = {
    STAGE_SPLIT: "submission-splitter",
    STAGE_EXTRACT: "submission-extractor",
    STAGE_AUTO_GRADE: "automated-grading",
}


class StageStatus(str, Enum):
    """How one stage ended. ``str``-valued so it serializes as itself."""

    RAN = "ran"  # did the work this time
    REUSED = "reused"  # the ledger says the previous run's work still stands
    SKIPPED = "skipped"  # not applicable to this run (no answer key, input already split, …)
    BLOCKED = "blocked"  # halted on a pre-flight finding; `force` overrides
    FAILED = "failed"


@dataclass(frozen=True)
class LlmOptions:
    """Whether (and which) models take part, in one value.

    Two switches, not one, because AI Setup asks about the two jobs
    separately and a professor's answers genuinely differ: reading
    handwriting is useful on a scanned exam whose rubric settles every
    answer on its own (no grading model needed), while suggesting grades is
    useful on a born-digital exam that needs no transcription at all. The two
    *model names* stay separate for the reason already documented: a vision
    model transcribes, a text model grades, and they are genuinely different
    models.

    Empty strings mean "whatever that tool already defaults to", so a default
    changing in one CLI is not silently overridden here.
    """

    read_handwriting: bool = False
    grade_written: bool = False
    vision_model: str = ""
    text_model: str = ""
    host: str = ""
    auto_ollama: bool = False
    min_confidence: float | None = None

    @property
    def enabled(self) -> bool:
        """True when either job would call a model.

        Read-only: this used to be the one stored switch, and is kept as a
        derived property so any caller that only needs "would a model run at
        all" (a run-plan summary, a fingerprint of "did AI touch this") does
        not have to spell out the ``or`` itself.
        """
        return self.read_handwriting or self.grade_written


@dataclass(frozen=True)
class PipelineRequest:
    """Everything one run needs, and nothing about how it is displayed."""

    scan: Path
    output_dir: Path
    qa_file: Path | None = None
    roster: Path | None = None
    blank_template: Path | None = None
    pages_per_submission: int | None = None
    llm: LlmOptions = LlmOptions()
    jobs: int | None = None
    auto_grade: bool = True
    force: bool = False
    fresh: bool = False

    @property
    def split_dir(self) -> Path:
        """Per-student PDFs. Inside the cohort folder, never beside the scan.

        Everything one run produces lives under ``output_dir`` so the whole
        thing can be moved, copied or deleted as a unit — including the ledger,
        which stores its outputs relative to exactly this directory.
        """
        return self.output_dir / "split"

    @property
    def submissions_dir(self) -> Path:
        """The ``.submission.md`` folder the TUI is pointed at."""
        return self.output_dir / "submissions"

    @property
    def input_is_split(self) -> bool:
        """True when ``scan`` is already a folder of per-student PDFs.

        A legitimate starting point (someone else split them, or ``photo-import``
        produced them), and the one thing that makes the split stage nonsense
        rather than merely unnecessary.
        """
        return self.scan.is_dir()

    @property
    def extract_source(self) -> Path:
        """Where the extractor reads from: the split output, or the input itself."""
        return self.scan if self.input_is_split else self.split_dir


@dataclass(frozen=True)
class StagePlan:
    """What a stage *would* do, decided without doing any of it.

    ``argv`` is the command line the stage would run, with the console-script
    name first (empty for a stage that calls an engine in-process instead).
    ``status`` is a **forecast**, not a result — see :func:`plan_pipeline` on why
    it can differ from what the run actually reports.
    """

    stage: str
    will_run: bool
    reason: str
    argv: tuple[str, ...] = ()
    status: StageStatus = StageStatus.RAN

    @property
    def command(self) -> str:
        """The plan's argv as a pasteable shell command (``""`` when in-process)."""
        return shlex.join(self.argv) if self.argv else ""


@dataclass(frozen=True)
class StageOutcome:
    """What one stage actually did.

    ``message`` is one line and belongs in a status bar; ``detail`` is the
    multi-line body (a scan report, the failing command) that a user opens when
    the one-liner is not enough. Keeping them apart is what lets the same
    outcome render in a TUI footer and in a terminal transcript.
    """

    stage: str
    status: StageStatus
    message: str
    detail: str = ""
    duration_seconds: float = 0.0
    outputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class PipelineReport:
    """The whole run, as data.

    Always complete enough to act on, even when the run stopped early: the
    stages that ran are here whether the last one succeeded or not.
    """

    stages: tuple[StageOutcome, ...]
    submissions_dir: Path
    qa_file: Path | None

    @property
    def ok(self) -> bool:
        """No stage failed or blocked. Skipped and reused stages are fine."""
        return not any(
            outcome.status in (StageStatus.FAILED, StageStatus.BLOCKED)
            for outcome in self.stages
        )

    @property
    def submission_count(self) -> int:
        """Gradeable submissions on disk right now.

        Counted from the filesystem rather than from the extract stage's own
        report, because a reused or partially-resumed run legitimately extracts
        nothing while leaving a full cohort in place — and it is the cohort, not
        this run's share of it, that decides whether there is anything to grade.
        """
        return sum(1 for _ in self.submissions_dir.rglob("*.submission.md"))

    @property
    def ready_to_grade(self) -> bool:
        return self.submission_count > 0

    def stage(self, name: str) -> StageOutcome | None:
        for outcome in self.stages:
            if outcome.stage == name:
                return outcome
        return None


@dataclass
class PipelineProgress:
    """UI-agnostic progress reporter, in ``ExtractionProgress``'s idiom.

    Every callback is optional, so a bare ``PipelineProgress()`` is a no-op and
    this module can report unconditionally. ``on_file_start``/``on_response``
    are forwarded verbatim to the inner stages' own progress reporters, so a
    caller that already draws an extraction progress bar keeps drawing it.
    """

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


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #


def plan_pipeline(request: PipelineRequest) -> tuple[StagePlan, ...]:
    """What each stage would do, with nothing executed and nothing written.

    Reads only: it stats the input, fingerprints it, and folds the run ledger,
    which is what lets a ``--dry-run`` say "split would be reused" instead of
    the far less useful "split would run".

    ``status`` is a **forecast**. :func:`run_pipeline` re-derives each stage's
    ledger key at the moment that stage is reached, because an earlier stage can
    change the answer: extraction adds submissions, and a grown cohort must
    un-reuse auto-grading or the students who arrived this run would never be
    graded at all. The forecast is exact in the case that matters — a re-run of
    an unchanged cohort, where extraction adds nothing.
    """
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
            # The path is deliberately left out: it is already in the plan's
            # command line, and a summary line that wraps to three terminal rows
            # is one nobody reads.
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
        # Never reused, and that is not an oversight — see `_run_extract`.
        return StagePlan(
            stage=stage,
            will_run=True,
            reason="always runs; --resume skips the individual PDFs already done",
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
    """The command line a stage would run. Pure; builds strings only."""
    if stage == STAGE_SPLIT:
        return _split_argv(request)
    if stage == STAGE_EXTRACT:
        return _extract_argv(request)
    if stage == STAGE_AUTO_GRADE:
        return _auto_grade_argv(request)
    # scan-check drives the engine directly rather than the CLI — it needs the
    # findings as data, not as printed text.
    return ()


def _split_argv(request: PipelineRequest) -> tuple[str, ...]:
    """Split the bundle, correcting the two faults that grade to zero in silence.

    ``--fix-rotation`` and ``--fix-order`` are not options here. They are what
    the TUI's setup screen already defaults on for this same user, for the
    reason ``core/submissions/scan_check.py`` opens with: an upside-down page
    still crops, still transcribes (to nothing) and still grades — to zero — and
    a swapped pair produces confident marks against the wrong questions. Both
    fixes are evidence-gated inside the splitter, so a scan that turns out to be
    fine costs only the check that said so.

    ``--extra-work-pages`` records which sheets are scratch work rather than
    answers, so the extractor can stamp them into each ``.submission.md``; that
    knowledge exists nowhere else and dies at the split if nobody asks for it.
    """
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
    return tuple(argv)


def _extract_argv(request: PipelineRequest) -> tuple[str, ...]:
    """Transcribe every submission, resuming per PDF.

    ``--llm-escalate`` rather than ``--llm-responses`` when a model is allowed:
    Tesseract reads every crop and the model is asked only about the readings it
    doubted. On the real ``cs146-quiz1`` scan that is *zero* model calls where
    the plain flag spends one per answer, and it cannot lose an answer — a model
    that reads nothing leaves the OCR text standing. Spending a vision model on
    answers that were already read at 90% is the single easiest way to turn a
    two-minute run into an hour-long one.
    """
    argv = [
        _STAGE_COMMANDS[STAGE_EXTRACT],
        "--input",
        str(request.extract_source),
        "--output",
        str(request.submissions_dir),
        "--resume",
    ]
    if request.roster is not None:
        argv += ["--id-file", str(request.roster)]
    if request.blank_template is not None:
        argv += ["--blank-template", str(request.blank_template)]
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
    return tuple(argv)


def _auto_grade_argv(request: PipelineRequest) -> tuple[str, ...]:
    """Grade what can be graded without a person, leaving the rest for the TUI."""
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
    return tuple(argv)


# --------------------------------------------------------------------------- #
# The ledger
# --------------------------------------------------------------------------- #


def _read_ledger(request: PipelineRequest) -> dict[str, ExtractionRecord]:
    """The run ledger, or nothing at all when ``--fresh`` was asked for."""
    if request.fresh:
        return {}
    return read_manifest(request.output_dir, filename=RUN_LEDGER_FILENAME)


def _stage_ledger_key(request: PipelineRequest, stage: str) -> tuple[str, str]:
    """``(source_sha256, settings_id)`` — what makes a stage's record reusable.

    The settings fingerprint carries **only what shapes that stage's output**,
    following ``manifest.py``'s own rule. Deliberately left out everywhere:
    ``jobs`` (worker count changes speed, not results), the Ollama host and
    ``auto_ollama`` (where the model lives, not which model), ``force`` and
    ``fresh`` (decisions about this run, not about its output), and the progress
    callbacks. Including any of them would make a professor who added
    ``--jobs 8`` re-split and re-grade a class for nothing.
    """
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
                    # Both change which checks can run at all: without a
                    # submission length there is no page-order window and no
                    # divisibility check, so a run that supplies one is not the
                    # same check as a run that did not.
                    "pages_per_submission": request.pages_per_submission,
                    "blank_template": template,
                }
            ),
        )

    if stage == STAGE_SPLIT:
        return (
            input_fingerprint(request.scan),
            settings_fingerprint(
                {
                    "pages_per_submission": request.pages_per_submission,
                    "blank_template": template,
                    # Recorded even though this module always sets them, so a
                    # ledger written by a future version that makes them
                    # optional cannot be mistaken for this one's output.
                    "fix_rotation": True,
                    "fix_order": True,
                    "extra_work_pages": True,
                }
            ),
        )

    if stage == STAGE_EXTRACT:
        return (
            input_fingerprint(request.extract_source),
            settings_fingerprint(
                {
                    "roster": (
                        file_fingerprint(request.roster)
                        if request.roster is not None
                        else ""
                    ),
                    "blank_template": template,
                    "llm_escalate": llm.read_handwriting,
                    "vision_model": llm.vision_model,
                }
            ),
        )

    return (
        file_fingerprint(request.qa_file) if request.qa_file is not None else "",
        settings_fingerprint(
            {
                "llm": llm.grade_written,
                "text_model": llm.text_model,
                "min_confidence": llm.min_confidence,
                # Which submissions exist, by name — *not* what is in them. This
                # is the one addition to "answer key + grading settings" and it
                # is not optional: extraction can add students to a folder that
                # was already graded, and without this the ledger would report
                # the pass reusable and the new arrivals would never be graded
                # at all. Names rather than contents on purpose — the TUI
                # rewrites these files as soon as anyone grades, and keying on
                # contents would re-run the expensive model pass after every
                # grading session for no change in the work to be done.
                "cohort": _cohort_fingerprint(request.submissions_dir),
            }
        ),
    )


def _stage_source(request: PipelineRequest, stage: str) -> Path:
    """The path whose fingerprint that stage's record is keyed on.

    Written into the record's human-readable ``source`` so a ledger line names
    what it actually depends on. Recording ``--scan`` for every stage would put
    the bundle PDF next to the auto-grade record, whose reuse turns entirely on
    the answer key — a log that reads plausibly and says the wrong thing.
    """
    if stage == STAGE_EXTRACT:
        return request.extract_source
    if stage == STAGE_AUTO_GRADE and request.qa_file is not None:
        return request.qa_file
    return request.scan


def _cohort_fingerprint(submissions_dir: Path) -> str:
    """A digest of *which* submissions exist, insensitive to their contents."""
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
    """Append this stage's completion to the run ledger.

    Only ever called after the stage's work is on disk, exactly as the
    extraction manifest is — the absence of a record has to keep meaning "not
    done", or a resumed run would skip work that never happened.
    """
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
    """The concrete "here is what resume bought you" line, per stage.

    Concrete on purpose: "split · reused" teaches nobody anything, while
    "reused 42 split PDFs from the previous run" tells a user both that resume
    worked and roughly how big their class is.
    """
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
    """Why this stage is about to do the work, in the user's terms."""
    if request.fresh:
        return "--fresh: the ledger is ignored and every stage redone"
    if record is None:
        return "no record of this stage having run here before"
    return "the input or the settings changed since the last run"


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #


def run_pipeline(
    request: PipelineRequest, *, progress: PipelineProgress | None = None
) -> PipelineReport:
    """Run the stages in order, resuming what the ledger says still stands.

    Stops at the first stage that fails or blocks and returns what happened up
    to that point — the later stages are simply absent from the report, which is
    the honest representation of a run that did not reach them.
    """
    reporter = progress or PipelineProgress()
    request.output_dir.mkdir(parents=True, exist_ok=True)

    outcomes: list[StageOutcome] = []
    for stage in PIPELINE_STAGES:
        # Re-planned per stage rather than once up front: an earlier stage
        # changes the world the next one is judged against (see `plan_pipeline`).
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
            except (SystemExit, Exception) as exc:  # noqa: BLE001 - see docstring
                outcome = StageOutcome(
                    stage=stage,
                    status=StageStatus.FAILED,
                    message=_failure_message(exc),
                    detail=plan.command,
                    duration_seconds=time.monotonic() - started,
                )

        outcomes.append(outcome)
        reporter.stage_finish(outcome)
        if outcome.status in (StageStatus.FAILED, StageStatus.BLOCKED):
            break

    return PipelineReport(
        stages=tuple(outcomes),
        submissions_dir=request.submissions_dir,
        qa_file=request.qa_file,
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
    return _run_auto_grade(request, plan, progress, started)


def _run_scan_check(
    request: PipelineRequest,
    plan: StagePlan,
    progress: PipelineProgress,
    started: float,
) -> StageOutcome:
    """Pre-flight the scan, and decide from the findings whether to continue.

    Driven through ``core/submissions/scan_check.py``'s engine rather than the
    ``scan-check`` CLI because this stage's output is a *decision*, not a
    printout: a high-severity finding stops the run, and scraping that verdict
    back out of formatted text would be both fragile and slower than not
    printing it in the first place.

    The full check runs — orientation included — even though the OSD pass is the
    slow part. Skipping it would remove precisely the check this whole feature
    exists for: the silent failures. (The split stage then re-reads the same
    pages, but since 2026-08-08 ``core/render_cache.py`` shares one raster per
    page across the run, so the second pass costs the measurements and not the
    rendering.)
    """
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

    # What "pages per submission" asserts depends on what was pointed at — the
    # same distinction `cli/scan_check.py` makes, and for the same reason. One
    # PDF is a bundle of students, so the claim is divisibility; a directory is
    # already-split submissions, so it is each file's exact length.
    is_bundle = not request.input_is_split
    bundle_pages = request.pages_per_submission if is_bundle else None
    per_file_pages = request.pages_per_submission if not is_bundle else None
    if request.blank_template is not None:
        per_file_pages = _template_page_count(request.blank_template)

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
        # Not recorded in the ledger. A blocking finding that a later run could
        # inherit as "already checked, fine" would defeat the check entirely —
        # and the same applies to a forced run, so `--force` is a decision the
        # user makes each time rather than one that gets written down.
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
    """Cut the bundle into per-student PDFs."""
    from opengrader.cli import submission_splitter

    # Fingerprinted *before* the stage runs. The splitter writes a rotation-fixed
    # copy under the output directory, and the input itself is never touched, but
    # taking the key first keeps the recorded identity the one the decision was
    # made on.
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
    """Turn per-student PDFs into ``.submission.md`` files.

    **This is the one stage whose resume is not the run ledger's doing, and a
    reader who assumes otherwise will mis-read everything below.** The extractor
    keeps its own manifest at
    ``<submissions>/.opengrader/extraction-manifest.jsonl`` with one record per
    PDF, so ``--resume`` — always passed — already skips exactly the submissions
    that finished, one level finer than a stage ever could. This stage therefore
    *always executes*, and the record it writes to the run ledger is purely
    informational: something for a later reader (or a report) to see that
    extraction happened, its duration, and what it produced. Nothing consults
    it, which is why :func:`_plan_stage` short-circuits before the reuse check
    rather than relying on the record never matching.

    The alternative — a stage-level reuse check — would be strictly worse: it
    can only answer "was the whole folder done under these settings?", so a run
    interrupted at submission 180 of 200 would come back either "reused" (losing
    twenty students in silence) or "not reused" (re-extracting a hundred and
    eighty).
    """
    from opengrader.cli import submission_extractor
    from opengrader.core.submissions.processor import ExtractionProgress

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


def _run_auto_grade(
    request: PipelineRequest,
    plan: StagePlan,
    progress: PipelineProgress,
    started: float,
) -> StageOutcome:
    """Grade everything a rubric (and optionally a model) can settle alone.

    Re-running this is *safe* — ``cli/automated_grading.py`` never overwrites an
    existing grade — but it is not *cheap*: a second pass re-asks the model about
    every answer that is still ungraded, which on a class of two hundred is the
    single most expensive thing this pipeline does. Hence the ledger entry,
    keyed on the answer key, the grading settings and which submissions exist.
    """
    from opengrader.cli import automated_grading

    source, settings_id = _stage_ledger_key(request, plan.stage)
    automated_grading.run_cli(
        list(plan.argv[1:]),
        # GradingProgress ticks per *question* rather than per response crop;
        # both are "one unit of the current file finished", which is exactly
        # what `on_response` reports, so the two map cleanly onto each other.
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


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _template_page_count(template: Path) -> int:
    """Page count of the blank template — how long one submission should be."""
    from pypdf import PdfReader

    return len(PdfReader(str(template)).pages)


def _scan_detail(reports: Sequence["ScanReport"]) -> str:
    """The pre-flight findings as a readable block, with the caveats first.

    What was *not* checked is printed ahead of the findings on purpose: an
    absent rotation report must never read as a passed one (the rule
    ``core/submissions/scan_check.py`` is built around), and the caller pasting
    this into a status pane has no other way to learn it.
    """
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
    """The outputs a reused stage is standing behind, as usable paths.

    The ledger stores them relative to the output dir (so a cohort folder
    survives being moved); a caller wants something it can open.
    """
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
    """One line naming what went wrong, whichever way the stage gave up.

    ``SystemExit`` gets its own branch because several of these CLIs report a
    bad input by printing it and calling ``sys.exit(1)``; ``str()`` on that is
    the bare string ``"1"``, which as a status line says nothing at all.
    """
    if isinstance(exc, SystemExit):
        code = exc.code
        if isinstance(code, str) and code.strip():
            return code.strip()
        return f"exited with status {code if code is not None else 0} (see the output above)"
    text = str(exc).strip()
    return f"{exc.__class__.__name__}: {text}" if text else exc.__class__.__name__
