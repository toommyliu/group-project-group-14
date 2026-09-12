from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Callable, Sequence

from opengrader.core.ocr.ollama_vlm import (
    OllamaVisionConfig,
    page_has_identity_field,
)
from opengrader.core.pdf_utils import render_page_to_image
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
) -> DetectedBoundaries:
    n = len(is_first_page)
    if n == 0:
        return DetectedBoundaries(ranges=[], warnings=[])

    starts: list[int] = [0]
    prev_was_first = True
    for i in range(1, n):
        if is_first_page[i] and not prev_was_first:
            starts.append(i)
        prev_was_first = is_first_page[i]

    ranges: list[tuple[int, int]] = []
    for idx, start in enumerate(starts):
        end = (starts[idx + 1] - 1) if idx + 1 < len(starts) else (n - 1)
        ranges.append((start, end))

    warnings = _flag_unusual_lengths(ranges)
    return DetectedBoundaries(ranges=ranges, warnings=warnings)


def _flag_unusual_lengths(ranges: list[tuple[int, int]]) -> list[str]:
    if len(ranges) < 3:
        return []
    lengths = [end - start + 1 for start, end in ranges]
    med = median(lengths)
    if med <= 0:
        return []
    warnings: list[str] = []
    for (start, end), length in zip(ranges, lengths):
        if length < max(1, med / 2) or length > med * 2:
            warnings.append(
                f"Submission at pages {start + 1}-{end + 1} has {length} page(s), "
                f"unusual vs. the median of {med:g}; review this boundary."
            )
    return warnings


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
    ollama_config: OllamaVisionConfig | None = None,
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
                if not has_header and not readable and ollama_config is not None:
                    img_file = tmp_path / f"p{i}.png"
                    image.save(img_file)
                    try:
                        has_header = page_has_identity_field(
                            image_path=img_file, config=ollama_config
                        )
                    except Exception:
                        has_header = False
                features.append((has_header, None))
    finally:
        doc.close()
    return features


def detect_page_signals(
    pdf_path: str | Path,
    *,
    ollama_config: OllamaVisionConfig | None = None,
    dpi: int = 150,
    progress_callback: Callable[[str], None] | None = None,
) -> list[bool]:
    return [
        is_first
        for is_first, _qr_page in _analyze_pages(
            pdf_path,
            ollama_config=ollama_config,
            dpi=dpi,
            progress_callback=progress_callback,
        )
    ]


def boundaries_from_qr_pages(
    qr_pages: Sequence[int | None],
) -> DetectedBoundaries | None:
    if not qr_pages or any(page is None for page in qr_pages):
        return None
    starts = [0] + [i for i in range(1, len(qr_pages)) if qr_pages[i] == 1]
    ranges: list[tuple[int, int]] = []
    for idx, start in enumerate(starts):
        end = (starts[idx + 1] - 1) if idx + 1 < len(starts) else (len(qr_pages) - 1)
        ranges.append((start, end))
    return DetectedBoundaries(ranges=ranges, warnings=_flag_unusual_lengths(ranges))


def plan_boundary_ranges(
    pdf_path: str | Path,
    *,
    ollama_config: OllamaVisionConfig | None = None,
    filename_prefix: str = "submission",
    zero_pad_width: int = 4,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[list[SubmissionPageRange], list[str]]:
    """Ranges use 1-based page numbers."""
    features = _analyze_pages(
        pdf_path,
        ollama_config=ollama_config,
        progress_callback=progress_callback,
    )
    detected = boundaries_from_qr_pages([qr_page for _flag, qr_page in features])
    if detected is None:
        detected = detect_submission_boundaries([flag for flag, _qr in features])

    prefix = _sanitize_filename(filename_prefix)
    ranges = [
        SubmissionPageRange(
            name=f"{prefix}-{index:0{zero_pad_width}d}",
            start_page=start + 1,
            end_page=end + 1,
        )
        for index, (start, end) in enumerate(detected.ranges, start=1)
    ]
    return ranges, detected.warnings
