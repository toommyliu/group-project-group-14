"""Run the extraction of many submission PDFs in parallel.

Extraction is *embarrassingly parallel*: every PDF is read from its own file and
everything it produces (the ``.submission.md`` plus the ``responses/`` and
``identifiers/`` crops) lands in its own ``<output-dir>/<stem>/`` folder. No two
submissions read or write the same byte, so N of them can be processed at once
with no locking at all.

**Why processes and not threads.** The two things that dominate the wall clock
are page rendering (PyMuPDF/MuPDF) and OCR. MuPDF is explicitly documented as
not thread-safe, and PyMuPDF holds the GIL across its calls, so threads would
add risk and buy nothing for the render half of the work. Separate processes
sidestep both problems and give true multi-core rendering. The ``spawn`` start
method is requested explicitly so behaviour is identical on macOS, Linux, and
Windows instead of inheriting the parent's memory on Linux only.

**Getting progress back out.** A worker process cannot call the parent's
``ExtractionProgress`` callbacks directly, so each one pushes plain tuples onto
a shared queue and a small drain thread in the parent replays them onto the real
reporter. From the caller's point of view the progress bar behaves exactly as it
did in the sequential pipeline.

**Picking a worker count.** :func:`resolve_worker_count` is deliberately
conservative, because "as many workers as cores" is the wrong answer here twice
over: a 300 DPI page render costs ~25 MB per page held in memory, and when the
vision model is in play the real bottleneck is Ollama, which serves only a few
generations concurrently — piling more workers on just makes requests queue
until they hit the per-request timeout.
"""

from __future__ import annotations

import multiprocessing
import os
import queue as queue_module
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal, Sequence

from opengrader.core.ocr.math_ocr import DEFAULT_MATH_BACKEND
from opengrader.core.ocr.ollama_vlm import OllamaVisionConfig

from .manifest import relative_outputs
from .processor import (
    ExtractionProgress,
    _RosterEntry,
    build_student_submission_document_unified,
)
from .template import AssignmentTemplate
from .student_file import StudentSubmissionDocument
from .transcription import tally_reading_sources

if TYPE_CHECKING:  # pragma: no cover - typing only
    from opengrader.core.bubble_sheet import BubbleSheetLayout
    # OcrConfig is annotation-only here (ExtractionJob.ocr_config); a real
    # import would resolve opengrader.core.ocr's cv2/fitz-backed extractor
    # module just to type one dataclass field, taxing every TUI startup since
    # this module is loaded eagerly by core/submissions/__init__.py.
    from opengrader.core.ocr import OcrConfig

# Auto worker caps. Beyond these, extra workers cost memory (and, with the
# vision model, request-queue latency) without finishing the batch any sooner.
AUTO_WORKER_CAP = 8
VLM_AUTO_WORKER_CAP = 4

# How long the drain thread waits for a progress event before re-checking
# whether the batch has finished. Short enough that shutdown feels instant.
_DRAIN_POLL_SECONDS = 0.1

OutcomeStatus = Literal["extracted", "skipped", "failed"]


@dataclass(frozen=True)
class ExtractionJob:
    """Everything one worker needs to extract one PDF, and nothing else.

    Every field is a plain value or a plain dataclass so the whole job can be
    pickled and shipped to another process. Callables (the interactive
    duplicate-name prompt, progress callbacks) are deliberately absent — they
    cannot cross a process boundary, and the runner handles each of them
    separately.
    """

    pdf_path: Path
    output_dir: Path
    ocr_config: OcrConfig | None = None
    dpi: int = 300
    split_subquestions: bool = True
    ollama_config: OllamaVisionConfig | None = None
    skip_ollama_qa: bool = False
    save_layout_debug: bool = False
    blank_assignment_template: AssignmentTemplate | None = None
    roster: list[_RosterEntry] | None = None
    update: bool = False
    refresh_layout: bool = False
    llm_responses: bool = False
    llm_math: bool = False
    # When set, the crop readers run cheapest-first and a model is asked only
    # about readings below this confidence (`--llm-escalate`). A plain float so
    # the job stays picklable, like every other field here.
    escalate_floor: float | None = None
    # Which math-OCR backend transcribes the crops (`core/ocr/math_ocr.py`).
    # A **name**, not a callable: every job field has to pickle into a worker,
    # and resolving it on the far side is also what gives each process its own
    # cached model instead of shipping one across.
    math_backend: str = DEFAULT_MATH_BACKEND
    default_section: str | None = None
    section_by_id: dict[str, str] | None = None
    # OMR mode: the geometry of the generated answer sheet these scans are.
    # A frozen dataclass tree of plain values, so it pickles into a worker like
    # every other field here.
    bubble_sheet_layout: "BubbleSheetLayout | None" = None
    # The batch was printed with a different question order per sheet, so a
    # sheet whose form key cannot be read must be refused rather than read
    # against the wrong rows.
    bubble_shuffled: bool = False
    # A form key read off the paper by hand, for the one sheet whose QR will
    # not scan. Only meaningful for a single-file run.
    bubble_form_key: str | None = None
    # Carried through untouched so the caller can key its resume ledger off the
    # outcome without hashing the input a second time.
    source_sha256: str = ""

    @property
    def key(self) -> str:
        """The manifest key / output folder name for this PDF."""
        return self.pdf_path.stem


@dataclass(frozen=True)
class ExtractionOutcome:
    """What happened to one PDF, in a form that survives pickling."""

    pdf_path: Path
    key: str
    status: OutcomeStatus
    markdown_path: Path | None = None
    student_id: str = ""
    student_name: str = ""
    section: str | None = None
    outputs: tuple[str, ...] = ()
    error: str = ""
    duration_seconds: float = 0.0
    source_sha256: str = ""
    # How many answers each reader produced, counted from the document that was
    # actually written. A tuple of pairs rather than a dict so it pickles back
    # from a worker unchanged, and so the aggregate order stays deterministic.
    reading_counts: tuple[tuple[str, int], ...] = ()

    @property
    def identity(self) -> str:
        """Human-facing 'Name (id)' label, or a clear stand-in when unknown."""
        if self.student_id:
            return f"{self.student_name} ({self.student_id})"
        return "Unidentified"


@dataclass
class BatchReport:
    """Aggregate result of a batch, in the caller's original input order."""

    outcomes: list[ExtractionOutcome] = field(default_factory=list)
    workers: int = 1

    @property
    def extracted(self) -> list[ExtractionOutcome]:
        return [o for o in self.outcomes if o.status == "extracted"]

    @property
    def skipped(self) -> list[ExtractionOutcome]:
        return [o for o in self.outcomes if o.status == "skipped"]

    @property
    def failed(self) -> list[ExtractionOutcome]:
        return [o for o in self.outcomes if o.status == "failed"]


def resolve_worker_count(
    requested: int | None,
    job_count: int,
    *,
    vlm_active: bool,
) -> int:
    """Decide how many worker processes to run.

    ``requested`` of ``None`` means "auto". Auto never exceeds the number of
    jobs (spawning a process that has nothing to do is pure loss), the CPU
    count, or the relevant cap — :data:`VLM_AUTO_WORKER_CAP` when the vision
    model will be called, :data:`AUTO_WORKER_CAP` otherwise. An explicit
    ``requested`` is honoured but still clamped to the job count.
    """
    if job_count <= 1:
        return 1
    if requested is not None:
        return max(1, min(requested, job_count))
    cpus = os.cpu_count() or 1
    cap = VLM_AUTO_WORKER_CAP if vlm_active else AUTO_WORKER_CAP
    return max(1, min(cpus, cap, job_count))


def document_outputs(
    output_dir: str | Path,
    document: StudentSubmissionDocument,
    markdown_path: Path,
) -> tuple[str, ...]:
    """List every file an extraction produced, for the resume ledger.

    The markdown file plus the identity crops plus one crop per question
    response. Recording the crops (not just the markdown) is what lets a resumed
    run notice that someone deleted the ``responses/`` folder and re-extract
    that submission instead of trusting a ``.submission.md`` whose images are
    all broken links.
    """
    # Every recorded image path is stored relative to the submission dir (see
    # `_save_field_crop` / `_question_with_scan_crop`), so resolve them against
    # it before handing them to the ledger.
    submission_dir = markdown_path.parent
    candidates: list[str | Path] = [markdown_path]
    for image in (document.name_image_path, document.id_image_path):
        if image:
            candidates.append(submission_dir / image)
    for question in document.questions:
        if question.response_image_path:
            candidates.append(submission_dir / question.response_image_path)
    return relative_outputs(output_dir, candidates)


def _execute_job(
    job: ExtractionJob,
    event_queue: "queue_module.Queue | None" = None,
    build: Callable[..., tuple[StudentSubmissionDocument, Path]] | None = None,
) -> ExtractionOutcome:
    """Extract one PDF and describe the result. Never raises.

    This is the function that runs inside a worker process, so it must be
    importable at module level and must not let an exception escape — a worker
    that dies takes the pool down with it, whereas a failed *outcome* only costs
    that one submission. Progress is reported by pushing tuples onto
    ``event_queue``; with no queue (the sequential path) the reporter passed by
    the caller is used directly.
    """
    started = time.monotonic()

    if event_queue is not None:
        progress = ExtractionProgress(
            on_file_start=lambda name, count: event_queue.put(("file_start", name, count)),
            on_response=lambda name, question_id: event_queue.put(
                ("response", name, question_id)
            ),
        )

        def status(message: str) -> None:
            event_queue.put(("status", message, None))
    else:  # pragma: no cover - the sequential path supplies its own reporter
        progress = ExtractionProgress()

        def status(message: str) -> None:
            return None

    builder = build or build_student_submission_document_unified
    try:
        document, markdown_path = builder(
            pdf_path=job.pdf_path,
            output_dir=job.output_dir,
            ocr_config=job.ocr_config,
            dpi=job.dpi,
            split_subquestions=job.split_subquestions,
            ollama_config=job.ollama_config,
            skip_ollama_qa=job.skip_ollama_qa,
            save_layout_debug=job.save_layout_debug,
            blank_assignment_template=job.blank_assignment_template,
            roster=job.roster,
            update=job.update,
            refresh_layout=job.refresh_layout,
            resolve_ambiguous=None,
            llm_responses=job.llm_responses,
            llm_math=job.llm_math,
            math_backend=job.math_backend,
            escalate_floor=job.escalate_floor,
            progress=progress,
            progress_callback=status,
            default_section=job.default_section,
            section_by_id=job.section_by_id,
            bubble_sheet_layout=job.bubble_sheet_layout,
            bubble_shuffled=job.bubble_shuffled,
            bubble_form_key=job.bubble_form_key,
        )
    except Exception as exc:  # noqa: BLE001 - one bad scan must not kill the batch
        return ExtractionOutcome(
            pdf_path=job.pdf_path,
            key=job.key,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            duration_seconds=time.monotonic() - started,
            source_sha256=job.source_sha256,
        )

    return ExtractionOutcome(
        pdf_path=job.pdf_path,
        key=job.key,
        status="extracted",
        markdown_path=markdown_path,
        student_id=document.student_id or "",
        student_name=document.student_name or "",
        section=document.section,
        outputs=document_outputs(job.output_dir, document, markdown_path),
        duration_seconds=time.monotonic() - started,
        source_sha256=job.source_sha256,
        reading_counts=tally_reading_sources(
            question.response_source
            for question in document.questions
            if (question.response_text or "").strip()
        ).counts,
    )


def _replay_events(
    event_queue: "queue_module.Queue",
    stop: threading.Event,
    progress: ExtractionProgress,
    status_callback: Callable[[str], None] | None,
) -> None:
    """Drain worker progress events onto the parent's reporter until told to stop.

    Runs on its own thread so the main thread stays free to collect finished
    futures. After ``stop`` is set it makes one last non-blocking pass so events
    already in flight when the batch ended are not dropped.
    """

    def dispatch(event: tuple) -> None:
        kind, first, second = event
        if kind == "file_start":
            progress.file_start(first, int(second))
        elif kind == "response":
            progress.response(first, second)
        elif kind == "status" and status_callback is not None:
            status_callback(first)

    while not stop.is_set():
        try:
            dispatch(event_queue.get(timeout=_DRAIN_POLL_SECONDS))
        except queue_module.Empty:
            continue
        except (EOFError, OSError, ValueError):  # queue closed during shutdown
            return
    while True:
        try:
            dispatch(event_queue.get_nowait())
        except queue_module.Empty:
            return
        except (EOFError, OSError, ValueError):
            return


def run_extraction_batch(
    jobs: Sequence[ExtractionJob],
    *,
    workers: int = 1,
    progress: ExtractionProgress | None = None,
    status_callback: Callable[[str], None] | None = None,
    on_outcome: Callable[[ExtractionOutcome], None] | None = None,
    build: Callable[..., tuple[StudentSubmissionDocument, Path]] | None = None,
    resolve_ambiguous: Callable | None = None,
    fail_fast: bool = False,
) -> BatchReport:
    """Extract every job, on ``workers`` processes, and report what happened.

    ``on_outcome`` fires on the calling thread as each PDF finishes — that is
    where the caller prints its per-submission line and appends to the resume
    ledger, so a run killed halfway still has a correct ledger for everything
    that did finish. Outcomes fire in *completion* order; the returned report
    restores the caller's original input order.

    Two arguments force single-process execution because neither can cross a
    process boundary, and both are meaningless in parallel anyway:

    - ``build`` swaps in a different builder (tests use it to stub out the OCR
      work) — an arbitrary callable is not picklable.
    - ``resolve_ambiguous`` is the interactive duplicate-name prompt, which
      needs the terminal; several workers racing for stdin would be unusable.

    ``fail_fast`` stops scheduling further work after the first failure. By
    default the batch runs to completion instead, since on a long overnight run
    one unreadable scan should not throw away the other 199 submissions — the
    caller reports the failures and can re-run with ``--resume`` afterwards.
    """
    progress = progress or ExtractionProgress()
    if not jobs:
        return BatchReport(outcomes=[], workers=0)

    forces_inline = build is not None or resolve_ambiguous is not None
    effective_workers = 1 if forces_inline else max(1, min(workers, len(jobs)))

    if effective_workers == 1:
        return _run_sequential(
            jobs,
            progress=progress,
            status_callback=status_callback,
            on_outcome=on_outcome,
            build=build,
            resolve_ambiguous=resolve_ambiguous,
            fail_fast=fail_fast,
        )
    return _run_parallel(
        jobs,
        workers=effective_workers,
        progress=progress,
        status_callback=status_callback,
        on_outcome=on_outcome,
        fail_fast=fail_fast,
    )


def _run_sequential(
    jobs: Sequence[ExtractionJob],
    *,
    progress: ExtractionProgress,
    status_callback: Callable[[str], None] | None,
    on_outcome: Callable[[ExtractionOutcome], None] | None,
    build: Callable[..., tuple[StudentSubmissionDocument, Path]] | None,
    resolve_ambiguous: Callable | None,
    fail_fast: bool,
) -> BatchReport:
    """One PDF at a time in this process — the original pipeline, unchanged.

    Kept as a real code path (not "a pool of one") because it is what runs for a
    single PDF, for ``--jobs 1``, and whenever the interactive duplicate-name
    prompt needs a terminal, and because it avoids all process-startup cost.
    """
    report = BatchReport(workers=1)
    for job in jobs:
        # The reporter and status sink are already in this process, so they are
        # wired straight through instead of going via the event queue.
        outcome = _execute_job_inline(
            job,
            progress=progress,
            status_callback=status_callback,
            build=build,
            resolve_ambiguous=resolve_ambiguous,
        )
        report.outcomes.append(outcome)
        if on_outcome is not None:
            on_outcome(outcome)
        if fail_fast and outcome.status == "failed":
            break
    return report


def _execute_job_inline(
    job: ExtractionJob,
    *,
    progress: ExtractionProgress,
    status_callback: Callable[[str], None] | None,
    build: Callable[..., tuple[StudentSubmissionDocument, Path]] | None,
    resolve_ambiguous: Callable | None = None,
) -> ExtractionOutcome:
    """In-process variant of :func:`_execute_job` that uses the real callbacks."""
    started = time.monotonic()
    builder = build or build_student_submission_document_unified
    status: Callable[[str], None] = status_callback or (lambda _message: None)
    try:
        document, markdown_path = builder(
            pdf_path=job.pdf_path,
            output_dir=job.output_dir,
            ocr_config=job.ocr_config,
            dpi=job.dpi,
            split_subquestions=job.split_subquestions,
            ollama_config=job.ollama_config,
            skip_ollama_qa=job.skip_ollama_qa,
            save_layout_debug=job.save_layout_debug,
            blank_assignment_template=job.blank_assignment_template,
            roster=job.roster,
            update=job.update,
            refresh_layout=job.refresh_layout,
            resolve_ambiguous=resolve_ambiguous,
            llm_responses=job.llm_responses,
            llm_math=job.llm_math,
            math_backend=job.math_backend,
            escalate_floor=job.escalate_floor,
            progress=progress,
            progress_callback=status,
            default_section=job.default_section,
            section_by_id=job.section_by_id,
            bubble_sheet_layout=job.bubble_sheet_layout,
            bubble_shuffled=job.bubble_shuffled,
            bubble_form_key=job.bubble_form_key,
        )
    except Exception as exc:  # noqa: BLE001 - reported as a failed outcome
        return ExtractionOutcome(
            pdf_path=job.pdf_path,
            key=job.key,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            duration_seconds=time.monotonic() - started,
            source_sha256=job.source_sha256,
        )
    return ExtractionOutcome(
        pdf_path=job.pdf_path,
        key=job.key,
        status="extracted",
        markdown_path=markdown_path,
        student_id=document.student_id or "",
        student_name=document.student_name or "",
        section=document.section,
        outputs=document_outputs(job.output_dir, document, markdown_path),
        duration_seconds=time.monotonic() - started,
        source_sha256=job.source_sha256,
        reading_counts=tally_reading_sources(
            question.response_source
            for question in document.questions
            if (question.response_text or "").strip()
        ).counts,
    )


def _worker_render_cache_bytes(workers: int) -> int:
    """The render-cache budget one worker process may hold.

    The default budget is sized against the whole machine's memory, but under
    ``--jobs N`` there are N independent processes each with their own cache, so
    the machine-wide figure has to be shared out or N workers would together
    retain N× what a sequential run does. A floor keeps the split from making
    the cache useless at high job counts: one submission's pages at 300 DPI are
    ~25 MB each, so 64 MiB still covers a worker's per-submission working set,
    which is all a worker ever reuses (each job is a different PDF).
    """
    from opengrader.core.render_cache import default_render_cache_bytes

    budget = default_render_cache_bytes()
    if budget <= 0:
        return 0
    return max(_MIN_WORKER_RENDER_CACHE_BYTES, budget // max(1, workers))


def _init_worker(render_cache_bytes: int) -> None:
    """Process initializer for the extraction pool.

    Module-level and trivially picklable because the pool uses the ``spawn``
    start method, which re-imports rather than forking.
    """
    from opengrader.core.render_cache import configure_render_cache

    configure_render_cache(max_bytes=render_cache_bytes)


_MIN_WORKER_RENDER_CACHE_BYTES = 64 * 1024 * 1024


def _run_parallel(
    jobs: Sequence[ExtractionJob],
    *,
    workers: int,
    progress: ExtractionProgress,
    status_callback: Callable[[str], None] | None,
    on_outcome: Callable[[ExtractionOutcome], None] | None,
    fail_fast: bool,
) -> BatchReport:
    """Extract on a spawn-based process pool, replaying progress as it arrives."""
    context = multiprocessing.get_context("spawn")
    # A Manager queue (a proxy) is used rather than a raw multiprocessing.Queue
    # because it can be pickled as an ordinary argument; a raw queue may only be
    # shared through process inheritance, which ProcessPoolExecutor does not
    # promise. Event volume is tiny (one per transcribed response), so the extra
    # hop costs nothing measurable next to the OCR it is reporting on.
    manager = context.Manager()
    stop = threading.Event()
    order = {job.key: index for index, job in enumerate(jobs)}
    outcomes: list[ExtractionOutcome] = []
    try:
        event_queue = manager.Queue()
        drain = threading.Thread(
            target=_replay_events,
            args=(event_queue, stop, progress, status_callback),
            name="extraction-progress",
            daemon=True,
        )
        drain.start()
        try:
            with ProcessPoolExecutor(
                max_workers=workers,
                mp_context=context,
                initializer=_init_worker,
                initargs=(_worker_render_cache_bytes(workers),),
            ) as pool:
                pending = {
                    pool.submit(_execute_job, job, event_queue): job for job in jobs
                }
                while pending:
                    done, _ = wait(set(pending), return_when=FIRST_COMPLETED)
                    for future in done:
                        job = pending.pop(future)
                        outcome = _outcome_from_future(future, job)
                        outcomes.append(outcome)
                        if on_outcome is not None:
                            on_outcome(outcome)
                        if fail_fast and outcome.status == "failed":
                            for remaining in pending:
                                remaining.cancel()
                            pending.clear()
                            break
        finally:
            stop.set()
            drain.join(timeout=2.0)
    finally:
        manager.shutdown()

    outcomes.sort(key=lambda outcome: order.get(outcome.key, 0))
    return BatchReport(outcomes=outcomes, workers=workers)


def _outcome_from_future(future, job: ExtractionJob) -> ExtractionOutcome:
    """Unwrap a finished future, turning a dead worker into a failed outcome.

    ``_execute_job`` already converts extraction errors into outcomes, so
    anything raised here is the pool itself failing — a worker killed by the OOM
    killer, say. Reporting it against the job keeps the batch honest instead of
    losing the submission silently.
    """
    try:
        return future.result()
    except Exception as exc:  # noqa: BLE001 - worker process died
        return ExtractionOutcome(
            pdf_path=job.pdf_path,
            key=job.key,
            status="failed",
            error=f"worker process failed: {type(exc).__name__}: {exc}",
            source_sha256=job.source_sha256,
        )
