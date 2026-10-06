"""Template and outline endpoints: where each question sits on the assignment PDF."""

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import RLock
from typing import Callable, Literal

import fitz
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from opengrader.core.outline import (
    BOXES_ONLY,
    Outline,
    TemplateIdentity,
    add_version,
    apply_outline,
    move_boxes,
    outline_for,
    outline_versions,
    remove_version,
    version_questions,
)
from opengrader.core.outline_overlay import OVERLAY_SIGNATURE, writing_overlay
from opengrader.core.qa_file import QADocument, parse_qa_markdown, render_qa_markdown
from opengrader.core.region_map import RegionMap, region_map_to_json
from opengrader.core.workspace.models import Assignment
from opengrader.core.workspace.recut import RecutStatus, recut_status, retry_recuts
from opengrader.core.workspace.store import StoredOutline, VersionSubmissions, Workspace
from opengrader.server.recut import start_recut
from opengrader.server.scan_jobs import ScanJobs

MAX_TEMPLATE_BYTES = 50 * 1024 * 1024
MAX_TEMPLATE_PAGES = 100
PDF_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
    }
}


class TemplateInfo(BaseModel):
    filename: str
    sha256: str
    page_count: int


class WritingOverlay(BaseModel):
    revision: str
    submissions: int


class OutlineLock(BaseModel):
    scope: Literal["questions", "outline"]
    reason: str


class AssignmentOutline(BaseModel):
    version: str
    versions: list[str]
    versions_with_template: list[str]
    versions_with_regions: list[str]
    template: TemplateInfo | None
    outline: Outline
    revision: str
    lock: OutlineLock | None
    writing: WritingOverlay | None = None
    submissions: VersionSubmissions | None
    recut: RecutStatus | None


class OutlineUpdate(BaseModel):
    version: str = ""
    revision: str
    outline: Outline


class NewVersion(BaseModel):
    label: str = Field(max_length=20)
    copy_from: str | None = None


async def receive_pdf(request: Request, directory: Path, limit: int, too_large: str) -> tuple[Path, str]:
    """Stream an upload to disk; the caller owns the returned file."""
    size = 0
    digest = hashlib.sha256()
    temporary = NamedTemporaryFile(prefix=".upload-", suffix=".pdf", dir=directory, delete=False)
    path = Path(temporary.name)
    try:
        with temporary:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, too_large)
                digest.update(chunk)
                await run_in_threadpool(temporary.write, chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path, digest.hexdigest()


def writing_for(stored: StoredOutline | None, sources: list[str]) -> WritingOverlay | None:
    if stored is None or not sources:
        return None
    key = "\0".join([OVERLAY_SIGNATURE, stored.template, *sources])
    return WritingOverlay(revision=hashlib.sha256(key.encode()).hexdigest(), submissions=len(sources))


def template_identity(path: Path, filename: str, sha256: str) -> TemplateIdentity:
    try:
        with fitz.open(path) as document:
            if document.needs_pass:
                raise HTTPException(422, "This PDF is password protected. Remove the password and upload it again.")
            pages = len(document)
            rect = document[0].rect if pages else None
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(422, "Choose a PDF file.") from exc
    if rect is None:
        raise HTTPException(422, "This PDF has no pages.")
    if pages > MAX_TEMPLATE_PAGES:
        raise HTTPException(422, f"Templates can have at most {MAX_TEMPLATE_PAGES} pages.")
    return TemplateIdentity(filename, sha256, pages, (round(rect.width, 2), round(rect.height, 2)))


def empty_regions(identity: TemplateIdentity, title: str) -> RegionMap:
    return RegionMap(
        source_name=identity.filename,
        source_sha256=identity.sha256,
        source_pages=identity.pages,
        source_page_size=identity.page_size,
        assignment_title=title,
    )


def _pdf_filename(filename: str) -> str:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not name.lower().endswith(".pdf"):
        raise HTTPException(422, "Choose a PDF file.")
    return name


def add_outline_routes(
    api: APIRouter,
    workspace: Workspace,
    jobs: ScanJobs,
    lock: RLock,
    require_assignment: Callable[[str], None],
    changed: Callable[[str], None],
) -> None:
    uploads = workspace.root / ".opengrader"
    overlay_lock = asyncio.Lock()

    def resolve(document: QADocument, version: str | None) -> str:
        versions = outline_versions(document)
        if version is None or (version == "" and versions != [""]):
            return versions[0]
        if version not in versions:
            raise HTTPException(422, "Choose an available assignment version.")
        return version

    def outline_lock(assignment_id: str, recut: RecutStatus | None) -> OutlineLock | None:
        if any(batch.status in ("queued", "processing") for batch in jobs.store.list(assignment_id)):
            return OutlineLock(
                scope="outline", reason="Wait for scans to finish processing before changing the outline."
            )
        if recut and recut.remaining:
            return OutlineLock(
                scope="outline",
                reason="Wait for answers to be cropped from the new boxes before changing the outline.",
            )
        submissions, imported = workspace.submission_counts(assignment_id)
        if imported:
            return OutlineLock(
                scope="outline",
                reason="Submissions imported from a class folder were cropped with their own boxes, "
                "so this outline can't change.",
            )
        return OutlineLock(scope="questions", reason=BOXES_ONLY) if submissions else None

    def revision(source: str, stored: StoredOutline | None) -> str:
        regions = json.dumps(region_map_to_json(stored.regions)) if stored else ""
        return hashlib.sha256(f"{source}\0{regions}".encode()).hexdigest()

    def current(assignment_id: str, version: str | None) -> AssignmentOutline:
        require_assignment(assignment_id)
        source = workspace.assignment_source(assignment_id)
        document = parse_qa_markdown(source)
        version = resolve(document, version)
        outlines = workspace.outlines(assignment_id)
        stored = outlines.get(version)
        regions = stored.regions if stored else None
        sources = workspace.submission_sources(assignment_id, version)
        recut = recut_status(workspace, assignment_id)
        return AssignmentOutline(
            version=version,
            versions=outline_versions(document),
            versions_with_template=sorted(outlines),
            versions_with_regions=sorted(
                key for key, item in outlines.items() if item.regions.questions
            ),
            template=TemplateInfo(
                filename=regions.source_name,
                sha256=regions.source_sha256,
                page_count=regions.source_pages,
            )
            if regions
            else None,
            outline=outline_for(version_questions(document, version), regions),
            revision=revision(source, stored),
            lock=outline_lock(assignment_id, recut),
            writing=writing_for(stored, sources),
            submissions=workspace.version_submissions(assignment_id, version),
            recut=recut,
        )

    def require_unlocked(assignment_id: str) -> None:
        locked = outline_lock(assignment_id, recut_status(workspace, assignment_id))
        if locked:
            raise HTTPException(409, locked.reason)

    @api.post("/assignments/from-template", status_code=201, openapi_extra=PDF_BODY)
    async def create_from_template(
        request: Request,
        title: str = Query(min_length=1, max_length=200),
        filename: str = Query(min_length=1, max_length=255),
    ) -> Assignment:
        name = _pdf_filename(filename)
        title = " ".join(title.split())
        if not title:
            raise HTTPException(422, "Enter an assignment name.")
        path, sha256 = await receive_pdf(
            request, uploads, MAX_TEMPLATE_BYTES, "Templates can be at most 50 MB."
        )
        try:
            identity = await run_in_threadpool(template_identity, path, name, sha256)
            template = await run_in_threadpool(workspace.add_asset, path)
        finally:
            path.unlink(missing_ok=True)
        source = render_qa_markdown(QADocument(questions=[], title=title))

        def create() -> Assignment:
            with lock:
                assignment = workspace.create_assignment(
                    source, template=StoredOutline(template, empty_regions(identity, title))
                )
                changed(assignment.id)
                return assignment

        return await run_in_threadpool(create)

    @api.get("/assignments/{assignment_id}/outline")
    def get_outline(assignment_id: str, version: str | None = None) -> AssignmentOutline:
        return current(assignment_id, version)

    @api.put("/assignments/{assignment_id}/outline")
    def save_outline(assignment_id: str, request: OutlineUpdate) -> AssignmentOutline:
        with lock:
            before = current(assignment_id, request.version)
            if before.lock and before.lock.scope == "outline":
                raise HTTPException(409, before.lock.reason)
            if before.revision != request.revision:
                raise HTTPException(409, "This outline changed. Reload it before saving.")
            stored = workspace.outlines(assignment_id).get(before.version)
            if stored is None:
                raise HTTPException(409, "Upload a template before drawing the outline.")
            regions = stored.regions
            source = workspace.assignment_source(assignment_id)
            if before.lock:
                moved, changes = move_boxes(
                    parse_qa_markdown(source), before.version, request.outline, regions
                )
                workspace.move_boxes(
                    assignment_id,
                    before.version,
                    StoredOutline(stored.template, moved),
                    changes,
                    expected_source=source,
                )
                if changes.questions or changes.identity:
                    start_recut(workspace, jobs, assignment_id)
                return current(assignment_id, before.version)
            updated, regions = apply_outline(
                parse_qa_markdown(source),
                before.version,
                request.outline,
                TemplateIdentity(
                    regions.source_name,
                    regions.source_sha256,
                    regions.source_pages,
                    regions.source_page_size,
                ),
            )
            workspace.save_outline(
                assignment_id,
                before.version,
                StoredOutline(stored.template, regions),
                expected_source=source,
                source=updated,
            )
            changed(assignment_id)
            return current(assignment_id, before.version)

    @api.post("/assignments/{assignment_id}/outline/recut")
    def retry_recut(assignment_id: str, version: str | None = None) -> AssignmentOutline:
        with lock:
            require_assignment(assignment_id)
            retry_recuts(workspace, assignment_id)
            start_recut(workspace, jobs, assignment_id)
            return current(assignment_id, version)

    @api.get(
        "/assignments/{assignment_id}/template",
        response_class=FileResponse,
        responses={200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}},
    )
    def get_template(assignment_id: str, version: str | None = None) -> FileResponse:
        outline = current(assignment_id, version)
        stored = workspace.outlines(assignment_id).get(outline.version)
        if stored is None:
            raise HTTPException(404, "This assignment has no template yet.")
        return FileResponse(
            workspace.asset(stored.template),
            media_type="application/pdf",
            filename=stored.regions.source_name or "template.pdf",
            content_disposition_type="inline",
        )

    @api.get(
        "/assignments/{assignment_id}/outline/overlay",
        response_class=Response,
        responses={200: {"content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}}},
    )
    async def get_overlay(
        assignment_id: str, page: int = Query(ge=1), version: str | None = None
    ) -> Response:
        def locate() -> tuple[Path, Callable[[], None]]:
            outline = current(assignment_id, version)
            stored = workspace.outlines(assignment_id).get(outline.version)
            if stored is None or page > stored.regions.source_pages:
                raise HTTPException(404, "This template has no such page.")
            sources = workspace.submission_sources(assignment_id, outline.version)
            writing = writing_for(stored, sources)
            if writing is None:
                raise HTTPException(404, "No submissions use this version yet.")
            directory = jobs.store.assignments_dir / assignment_id / "overlays"
            prefix = hashlib.sha256(outline.version.encode()).hexdigest()[:12]
            cached = directory / f"{prefix}-{writing.revision}-{page}.png"

            def render() -> None:
                image = writing_overlay(
                    workspace.asset(stored.template), [workspace.asset(source) for source in sources], page
                )
                directory.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(dir=directory, suffix=".png", delete=False) as staging:
                    staging.write(image)
                Path(staging.name).replace(cached)
                for old in directory.glob(f"{prefix}-*.png"):
                    if not old.name.startswith(f"{prefix}-{writing.revision}-"):
                        old.unlink(missing_ok=True)

            return cached, render

        cached, render = await run_in_threadpool(locate)

        def load() -> bytes:
            if not cached.is_file():
                render()
            return cached.read_bytes()

        async with overlay_lock:
            image = await run_in_threadpool(load)
        return Response(image, media_type="image/png")

    @api.put("/assignments/{assignment_id}/template", openapi_extra=PDF_BODY)
    async def replace_template(
        assignment_id: str,
        request: Request,
        filename: str = Query(min_length=1, max_length=255),
        version: str | None = None,
    ) -> AssignmentOutline:
        name = _pdf_filename(filename)
        before = await run_in_threadpool(current, assignment_id, version)
        if before.lock:
            raise HTTPException(409, before.lock.reason)
        path, sha256 = await receive_pdf(
            request, uploads, MAX_TEMPLATE_BYTES, "Templates can be at most 50 MB."
        )
        try:
            identity = await run_in_threadpool(template_identity, path, name, sha256)
            template = await run_in_threadpool(workspace.add_asset, path)
        finally:
            path.unlink(missing_ok=True)
        def replace_stored() -> AssignmentOutline:
            with lock:
                require_unlocked(assignment_id)
                source = workspace.assignment_source(assignment_id)
                document = parse_qa_markdown(source)
                stored = workspace.outlines(assignment_id).get(before.version)
                regions = kept_regions(stored.regions, identity) if stored else empty_regions(
                    identity, document.title or ""
                )
                workspace.save_outline(
                    assignment_id,
                    before.version,
                    StoredOutline(template, regions),
                    expected_source=source,
                )
                changed(assignment_id)
                return current(assignment_id, before.version)

        return await run_in_threadpool(replace_stored)

    @api.post("/assignments/{assignment_id}/template/generate")
    def generate_template(assignment_id: str, version: str | None = None) -> AssignmentOutline:
        from opengrader.core.pdf.templates import generate_template_pdf
        from opengrader.core.qa_file import qa_to_template_questions

        before = current(assignment_id, version)
        require_unlocked(assignment_id)
        source = workspace.assignment_source(assignment_id)
        document = parse_qa_markdown(source)
        label = before.version or "A"
        with TemporaryDirectory(prefix="opengrader-template-") as temporary:
            path = Path(temporary) / f"template-{label}.pdf"
            try:
                layout = generate_template_pdf(
                    output_path=path,
                    version=label,
                    questions=qa_to_template_questions(document, version=label),
                    title=document.title or "Untitled assignment",
                    instructions=document.instructions,
                    continued_page_header=document.continued_page_header,
                    start_questions_on_first_page=document.questions_start_page == 1,
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            identity = template_identity(
                path, path.name, hashlib.sha256(path.read_bytes()).hexdigest()
            )
            template = workspace.add_asset(path)
        regions = replace(
            empty_regions(identity, document.title or ""),
            name_field=layout.name_field,
            student_id_field=layout.student_id_field,
            questions=layout.questions,
        )
        with lock:
            require_unlocked(assignment_id)
            workspace.save_outline(
                assignment_id,
                before.version,
                StoredOutline(template, regions),
                expected_source=source,
            )
            changed(assignment_id)
        return current(assignment_id, before.version)

    @api.post("/assignments/{assignment_id}/versions", status_code=201)
    def create_version(assignment_id: str, request: NewVersion) -> AssignmentOutline:
        with lock:
            require_assignment(assignment_id)
            require_unlocked(assignment_id)
            source = workspace.assignment_source(assignment_id)
            document = parse_qa_markdown(source)
            updated = add_version(document, request.label, request.copy_from)
            workspace.change_versions(
                assignment_id,
                render_qa_markdown(updated),
                expected_source=source,
                renamed={} if document.version_questions else {"": updated.versions[0]},
            )
            changed(assignment_id)
            return current(assignment_id, updated.versions[-1])

    @api.delete("/assignments/{assignment_id}/versions/{version}")
    def delete_version(assignment_id: str, version: str) -> AssignmentOutline:
        with lock:
            require_assignment(assignment_id)
            require_unlocked(assignment_id)
            source = workspace.assignment_source(assignment_id)
            updated = remove_version(parse_qa_markdown(source), version)
            workspace.change_versions(
                assignment_id,
                render_qa_markdown(updated),
                expected_source=source,
                renamed={},
                removed=version,
            )
            changed(assignment_id)
            return current(assignment_id, None)


def kept_regions(regions: RegionMap, identity: TemplateIdentity) -> RegionMap:
    """Carry boxes to a replacement template, dropping any on pages it no longer has."""

    def keep(box):  # noqa: ANN001, ANN202
        return box if box is not None and box.page <= identity.pages else None

    questions = []
    for question in regions.questions:
        segments = [segment for segment in question.segments if segment.page <= identity.pages]
        if segments:
            questions.append(
                replace(
                    question,
                    page=segments[0].page,
                    bbox=segments[0].bbox,
                    continuations=tuple(segments[1:]),
                )
            )
    return replace(
        regions,
        source_name=identity.filename,
        source_sha256=identity.sha256,
        source_pages=identity.pages,
        source_page_size=identity.page_size,
        name_field=keep(regions.name_field),
        student_id_field=keep(regions.student_id_field),
        title_field=keep(regions.title_field),
        questions=tuple(questions),
    )
