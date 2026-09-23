from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from collections import Counter
from typing import TYPE_CHECKING, Callable, Sequence

from opengrader.core.pdf_utils import render_page_to_image

if TYPE_CHECKING:  # pragma: no cover
    from opengrader.core.ocr.vision_reader import VisionReader
from opengrader.core.submissions.splitter import SubmissionPageRange, _sanitize_filename


_IDENTITY_HEADER_RE = re.compile(
    r"\b(?:name|student\s*(?:id|number|no|name)|id\s*number|sid)\b",
    re.IGNORECASE,
)

_MIN_READABLE_ALNUM = 6


@dataclass(frozen=True)
class DetectedBoundaries:
    ranges: list[tuple[int, int]]
    warnings: list[str]


def detect_submission_boundaries(
    is_first_page: Sequence[bool],
    *,
    page_numbers: Sequence[int | None] | None = None,
    expected_pages: int | None = None,
) -> DetectedBoundaries:
    if expected_pages is not None and expected_pages < 1:
        raise ValueError("expected_pages must be positive")
    numbers = list(page_numbers) if page_numbers is not None else [None] * len(is_first_page)
    if len(numbers) != len(is_first_page):
        raise ValueError("Each scan page needs one page-number observation")
    if not is_first_page:
        return DetectedBoundaries(ranges=[], warnings=[])
    starts = {0}
    for i in range(1, len(is_first_page)):
        number, previous = numbers[i], numbers[i - 1]
        restart = number == 1 or (number is not None and previous is not None and number < previous)
        header_start = number is None and is_first_page[i] and not is_first_page[i - 1]
        if restart or header_start:
            starts.add(i)
    # A numbered continuation can locate its unnumbered first page, including
    # adjacent identity headers when the preceding student's scan is short.
    for index, number in enumerate(numbers):
        if number is None or number <= 1:
            continue
        candidate = index - number + 1
        if candidate < 0 or not is_first_page[candidate]:
            continue
        if all(observed is None or observed == offset + 1
               for offset, observed in enumerate(numbers[candidate:index + 1])):
            starts.add(candidate)
    ordered_starts = sorted(starts)
    ranges = [(start, ordered_starts[i + 1] - 1 if i + 1 < len(ordered_starts) else len(numbers) - 1)
              for i, start in enumerate(ordered_starts)]
    expected = expected_pages or _infer_expected_pages(ranges, numbers)
    warnings = [warning for start, end in ranges
                for warning in submission_warnings(end - start + 1, expected, numbers[start:end + 1],
                                                   label=f"Submission at pages {start + 1}-{end + 1}")]
    return DetectedBoundaries(ranges=ranges, warnings=warnings)


def _infer_expected_pages(ranges: Sequence[tuple[int, int]], numbers: Sequence[int | None]) -> int | None:
    observed = [number for number in numbers if number is not None]
    if observed and max(observed) > 1:
        return max(observed)
    if len(ranges) < 2:
        return None
    counts = Counter(end - start + 1 for start, end in ranges)
    return max(counts, key=lambda length: (counts[length], length))


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


def _printed_page_number(page, image) -> int | None:
    import pytesseract

    pattern = re.compile(r"^\s*(?:page\s+)?(\d{1,3})(?:\s*(?:of|/)\s*\d{1,3})?\s*$", re.I)
    width, height = image.size
    text = page.get_text("text", clip=(page.rect.width * .35, page.rect.height * .85,
                                      page.rect.width * .65, page.rect.height))
    if not text.strip():
        footer = image.crop((int(width * .35), int(height * .85), int(width * .65), height))
        text = pytesseract.image_to_string(footer, config="--psm 6")
    matches = [pattern.fullmatch(line) for line in text.splitlines()]
    numbers = [int(match[1]) for match in matches if match and int(match[1]) > 0]
    if len(numbers) == 1:
        return numbers[0]
    header_text = page.get_text("text", clip=(0, 0, page.rect.width, page.rect.height * .15))
    if not header_text.strip():
        header_text = pytesseract.image_to_string(image.crop((0, 0, width, int(height * .15))), config="--psm 6")
    header_numbers = re.findall(r"\bpage\s+(\d{1,3})(?:\s*(?:of|/)\s*\d{1,3})?\b", header_text, re.I)
    return int(header_numbers[0]) if len(header_numbers) == 1 and int(header_numbers[0]) > 0 else None


def page_has_identity_header(image, *, top_fraction: float = 0.35) -> tuple[bool, bool]:
    import pytesseract

    width, height = image.size
    top = image.crop((0, 0, width, max(1, int(height * top_fraction))))
    try:
        text = pytesseract.image_to_string(top, config="--psm 6")
    except Exception:
        return False, False
    alnum = re.sub(r"[^A-Za-z0-9]", "", text)
    readable = len(alnum) >= _MIN_READABLE_ALNUM
    return bool(_IDENTITY_HEADER_RE.search(text)), readable


def _analyze_pages(
    pdf_path: str | Path,
    *,
    vision_reader: VisionReader | None = None,
    dpi: int = 150,
    progress_callback: Callable[[str], None] | None = None,
) -> list[tuple[bool, int | None]]:
    from opengrader.core.ocr.qr_reader import read_code_from_image

    import fitz

    path = Path(pdf_path)
    doc = fitz.open(path)
    features: list[tuple[bool, int | None]] = []
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            for i in range(len(doc)):
                if progress_callback is not None:
                    progress_callback(f"Analyzing page {i + 1}/{len(doc)}…")
                image, _, _ = render_page_to_image(doc, i, dpi=dpi)

                code = read_code_from_image(image)
                if code is not None and code.page is not None:
                    features.append((code.page == 1, code.page))
                    continue

                has_header, readable = page_has_identity_header(image)
                if not has_header and not readable and vision_reader is not None:
                    img_file = tmp_path / f"p{i}.png"
                    image.save(img_file)
                    try:
                        has_header = vision_reader.page_has_identity_field(
                            image_path=img_file
                        )
                    except Exception:
                        has_header = False
                features.append((has_header, _printed_page_number(doc[i], image)))
    finally:
        doc.close()
    return features


def boundaries_from_qr_pages(
    qr_pages: Sequence[int | None],
) -> DetectedBoundaries | None:
    if not qr_pages or any(page is None for page in qr_pages):
        return None
    return detect_submission_boundaries([page == 1 for page in qr_pages], page_numbers=qr_pages)


def plan_boundary_ranges(
    pdf_path: str | Path,
    *,
    vision_reader: VisionReader | None = None,
    expected_pages: int | None = None,
    filename_prefix: str = "submission",
    zero_pad_width: int = 4,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[list[SubmissionPageRange], list[str]]:
    """Ranges use 1-based page numbers."""
    features = _analyze_pages(
        pdf_path,
        vision_reader=vision_reader,
        progress_callback=progress_callback,
    )
    numbers = [number for _header, number in features]
    detected = detect_submission_boundaries(
        [header for header, _number in features], page_numbers=numbers, expected_pages=expected_pages,
    )
    expected_pages = expected_pages or _infer_expected_pages(detected.ranges, numbers)

    prefix = _sanitize_filename(filename_prefix)
    ranges = [
        SubmissionPageRange(
            name=f"{prefix}-{index:0{zero_pad_width}d}",
            start_page=start + 1,
            end_page=end + 1,
            expected_pages=expected_pages,
            warnings=submission_warnings(end - start + 1, expected_pages, numbers[start:end + 1]),
        )
        for index, (start, end) in enumerate(detected.ranges, start=1)
    ]
    return ranges, detected.warnings
