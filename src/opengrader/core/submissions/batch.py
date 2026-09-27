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

from .manifest import relative_outputs
from .processor import (
    ExtractionProgress,
    _RosterEntry,
    build_student_submission_document_unified,
)
from .template import AssignmentTemplate
from .student_file import StudentSubmissionDocument
from .transcription import tally_reading_sources

if TYPE_CHECKING:  # pragma: no cover
    from opengrader.core.pages.catalog import PageCatalog
    from opengrader.core.bubble_sheet import BubbleSheetLayout
    from opengrader.core.ocr import OcrConfig
    from opengrader.core.ocr.vision_reader import VisionReader

AUTO_WORKER_CAP = 8
VLM_AUTO_WORKER_CAP = 4

_DRAIN_POLL_SECONDS = 0.1

OutcomeStatus = Literal["extracted", "skipped", "failed"]


@dataclass(frozen=True)
class ExtractionJob:
    pdf_path: Path
    output_dir: Path
    ocr_config: OcrConfig | None = None
    dpi: int = 300
    split_subquestions: bool = True
    vision_reader: "VisionReader | None" = None
    skip_ollama_qa: bool = False
    save_layout_debug: bool = False
    blank_assignment_template: AssignmentTemplate | None = None
    roster: list[_RosterEntry] | None = None
    update: bool = False
    refresh_layout: bool = False
    llm_responses: bool = False
    llm_math: bool = False
    escalate_floor: float | None = None
    math_backend: str = DEFAULT_MATH_BACKEND
    default_section: str | None = None
    section_by_id: dict[str, str] | None = None
    bubble_sheet_layout: "BubbleSheetLayout | None" = None
    bubble_shuffled: bool = False
    bubble_form_key: str | None = None
    source_sha256: str = ""
    page_catalog: "PageCatalog | None" = None

    @property
    def key(self) -> str:
        return self.pdf_path.stem


@dataclass(frozen=True)
class ExtractionOutcome:
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
    reading_counts: tuple[tuple[str, int], ...] = ()

    @property
    def identity(self) -> str:
        if self.student_id:
            return f"{self.student_name} ({self.student_id})"
        return "Unidentified"


@dataclass
class BatchReport:
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
    submission_dir = markdown_path.parent
    candidates: list[str | Path] = [markdown_path]
    for image in (document.name_image_path, document.id_image_path):
        if image:
            candidates.append(submission_dir / image)
    for question in document.questions:
        if question.response_image_path:
            candidates.append(submission_dir / question.response_image_path)
        if question.ink_image_path:
            candidates.append(submission_dir / question.ink_image_path)
    return relative_outputs(output_dir, candidates)


def _execute_job(
    job: ExtractionJob,
    event_queue: "queue_module.Queue | None" = None,
    build: Callable[..., tuple[StudentSubmissionDocument, Path]] | None = None,
) -> ExtractionOutcome:
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
    else:  # pragma: no cover
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
            vision_reader=job.vision_reader,
            skip_ollama_qa=job.skip_ollama_qa,
            save_layout_debug=job.save_layout_debug,
            blank_assignment_template=job.blank_assignment_template,
            page_catalog=job.page_catalog,
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
    except Exception as exc:  # noqa: BLE001
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
        except (EOFError, OSError, ValueError):
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
    """on_outcome runs on the calling thread in completion order; results use input order.
    Injected build or resolve_ambiguous forces sequential execution. fail_fast stops
    further scheduling after failure.
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
    report = BatchReport(workers=1)
    for job in jobs:
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
            vision_reader=job.vision_reader,
            skip_ollama_qa=job.skip_ollama_qa,
            save_layout_debug=job.save_layout_debug,
            blank_assignment_template=job.blank_assignment_template,
            page_catalog=job.page_catalog,
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
    except Exception as exc:  # noqa: BLE001
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
    from opengrader.core.render_cache import default_render_cache_bytes

    budget = default_render_cache_bytes()
    if budget <= 0:
        return 0
    return max(_MIN_WORKER_RENDER_CACHE_BYTES, budget // max(1, workers))


def _init_worker(render_cache_bytes: int, ai_calls_in_flight: int = 1) -> None:
    import os

    import cv2

    from opengrader.core.pages.printed import limit_threads

    cv2.setNumThreads(1)
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    limit_threads(1)

    from opengrader.core.render_cache import configure_render_cache

    configure_render_cache(max_bytes=render_cache_bytes)

    # The provider-call budget this worker is allowed, which is how `--jobs`
    # reaches the transport's semaphore. A worker handles one submission at a
    # time, so its own budget is one; the fleet-wide bound a hosted service
    # actually sees is the worker count, which is `--jobs` by another name.
    # Setting it here rather than leaving it unbounded means a future
    # thread-fanned worker inherits a cap instead of discovering it needs one
    # from a rate-limit response.
    from opengrader.core.llm.transport import set_concurrency_limit

    set_concurrency_limit(ai_calls_in_flight)


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
    context = multiprocessing.get_context("spawn")
    # Pool arguments must be picklable; raw queues require process inheritance.
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
                initargs=(_worker_render_cache_bytes(workers), 1),
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
    try:
        return future.result()
    except Exception as exc:  # noqa: BLE001
        return ExtractionOutcome(
            pdf_path=job.pdf_path,
            key=job.key,
            status="failed",
            error=f"worker process failed: {type(exc).__name__}: {exc}",
            source_sha256=job.source_sha256,
        )
