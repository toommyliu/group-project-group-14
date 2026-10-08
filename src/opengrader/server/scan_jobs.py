"""Bounded background intake jobs, independent of a browser connection."""

import logging
import shutil
import signal
from functools import partial
from itertools import batched
from pathlib import Path
from concurrent.futures import CancelledError, Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context
from threading import RLock
from typing import Callable
from uuid import uuid4

from opengrader.core.submissions.boundary import submission_warnings
from opengrader.core.submissions.region_crop import CropInputs, crop_submission
from opengrader.core.submissions.scan_plan import ScanPage, write_scan_submissions
from opengrader.core.submissions.splitter import plan_submission_ranges
from opengrader.core.submissions.version_match import check_group, score_pages, template_features
from opengrader.server.scan_models import PageSelection, ScanBatch, ScanGroup
from opengrader.server.scan_store import ScanStore

logger = logging.getLogger(__name__)


def _defer_terminal_shutdown() -> None:
    # The server drains these jobs before closing the pool. Terminal signals
    # reach the workers too, but must not interrupt an extraction halfway through.
    for name in ("SIGINT", "SIGHUP", "SIGBREAK"):
        if (sig := getattr(signal, name, None)) is not None:
            signal.signal(sig, signal.SIG_IGN)


def _process_pool() -> ProcessPoolExecutor:
    return ProcessPoolExecutor(
        max_workers=1,
        mp_context=get_context("spawn"),
        initializer=_defer_terminal_shutdown,
    )


def check_template(group: ScanGroup, batch: ScanBatch, *, reorder: bool) -> None:
    check = check_group([page.number for page in group.pages], batch.template_scores, reorder=reorder)
    by_number = {page.number: page for page in group.pages}
    group.pages = [by_number[number] for number in check.pages]
    group.matched_version = check.version
    group.scan_warnings.extend(check.warnings)


class ScanCancelled(Exception):
    """Stop a job at a publication boundary when its assignment is being deleted."""


class ScanWorkers:
    """One bounded set of intake and PDF workers for the server instance."""

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scan-intake")
        self.processing = _process_pool()
        self.previews = _process_pool()

    def close(self) -> None:
        self.executor.shutdown(wait=True)
        self.processing.shutdown(wait=True)
        self.previews.shutdown(wait=True)


class ScanJobs:
    def __init__(
        self,
        store: ScanStore,
        lock: RLock,
        refresh: Callable[[str, Path], int],
        inputs: Callable[[str, str | None, str | None], CropInputs],
        published: Callable[[str, str], int | None],
        templates: Callable[[str], dict[str, Path]],
        *,
        workers: ScanWorkers | None = None,
    ):
        self.store = store
        self.lock = lock
        self.refresh = refresh
        self.inputs = inputs
        self.published = published
        self.templates = templates
        self.stopping: set[str] = set()
        self.futures: dict[str, set[Future[None]]] = {}
        self.workers = workers or ScanWorkers()
        self._owns_workers = workers is None
        self.executor = self.workers.executor

    @property
    def processing(self):
        return self.workers.processing

    @processing.setter
    def processing(self, pool):
        self.workers.processing = pool

    @property
    def previews(self):
        return self.workers.previews

    def close(self) -> None:
        if self._owns_workers:
            self.workers.close()

    def submit(self, assignment_id: str, batch_id: str, *, analyze: bool) -> None:
        self.start(assignment_id, partial(self._run, assignment_id, batch_id, analyze))

    def start(self, assignment_id: str, work: Callable[[], None]) -> None:
        with self.lock:
            self.check_running(assignment_id)
            futures = self.futures.setdefault(assignment_id, set())
            future = self.executor.submit(work)
            futures.add(future)
            def finished(completed: Future[None]) -> None:
                with self.lock:
                    futures.discard(completed)
                    if not futures:
                        self.futures.pop(assignment_id, None)
            future.add_done_callback(finished)

    def check_running(self, assignment_id: str) -> None:
        if assignment_id in self.stopping:
            raise ScanCancelled()

    def stop_assignment(self, assignment_id: str) -> tuple[Future[None], ...]:
        """Called under the coordinator lock; wait for returned jobs outside the lock."""
        self.stopping.add(assignment_id)
        futures = tuple(self.futures.get(assignment_id, ()))
        for future in futures:
            future.cancel()
        return futures

    def wait_for_stopped(self, futures: tuple[Future[None], ...]) -> None:
        for future in futures:
            try:
                future.result()
            except CancelledError:
                pass

    def in_process[T](self, work: Callable[..., T], *args) -> T:
        try:
            return self.processing.submit(work, *args).result()
        except BrokenProcessPool:
            self.processing.shutdown(wait=False)
            self.processing = _process_pool()
            raise

    def crop(self, pdf: Path, inputs: CropInputs, output: Path) -> Path:
        return self.in_process(crop_submission, pdf, inputs, output)

    def _match_versions(self, assignment_id: str, batch: ScanBatch) -> None:
        batch.template_scores = {}
        scan = self.store.directory(assignment_id, batch.id) / "original.pdf"
        try:
            templates = self.templates(assignment_id)
            if len(templates) < 2:
                return
            references = self.in_process(template_features, templates)
            for pages in batched(range(1, batch.page_count + 1), 20):
                batch.message = f"Matching pages to versions: {pages[0] - 1} of {batch.page_count}."
                self.save(assignment_id, batch)
                batch.template_scores.update(self.in_process(score_pages, scan, pages, references))
        except ScanCancelled:
            raise
        except (Exception, SystemExit):
            logger.exception("Version matching failed: %s", batch.id)
            batch.template_scores = {}

    def save(self, assignment_id: str, batch: ScanBatch) -> None:
        with self.lock:
            self.check_running(assignment_id)
            batch.revision += 1
            self.store.save(assignment_id, batch)

    def _run(self, assignment_id: str, batch_id: str, analyze: bool) -> None:
        batch: ScanBatch | None = None
        try:
            with self.lock:
                self.check_running(assignment_id)
                batch = self.store.get(assignment_id, batch_id)
            if analyze:
                self._analyze(assignment_id, batch)
            else:
                self._process(assignment_id, batch)
        except ScanCancelled:
            return
        except (Exception, SystemExit) as exc:
            if isinstance(exc, BrokenProcessPool):
                self.processing.shutdown(wait=False)
                self.processing = _process_pool()
            logger.exception("Scan job failed: %s", batch_id)
            if batch is None:
                return
            batch.status = "failed"
            batch.message = "Could not finish this scan. Retry to continue, or check the server log."
            with self.lock:
                if assignment_id not in self.stopping:
                    self.save(assignment_id, batch)

    def _analyze(self, assignment_id: str, batch: ScanBatch) -> None:
        self._match_versions(assignment_id, batch)
        ranges = plan_submission_ranges(
            total_pages=batch.page_count,
            pages_per_submission=batch.pages_per_submission if batch.mode == "fixed" else batch.page_count,
        )
        batch.groups = [
            ScanGroup(
                key=str(uuid4()),
                pages=[PageSelection(number=page) for page in range(group.start_page, group.end_page + 1)],
                scan_warnings=list(submission_warnings(group.end_page - group.start_page + 1, batch.pages_per_submission)),
            )
            for group in ranges
        ]
        for group in batch.groups:
            check_template(group, batch, reorder=True)
        batch.excluded_pages = []
        batch.findings = []
        batch.status = "review"
        batch.message = "Review the page groups before creating submissions."
        self.save(assignment_id, batch)

    def _process(self, assignment_id: str, batch: ScanBatch) -> None:
        directory = self.store.directory(assignment_id, batch.id)
        assignment = self.store.assignments_dir / assignment_id
        batch.status = "processing"
        batch.message = "Preparing the reviewed page groups."
        self.save(assignment_id, batch)
        missing = {
            directory / "split" / f"scan-{batch.id}-{group.key}.pdf": [ScanPage(page.number, page.rotation) for page in group.pages]
            for group in batch.groups
            if not (directory / "split" / f"scan-{batch.id}-{group.key}.pdf").exists()
        }
        if missing:
            write_scan_submissions(directory / "original.pdf", missing)
        for index, group in enumerate(batch.groups, start=1):
            name = f"scan-{batch.id}-{group.key}"
            destination = assignment / "submissions" / name
            markdown = destination / f"{name}.submission.md"
            batch.message = f"Cropping answers and matching names: {index} of {len(batch.groups)} submissions."
            group.error = ""
            self.save(assignment_id, batch)
            try:
                with self.lock:
                    self.check_running(assignment_id)
                    group.submission_id = self.published(assignment_id, name)
                if group.submission_id is None:
                    inputs = self.inputs(
                        assignment_id,
                        group.assignment_version or group.matched_version or batch.assignment_version,
                        group.student.student_id if group.student else None,
                    )
                    staging = self.crop(directory / "split" / f"{name}.pdf", inputs, directory / "extracted")
                    with self.lock:
                        self.check_running(assignment_id)
                        if destination.exists():
                            shutil.rmtree(destination)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        staging.rename(destination)
                        group.submission_id = self.refresh(assignment_id, markdown)
            except ScanCancelled:
                raise
            except (Exception, SystemExit) as exc:
                logger.exception("Submission cropping failed: %s", name)
                group.error = str(exc) if isinstance(exc, ValueError) else "Could not crop this submission. Check its pages and retry."
            self.save(assignment_id, batch)
        failed = sum(bool(group.error) for group in batch.groups)
        batch.status = "failed" if failed else "ready"
        batch.message = (
            f"{len(batch.groups) - failed} ready, {failed} failed. Retry to process only unfinished submissions."
            if failed else f"{len(batch.groups)} submissions ready."
        )
        self.save(assignment_id, batch)
