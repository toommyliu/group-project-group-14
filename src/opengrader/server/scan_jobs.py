"""Bounded background intake jobs, independent of a browser connection."""

import logging
from pathlib import Path
from concurrent.futures import CancelledError, Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context
from threading import RLock
from typing import Callable
from uuid import uuid4

from opengrader.core.submissions.boundary import plan_boundary_ranges
from opengrader.core.submissions.scan_check import analyze_scan_pdf
from opengrader.core.submissions.scan_plan import ScanPage, write_scan_submissions
from opengrader.core.submissions.splitter import plan_submission_ranges
from opengrader.server.scan_models import PageSelection, ScanBatch, ScanFinding, ScanGroup
from opengrader.core.submissions.intake import SubmissionIntake
from opengrader.server.scan_store import ScanStore
from opengrader.server.submission_ids import submission_ids

logger = logging.getLogger(__name__)


class ScanCancelled(Exception):
    """Stop a job at a publication boundary when its assignment is being deleted."""


class ScanJobs:
    def __init__(self, store: ScanStore, lock: RLock, refresh: Callable[[str, Path], None]):
        self.store = store
        self.lock = lock
        self.refresh = refresh
        self.stopping: set[str] = set()
        self.futures: dict[str, set[Future[None]]] = {}
        # One active batch bounds OCR/LLM load while request threads remain available.
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scan-intake")
        # PyMuPDF must not run concurrently in request threads.
        self.processing = ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn"))
        self.previews = ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn"))

    def close(self) -> None:
        self.executor.shutdown(wait=True)
        self.processing.shutdown(wait=True)
        self.previews.shutdown(wait=True)

    def submit(self, assignment_id: str, batch_id: str, *, analyze: bool) -> None:
        with self.lock:
            self.check_running(assignment_id)
            futures = self.futures.setdefault(assignment_id, set())
            future = self.executor.submit(self._run, assignment_id, batch_id, analyze)
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
                self.processing = ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn"))
            logger.exception("Scan job failed: %s", batch_id)
            if batch is None:
                return
            batch.status = "failed"
            batch.message = "Could not finish this scan. Retry to continue, or check the server log."
            with self.lock:
                if assignment_id not in self.stopping:
                    self.save(assignment_id, batch)

    def _analyze(self, assignment_id: str, batch: ScanBatch) -> None:
        source = self.store.directory(assignment_id, batch.id) / "original.pdf"
        batch.status = "checking"
        batch.message = "Checking scan quality and submission boundaries."
        self.save(assignment_id, batch)
        report = self.processing.submit(
            analyze_scan_pdf,
            source,
            pages_per_submission=batch.pages_per_submission if batch.mode == "fixed" else None,
            single_submission=batch.mode == "individual",
        ).result()
        findings = [ScanFinding(page=issue.page, severity=issue.severity, detail=issue.detail) for issue in report.issues]
        if not report.orientation_available:
            findings.append(ScanFinding(severity="warning", detail=report.orientation_note or "Page orientation could not be checked."))
        if batch.mode == "automatic":
            ranges, warnings = self.processing.submit(plan_boundary_ranges, source).result()
            findings.extend(ScanFinding(severity="warning", detail=warning) for warning in warnings)
        else:
            ranges = plan_submission_ranges(
                total_pages=batch.page_count,
                pages_per_submission=batch.pages_per_submission if batch.mode == "fixed" else batch.page_count,
            )
        batch.groups = [
            ScanGroup(key=str(uuid4()), pages=[PageSelection(number=page) for page in range(group.start_page, group.end_page + 1)])
            for group in ranges
        ]
        batch.excluded_pages = []
        batch.findings = findings
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
        intakes: dict[str | None, SubmissionIntake] = {}
        for index, group in enumerate(batch.groups, start=1):
            name = f"scan-{batch.id}-{group.key}"
            destination = assignment / "submissions" / name
            markdown = destination / f"{name}.submission.md"
            batch.message = f"Reading answers: {index} of {len(batch.groups)} submissions."
            group.error = ""
            self.save(assignment_id, batch)
            try:
                if not markdown.exists():
                    pdf = directory / "split" / f"{name}.pdf"
                    version = group.assignment_version or batch.assignment_version
                    if version not in intakes:
                        intakes[version] = SubmissionIntake(assignment / "qa.md", auto_grade=batch.auto_grade, assignment_version=version)
                    intake = intakes[version]
                    staging = self.processing.submit(intake.extract, pdf, directory / "extracted").result()
                    with self.lock:
                        self.check_running(assignment_id)
                        if not destination.exists():
                            staging.rename(destination)
                with self.lock:
                    self.check_running(assignment_id)
                    ids = submission_ids(assignment / "submissions", [markdown])
                    self.refresh(assignment_id, markdown)
                    group.submission_id = ids[markdown.relative_to(assignment / "submissions").as_posix()]
            except ScanCancelled:
                raise
            except (Exception, SystemExit) as exc:
                if isinstance(exc, BrokenProcessPool):
                    self.processing.shutdown(wait=False)
                    self.processing = ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn"))
                logger.exception("Submission extraction failed: %s", name)
                group.error = str(exc) if isinstance(exc, ValueError) else "Could not read this submission. Check its pages and retry."
            self.save(assignment_id, batch)
        failed = sum(bool(group.error) for group in batch.groups)
        batch.status = "failed" if failed else "ready"
        batch.message = (
            f"{len(batch.groups) - failed} ready, {failed} failed. Retry to process only unfinished submissions."
            if failed else f"{len(batch.groups)} submissions ready."
        )
        self.save(assignment_id, batch)
