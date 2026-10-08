"""HTTP intake endpoints; all processing runs outside request handlers."""

import hashlib
import logging
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Callable, Literal

import fitz
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response

from opengrader.core.submissions.scan_plan import ScanPage, validate_scan_plan
from opengrader.server.scan_jobs import ScanJobs, check_template
from opengrader.server.scan_models import (
    MAX_SCAN_BYTES, AnalyzeScan, PageSelection, ProcessScan, ReviewScan,
    ScanBatch, ScanGroup, ScanList, ScanRevision,
)
from opengrader.server.scan_store import ScanStore
from uuid import uuid4


def render_scan_page(source: Path, page_number: int, edge: int) -> bytes:
    """Render in the preview process so cropping cannot block page review."""
    with fitz.open(source) as document:
        page = document[page_number - 1]
        scale = edge / max(page.rect.width, page.rect.height)
        return page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")


def add_scan_routes(
    api: APIRouter, store: ScanStore, jobs: ScanJobs, lock: RLock,
    require_assignment: Callable[[str], object],
    discard_submissions: Callable[[str, list[Path]], None],
) -> None:
    def batch_for(assignment_id: str, batch_id: str) -> ScanBatch:
        require_assignment(assignment_id)
        return store.get(assignment_id, batch_id)

    def editable(assignment_id: str, batch_id: str, revision: int) -> ScanBatch:
        batch = batch_for(assignment_id, batch_id)
        if batch.revision != revision:
            raise HTTPException(409, "This scan changed. Reload it before saving.")
        if batch.status in ("checking", "queued", "processing", "ready", "removed"):
            raise HTTPException(409, "This scan is processing or already complete.")
        return batch

    @api.get("/assignments/{assignment_id}/scans")
    def list_scans(assignment_id: str) -> ScanList:
        with lock:
            require_assignment(assignment_id)
            return ScanList(batches=store.list(assignment_id))

    @api.post("/assignments/{assignment_id}/scans", response_model=ScanBatch, openapi_extra={
        "requestBody": {"required": True, "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}},
    })
    async def upload_scan(
        assignment_id: str, request: Request, filename: str = Query(min_length=1, max_length=255),
        student_id: str | None = Query(None, min_length=1, max_length=64),
        assignment_version: str | None = Query(None, max_length=20),
    ) -> ScanBatch:
        require_assignment(assignment_id)
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(422, "Choose a PDF file.")
        size = 0
        digest = hashlib.sha256()
        temporary = NamedTemporaryFile(prefix=".upload-", dir=store.assignments_dir, delete=False)
        path = Path(temporary.name)
        try:
            with temporary:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_SCAN_BYTES:
                        raise HTTPException(413, "This PDF exceeds the 250 MB limit. Split it into smaller files.")
                    digest.update(chunk)
                    await run_in_threadpool(temporary.write, chunk)
            def accept() -> ScanBatch:
                with lock:
                    require_assignment(assignment_id)
                    if student_id is None:
                        return store.accept(assignment_id, path, filename, digest.hexdigest())
                    try:
                        student = jobs.inputs(assignment_id, assignment_version, student_id).student
                    except ValueError as exc:
                        raise HTTPException(422, str(exc)) from exc
                    assert student is not None
                    if any(
                        batch.status != "removed" and any(
                            group.student is not None and group.student.student_id == student_id
                            and group.submission_id is None
                            for group in batch.groups
                        )
                        for batch in store.list(assignment_id)
                    ):
                        raise HTTPException(409, f"An upload for {student.name} is already on the Scans page.")
                    batch = store.accept(assignment_id, path, filename, digest.hexdigest())
                    if batch.started or batch.status != "uploaded":
                        raise HTTPException(409, "This PDF was already uploaded. Find it on the Scans page.")
                    batch.mode = "individual"
                    batch.groups = [ScanGroup(
                        key=str(uuid4()), student=student, assignment_version=assignment_version,
                        pages=[PageSelection(number=page) for page in range(1, batch.page_count + 1)],
                    )]
                    batch.started = True
                    batch.assignment_version = assignment_version
                    batch.status = "queued"
                    batch.message = f"Waiting to process the submission for {student.name}."
                    jobs.save(assignment_id, batch)
                    jobs.submit(assignment_id, batch.id, analyze=False)
                    return batch
            return await run_in_threadpool(accept)
        finally:
            path.unlink(missing_ok=True)

    @api.post("/assignments/{assignment_id}/scans/{batch_id}/analyze", status_code=202)
    def analyze_scan(assignment_id: str, batch_id: str, request: AnalyzeScan) -> ScanBatch:
        with lock:
            batch = editable(assignment_id, batch_id, request.expected_revision)
            if batch.started:
                raise HTTPException(409, "Submissions have already been created from this split. Upload a corrected scan instead.")
            if request.mode == "fixed" and request.pages_per_submission is None:
                raise HTTPException(422, "Enter the number of scanned pages per submission.")
            batch.mode = request.mode
            batch.pages_per_submission = request.pages_per_submission
            batch.status = "checking"
            batch.message = "Waiting to check this scan."
            jobs.save(assignment_id, batch)
            jobs.submit(assignment_id, batch_id, analyze=True)
            return batch

    @api.put("/assignments/{assignment_id}/scans/{batch_id}/groups")
    def review_scan(assignment_id: str, batch_id: str, request: ReviewScan) -> ScanBatch:
        with lock:
            batch = editable(assignment_id, batch_id, request.expected_revision)
            if batch.started:
                raise HTTPException(409, "This split has already been processed.")
            try:
                validate_scan_plan(batch.page_count, [
                    [ScanPage(page.number, page.rotation) for page in group] for group in request.groups
                ], request.excluded_pages)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            from opengrader.core.submissions.boundary import submission_warnings

            previous = {tuple(page.number for page in group.pages): group for group in batch.groups}
            batch.groups = [
                previous[tuple(page.number for page in group)].model_copy(update={"pages": group})
                if tuple(page.number for page in group) in previous else ScanGroup(
                    key=str(uuid4()), pages=group,
                    scan_warnings=list(submission_warnings(len(group), batch.pages_per_submission)),
                ) for group in request.groups
            ]
            for group in batch.groups:
                if tuple(page.number for page in group.pages) not in previous:
                    check_template(group, batch, reorder=False)
            batch.excluded_pages = request.excluded_pages
            batch.status = "review"
            batch.message = "Split saved. Create submissions when the page groups are correct."
            jobs.save(assignment_id, batch)
            return batch

    @api.post("/assignments/{assignment_id}/scans/{batch_id}/process", status_code=202)
    def process_scan(assignment_id: str, batch_id: str, request: ProcessScan) -> ScanBatch:
        with lock:
            batch = editable(assignment_id, batch_id, request.expected_revision)
            if not batch.groups:
                raise HTTPException(422, "Check and review the scan first.")
            if (batch.findings or any(group.scan_warnings for group in batch.groups)) and not request.acknowledge_findings:
                raise HTTPException(422, "Review the scan findings before creating submissions.")
            if request.group_versions.keys() - {group.key for group in batch.groups}:
                raise HTTPException(422, "The page groups changed. Reload scans before choosing versions.")
            for group in batch.groups:
                if group.key in request.group_versions and group.submission_id is None:
                    group.assignment_version = request.group_versions[group.key]
                if group.submission_id is None:
                    try:
                        jobs.inputs(
                            assignment_id,
                            group.assignment_version or group.matched_version or request.assignment_version,
                            None,
                        )
                    except ValueError as exc:
                        raise HTTPException(422, str(exc)) from exc
            batch.started = True
            batch.assignment_version = request.assignment_version
            batch.status = "queued"
            batch.message = "Waiting to process submissions. You can leave this page."
            jobs.save(assignment_id, batch)
            jobs.submit(assignment_id, batch_id, analyze=False)
            return batch

    @api.get("/assignments/{assignment_id}/scans/{batch_id}/pdf")
    def scan_pdf(assignment_id: str, batch_id: str) -> FileResponse:
        batch = batch_for(assignment_id, batch_id)
        return FileResponse(store.directory(assignment_id, batch_id) / "original.pdf", media_type="application/pdf", filename=batch.filename, content_disposition_type="inline")

    @api.post("/assignments/{assignment_id}/scans/{batch_id}/remove")
    def remove_scan(assignment_id: str, batch_id: str, request: ScanRevision) -> ScanBatch:
        with lock:
            batch = batch_for(assignment_id, batch_id)
            if batch.revision != request.expected_revision:
                raise HTTPException(409, "This scan changed. Reload it before removing.")
            if batch.status in ("checking", "queued", "processing"):
                raise HTTPException(409, "Wait for processing to finish before removing this scan.")
            batch.status = "removed"
            batch.message = "Scan removed from intake. Its original PDF is preserved and can be restored."
            jobs.save(assignment_id, batch)
            return batch

    @api.delete("/assignments/{assignment_id}/scans/{batch_id}", status_code=204)
    def delete_scan(assignment_id: str, batch_id: str, expected_revision: int) -> Response:
        with lock:
            batch = batch_for(assignment_id, batch_id)
            if batch.revision != expected_revision:
                raise HTTPException(409, "This scan changed. Reload it before deleting.")
            if batch.status in ("checking", "queued", "processing"):
                raise HTTPException(409, "Wait for this scan to finish processing before deleting it.")
            submissions = store.assignments_dir / assignment_id / "submissions"
            paths = [submissions / f"scan-{batch.id}-{group.key}" for group in batch.groups]
            deleted = store.assignments_dir / f".deleted-scan-{uuid4()}"
            moved: list[tuple[Path, Path]] = []
            try:
                deleted.mkdir()
                for path in paths:
                    if path.exists():
                        target = deleted / path.name
                        path.rename(target)
                        moved.append((path, target))
                # Unpublish under the coordinator lock before allowing another upload or job.
                target = deleted / "scan"
                source = store.directory(assignment_id, batch_id)
                source.rename(target)
                moved.append((source, target))
                discard_submissions(assignment_id, paths)
            except Exception as exc:
                for source, target in reversed(moved):
                    target.rename(source)
                if deleted.exists():
                    deleted.rmdir()
                raise HTTPException(500, "Could not delete this scan. Check server storage and try again.") from exc
        try:
            shutil.rmtree(deleted)
        except OSError:
            logging.getLogger(__name__).exception("Could not clean deleted scan %s", deleted)
        return Response(status_code=204)

    @api.post("/assignments/{assignment_id}/scans/{batch_id}/restore")
    def restore_scan(assignment_id: str, batch_id: str, request: ScanRevision) -> ScanBatch:
        with lock:
            batch = batch_for(assignment_id, batch_id)
            if batch.status != "removed" or batch.revision != request.expected_revision:
                raise HTTPException(409, "This scan changed. Reload it before restoring.")
            if batch.started:
                batch.status = "ready" if all(group.submission_id is not None for group in batch.groups) else "failed"
                batch.message = "Scan restored. Existing submissions are preserved; unfinished submissions can be retried."
            else:
                batch.status = "review" if batch.groups else "uploaded"
                batch.message = "Scan restored. Review its page groups before processing."
            jobs.save(assignment_id, batch)
            return batch

    @api.get("/assignments/{assignment_id}/scans/{batch_id}/pages/{page_number}")
    def scan_page(assignment_id: str, batch_id: str, page_number: int, size: Literal["small", "large"] = "small") -> Response:
        batch = batch_for(assignment_id, batch_id)
        if not 1 <= page_number <= batch.page_count:
            raise HTTPException(404, "Scan page not found.")
        directory = store.directory(assignment_id, batch_id)
        cache = directory / "thumbnails" / f"{page_number}-{size}.png"
        if not cache.exists():
            content = jobs.previews.submit(render_scan_page, directory / "original.pdf", page_number, 1400 if size == "large" else 320).result()
            cache.parent.mkdir(exist_ok=True)
            with NamedTemporaryFile(dir=cache.parent, delete=False) as temporary:
                temporary.write(content)
                temporary_path = Path(temporary.name)
            temporary_path.replace(cache)
        content = cache.read_bytes()
        return Response(content, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})
