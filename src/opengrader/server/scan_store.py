"""Atomic batch records. The server's coordinator lock serializes mutations."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from fastapi import HTTPException

from opengrader.server.scan_models import MAX_SCAN_PAGES, ScanBatch


class ScanStore:
    def __init__(self, assignments_dir: Path):
        self.assignments_dir = assignments_dir

    def root(self, assignment_id: str) -> Path:
        return self.assignments_dir / assignment_id / ".opengrader" / "scans"

    def directory(self, assignment_id: str, batch_id: str) -> Path:
        # IDs from URL paths never become arbitrary filesystem paths.
        try:
            if str(UUID(batch_id)) != batch_id:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(404, "Scan not found.") from exc
        return self.root(assignment_id) / batch_id

    def list(self, assignment_id: str) -> list[ScanBatch]:
        root = self.root(assignment_id)
        return sorted(
            (ScanBatch.model_validate_json(path.read_text()) for path in root.glob("*/batch.json")),
            key=lambda batch: batch.created_at,
        )

    def get(self, assignment_id: str, batch_id: str) -> ScanBatch:
        path = self.directory(assignment_id, batch_id) / "batch.json"
        if not path.is_file():
            raise HTTPException(404, "Scan not found.")
        return ScanBatch.model_validate_json(path.read_text())

    def save(self, assignment_id: str, batch: ScanBatch) -> None:
        directory = self.directory(assignment_id, batch.id)
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / "batch.json"
        temporary = directory / "batch.tmp"
        temporary.write_text(batch.model_dump_json())
        temporary.replace(destination)

    def accept(self, assignment_id: str, source: Path, filename: str, digest: str) -> ScanBatch:
        for batch in self.list(assignment_id):
            if batch.sha256 == digest:
                return batch
        try:
            with source.open("rb") as stream:
                document = PdfReader(stream)
                if document.is_encrypted:
                    raise ValueError("Upload an unlocked PDF.")
                count = len(document.pages)
                if count < 1 or count > MAX_SCAN_PAGES:
                    raise ValueError(f"Upload a PDF with 1 to {MAX_SCAN_PAGES:,} pages.")
        except (PdfReadError, ValueError) as exc:
            raise HTTPException(422, f"Could not open this scan. {exc}") from exc
        batch = ScanBatch(
            id=str(uuid4()), filename=filename, sha256=digest, page_count=count,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        directory = self.directory(assignment_id, batch.id)
        directory.mkdir(parents=True)
        source.replace(directory / "original.pdf")
        self.save(assignment_id, batch)
        return batch

    def recover(self) -> None:
        for assignment in self.assignments_dir.iterdir():
            if not assignment.is_dir() or assignment.name.startswith("."):
                continue
            for batch in self.list(assignment.name):
                if batch.status in ("checking", "queued", "processing"):
                    batch.status = "failed"
                    batch.message = "Processing was interrupted. Retry to continue; completed submissions are preserved."
                    batch.revision += 1
                    self.save(assignment.name, batch)
