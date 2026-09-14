"""Validate and materialize reviewed scan pages without changing the source PDF."""

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence
from tempfile import NamedTemporaryFile

from pypdf import PdfReader, PdfWriter


@dataclass(frozen=True)
class ScanPage:
    number: int
    rotation: int = 0


def validate_scan_plan(
    total_pages: int,
    groups: Sequence[Sequence[ScanPage]],
    excluded: Sequence[int],
) -> None:
    """Every source page must occur exactly once, either in a group or excluded."""
    if not groups or any(not group for group in groups):
        raise ValueError("Keep at least one page in each submission.")
    pages = [page.number for group in groups for page in group] + list(excluded)
    if len(pages) != total_pages or set(pages) != set(range(1, total_pages + 1)):
        raise ValueError("Include each scan page exactly once, or mark it as excluded.")
    if any(page.rotation not in (0, 90, 180, 270) for group in groups for page in group):
        raise ValueError("Page rotations must be 0, 90, 180, or 270 degrees.")


def write_scan_submission(source: Path, destination: Path, pages: Sequence[ScanPage]) -> None:
    """Write an ordered group to a new PDF; callers own destination publication."""
    write_scan_submissions(source, {destination: pages})


def write_scan_submissions(source: Path, groups: Mapping[Path, Sequence[ScanPage]]) -> None:
    """Read the bulk PDF once and publish only completely written split files."""
    reader = PdfReader(source)
    for destination, pages in groups.items():
        writer = PdfWriter()
        for selection in pages:
            if not 1 <= selection.number <= len(reader.pages):
                raise ValueError("Selected page is outside the scan.")
            page = writer.add_page(reader.pages[selection.number - 1])
            if selection.rotation:
                page.rotate(selection.rotation)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False)
        path = Path(temporary.name)
        try:
            with temporary:
                writer.write(temporary)
            path.replace(destination)
        finally:
            path.unlink(missing_ok=True)
