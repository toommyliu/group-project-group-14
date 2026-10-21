from __future__ import annotations

import re
import json
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from .scan_notes import SCAN_WARNINGS_SUFFIX, read_note_path, write_note


@dataclass(frozen=True)
class SubmissionPageRange:
    name: str
    start_page: int
    end_page: int
    expected_pages: int | None = None
    warnings: tuple[str, ...] = ()


def _sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = cleaned.strip("-._")
    return cleaned or "submission"


def plan_submission_ranges(
    *,
    total_pages: int,
    pages_per_submission: int,
    start_page: int = 1,
    end_page: int | None = None,
    filename_prefix: str = "submission",
    zero_pad_width: int = 4,
) -> list[SubmissionPageRange]:
    if total_pages <= 0:
        raise ValueError("input PDF has no pages")

    if pages_per_submission <= 0:
        raise ValueError("pages_per_submission must be a positive integer")

    if start_page < 1:
        raise ValueError("start_page must be >= 1")

    last_page = total_pages if end_page is None else end_page
    if last_page < start_page:
        raise ValueError("end_page must be >= start_page")
    if last_page > total_pages:
        raise ValueError(
            f"end_page ({last_page}) exceeds total pages in input PDF ({total_pages})"
        )

    from .boundary import submission_warnings

    prefix = _sanitize_filename(filename_prefix)
    ranges: list[SubmissionPageRange] = []

    current_start = start_page
    index = 1
    while current_start <= last_page:
        current_end = min(current_start + pages_per_submission - 1, last_page)
        ranges.append(
            SubmissionPageRange(
                name=f"{prefix}-{index:0{zero_pad_width}d}",
                start_page=current_start,
                end_page=current_end,
                expected_pages=pages_per_submission,
                warnings=submission_warnings(current_end - current_start + 1, pages_per_submission),
            )
        )
        current_start = current_end + 1
        index += 1

    return ranges


def split_scanned_submission_pdf(
    input_pdf: str | Path,
    output_dir: str | Path,
    ranges: list[SubmissionPageRange],
) -> list[Path]:
    input_path = Path(input_pdf)
    if not input_path.exists():
        raise FileNotFoundError(f"input PDF not found: {input_path}")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(input_path))
    total_pages = len(reader.pages)
    if total_pages <= 0:
        raise ValueError(f"input PDF has no pages: {input_path}")

    created_files: list[Path] = []
    for page_range in ranges:
        if page_range.start_page < 1 or page_range.end_page > total_pages:
            raise ValueError(
                f"invalid range {page_range.start_page}-{page_range.end_page} for input PDF with {total_pages} pages"
            )

        writer = PdfWriter()
        for page_index in range(page_range.start_page - 1, page_range.end_page):
            writer.add_page(reader.pages[page_index])

        destination = output_path / f"{page_range.name}.pdf"
        with open(destination, "wb") as f:
            writer.write(f)

        write_scan_warnings(destination, page_range.warnings)
        created_files.append(destination)

    return created_files


def write_scan_warnings(pdf: Path, warnings: tuple[str, ...]) -> None:
    write_note(pdf, SCAN_WARNINGS_SUFFIX, json.dumps({"warnings": list(warnings)}, indent=2) + "\n")


def read_scan_warnings(pdf: Path) -> tuple[str, ...]:
    path = read_note_path(pdf, SCAN_WARNINGS_SUFFIX)
    if not path.exists():
        return ()
    data = json.loads(path.read_text())
    warnings = data.get("warnings") if isinstance(data, dict) else None
    if not isinstance(warnings, list) or not all(isinstance(warning, str) for warning in warnings):
        raise ValueError(f"Invalid scan warnings in {path}")
    return tuple(warnings)
