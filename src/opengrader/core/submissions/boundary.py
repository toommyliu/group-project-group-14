"""Content-aware submission boundary detection.

Used when a scanned bundle is split *without* a fixed pages-per-submission
count and *without* a blank template — we can't assume every student used the
same number of pages (extra work sheets, blanks, a cover page or not).

The signal, per the design: **a submission's first page carries a student Name
and/or Student ID field** (a cover page has these too). Page 1 of the bundle is
always the first page of submission 1. Walking forward, the next page bearing a
Name/ID header starts the next submission.

**Cover-page coalescing** makes this hold up on real scans: a Name/ID page only
starts a *new* submission when the page just before it was *not* itself a Name/ID
page. So a cover page immediately followed by a first content page that also
shows Name/ID collapses into one submission start, and two first pages that land
next to each other are left together rather than mis-split. Everything else —
content pages, blank/scratch pages — is a "continuation" absorbed into the
current submission (a blank page carries no header, so it never starts a
submission and correctly lets the *next* header page split).

The pure decision (`detect_submission_boundaries`) works on a per-page boolean
list so it is fully unit-testable; the image/VLM feature extraction lives
separately.
"""

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


# Header tokens that mark a submission's first/cover page.
_IDENTITY_HEADER_RE = re.compile(
    r"\b(?:name|student\s*(?:id|number|no|name)|id\s*number|sid)\b",
    re.IGNORECASE,
)

# Below this many alphanumeric characters, the top-strip OCR is treated as
# unreadable and (if configured) the VLM is asked instead.
_MIN_READABLE_ALNUM = 6


@dataclass(frozen=True)
class DetectedBoundaries:
    """Result of the pure boundary decision.

    ``ranges`` are inclusive 0-based ``(start_index, end_index)`` page spans.
    ``warnings`` flag spots a human should eyeball (unusual-length submissions).
    """

    ranges: list[tuple[int, int]]
    warnings: list[str]


def detect_submission_boundaries(
    is_first_page: Sequence[bool],
) -> DetectedBoundaries:
    """Group page indices into submissions from a per-page first-page flag.

    Page 0 always begins submission 1. A later first-page page starts a new
    submission only when the page immediately before it was *not* a first page
    (cover-page coalescing — see the module docstring).
    """
    n = len(is_first_page)
    if n == 0:
        return DetectedBoundaries(ranges=[], warnings=[])

    starts: list[int] = [0]
    prev_was_first = True  # page 0 is the anchor first page
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
    """Warn about submissions whose page count is far from the median.

    Catches both mis-merges (too long) and mis-splits (too short) so a human can
    review the ambiguous spots instead of trusting the guess.
    """
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


# --------------------------------------------------------------------------- #
# Per-page feature extraction (image + optional VLM)
# --------------------------------------------------------------------------- #


def page_has_identity_header(image, *, top_fraction: float = 0.35) -> tuple[bool, bool]:
    """Detect a Name/Student-ID header in the top strip via Tesseract.

    Returns ``(has_header, ocr_readable)``. ``ocr_readable`` is False when the
    top strip produced almost no text (a poor scan), signalling the caller to
    fall back to the VLM if one is configured.
    """
    # Import lazily: pytesseract (~110ms cold) is only needed on the
    # content-aware boundary-detection path (no fixed page count, no blank
    # template), not on every TUI startup — this module is pulled in eagerly
    # by core/submissions/__init__.py.
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
    """Render each page once and return ``(is_first_page, qr_page_number)``.

    Per page, cheapest/most-certain first:

    0. **Opengrader QR** — if the sheet was generated with QR codes, the page's
       code carries its exact page number (``qr_page``). ``page == 1`` is a
       submission start; ``page > 1`` is definitively a continuation.
    1. **Tesseract** Name/ID-header detection on the top strip.
    2. **VLM** (when ``ollama_config`` is given) if the top strip is unreadable.

    A blank page with no signal reads as a non-first continuation page — the safe
    default. ``qr_page`` is ``None`` whenever the page carries no readable QR
    (the caller uses "every page has one" to decide it can trust QR exactly).
    """
    from opengrader.core.ocr.qr_reader import read_code_from_image

    # Import lazily: fitz (PyMuPDF, ~110ms cold) — see page_has_identity_header
    # above for why this module defers its heavy OCR dependencies.
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

                # Tier 0 — QR: a decoded page number settles it with no OCR.
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
    """Per-page first/cover-page flags (see :func:`_analyze_pages`)."""
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
    """Exact boundaries from per-page QR page numbers, or ``None`` if unusable.

    Requires *every* page to carry a QR page number — then a new submission
    begins at page 0 and at every page whose QR number is 1, with **no**
    cover-page coalescing: QR numbers are exact, so two adjacent single-page
    submissions (both ``page == 1``) are correctly kept apart, unlike the
    OCR-header heuristic which can't tell a cover page from a first content page.
    """
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
    """Detect submission boundaries in a bundle and return named 1-based ranges.

    Returns ``(ranges, warnings)`` ready for ``split_scanned_submission_pdf``.

    Prefers exact QR page numbers when every page carries one; otherwise falls
    back to the Name/ID-header + VLM first-page heuristic. Both come from a single
    render pass.
    """
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
            start_page=start + 1,  # 0-based index -> 1-based page
            end_page=end + 1,
        )
        for index, (start, end) in enumerate(detected.ranges, start=1)
    ]
    return ranges, detected.warnings
