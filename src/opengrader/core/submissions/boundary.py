"""What a submission of the wrong length, or with a page number repeated or
missing, is flagged with. Shared by every way a scan is cut into submissions
(every N pages, the web app's scan review), so the review queue reads one
wording whichever cut it.

This module once also found where submissions start from Name/ID headers and
OCR'd page numbers (`--detect-boundaries`); matching pages to each other
(`core/pages/split.py`) replaced that.
"""

from __future__ import annotations

from typing import Sequence


def submission_warnings(
    page_count: int,
    expected_pages: int | None,
    numbers: Sequence[int | None] = (),
    *,
    label: str = "Submission",
) -> tuple[str, ...]:
    warnings: list[str] = []
    if expected_pages is not None and page_count < expected_pages:
        warnings.append(f"{label}: incomplete scan, {page_count} of {expected_pages} expected pages; "
                        f"{expected_pages - page_count} page(s) missing. Check the original scan.")
    elif expected_pages is not None and page_count > expected_pages:
        warnings.append(f"{label}: unusual length, {page_count} pages for {expected_pages} expected; "
                        "check for extra work, duplicate pages, or a missed boundary.")
    observed = [number for number in numbers if number is not None]
    if len(observed) != len(set(observed)):
        warnings.append(f"{label}: repeated printed page numbers; check for duplicate scans.")
    if expected_pages and numbers and all(number is not None for number in numbers):
        missing = sorted(set(range(1, expected_pages + 1)) - set(observed))
        if missing:
            warnings.append(f"{label}: missing printed page(s) {', '.join(map(str, missing))}.")
    return tuple(warnings)
