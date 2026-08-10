from __future__ import annotations

import re
import tempfile
from collections import defaultdict, deque
from pathlib import Path
import sys
from dataclasses import dataclass, field
from statistics import median
from typing import TYPE_CHECKING, Callable

from PIL import Image
from pypdf import PdfReader, PdfWriter

from opengrader.core.ocr.ollama_vlm import OllamaVisionConfig, extract_page_number
from opengrader.core.pdf_utils import norm_bbox, render_page_to_image, safe_image_to_data

if TYPE_CHECKING:  # pragma: no cover - typing only
    # fitz/pytesseract (~110ms each cold) are imported lazily at each call
    # site below — this reorder pass is one strategy among several the setup
    # screen offers, not something every TUI startup needs, and this module
    # is loaded eagerly by core/submissions/__init__.py.
    import fitz


_ROMAN_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)


def _roman_to_int(s: str) -> int:
    s = s.upper()
    roman = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    res = 0
    for i in range(len(s)):
        if i + 1 < len(s) and roman[s[i]] < roman[s[i + 1]]:
            res -= roman[s[i]]
        else:
            res += roman[s[i]]
    return res


def _word_to_int(s: str) -> int | None:
    words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
        "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    }
    return words.get(s.lower())


def _parse_page_number(text: str, max_pages: int | None = None) -> int | None:
    """Parse a page number out of a footer-ish text string.

    Shared by the Tesseract path (OCR of a rendered scan) and the born-digital
    path (a page's embedded text layer), so both interpret "Page 12", "12 / 45",
    Roman numerals, and number words identically.
    """
    if not text:
        return None

    # Remove common decorative symbols/punctuation used around page numbers
    # (brackets, parens, bullets, dashes, dots, separators)
    clean = re.sub(r"[\[\]\(\)·•\-\.\|—,;]", " ", text).strip()

    def validate(num: int) -> int | None:
        if num <= 0:
            return None
        # Sanity check: ignore numbers that are obviously not page numbers in this context
        if max_pages is not None and num > max_pages + 5:
            return None
        return num

    # 1. Explicit labels: "Page 12", "Page Twelve", "Page XII"
    page_match = re.search(r"page\s+([a-z\d]+)", clean, re.IGNORECASE)
    if page_match:
        val = page_match.group(1)
        if val.isdigit():
            return validate(int(val))
        word_val = _word_to_int(val)
        if word_val:
            return validate(word_val)
        if _ROMAN_RE.fullmatch(val):
            return validate(_roman_to_int(val))

    # 2. Fractions: "12 / 45" or "12 of 45"
    of_match = re.search(r"(\d+)\s*(?:of|/)\s*\d+", clean, re.IGNORECASE)
    if of_match:
        return validate(int(of_match.group(1)))

    # 3. Last resort: Extract all numeric-like tokens and work backwards.
    # Page numbers are usually the most specific/final piece of information in a footer.
    tokens = clean.split()
    for token in reversed(tokens):
        if token.isdigit():
            return validate(int(token))
        # Support Roman and Words if they appear as standalone tokens at the end
        if _ROMAN_RE.fullmatch(token):
            return validate(_roman_to_int(token))
        word_val = _word_to_int(token)
        if word_val:
            return validate(word_val)

    return None


def detect_page_number_tesseract(
    image: Image.Image, already_cropped: bool = False, max_pages: int | None = None
) -> int | None:
    """Extract page number from the bottom of a page image using Tesseract."""
    import pytesseract  # see the TYPE_CHECKING import above for why this is deferred

    if not already_cropped:
        w, h = image.size
        # Focus on the bottom 15% where page numbers usually reside
        image = image.crop((0, int(h * 0.85), w, h))

    # PSM 6 (single block) is better for finding structured text lines like footers
    text = pytesseract.image_to_string(image, config="--psm 6").strip()
    return _parse_page_number(text, max_pages=max_pages)


def detect_page_number_text_layer(
    page: "fitz.Page", max_pages: int | None = None
) -> int | None:
    """Read a page number from a born-digital page's embedded text layer.

    The fastest, most exact tier: no rasterization or OCR. Only the bottom of
    the page is considered (where footers live), so a "Page 3" printed in a
    question body can't masquerade as the page number. Returns None when the
    page has no text layer (a scanned image), so the caller falls back to OCR.
    """
    try:
        page_height = page.rect.height or 1.0
        footer_top = page_height * 0.85
        footer_words = [
            word[4]
            for word in page.get_text("words")
            # word = (x0, y0, x1, y1, text, block, line, word_no)
            if word[1] >= footer_top
        ]
        text = " ".join(footer_words).strip()
    except Exception:
        return None
    if not text:
        return None
    return _parse_page_number(text, max_pages=max_pages)


@dataclass(frozen=True)
class PageNumberLayoutInfo:
    # Normalized bounding box (x, y, w, h) where page numbers are expected
    bbox_norm: tuple[float, float, float, float]
    # Page dimensions (width, height) from the blank assignment for context
    page_w: int
    page_h: int
    # The total number of pages in the blank assignment template
    total_pages: int
    # Printed page number per template page position, in the template's
    # (correct) page order. None means no number is printed there — e.g. an
    # unnumbered title page when questions start on page 2.
    page_labels: tuple[int | None, ...] = field(default=())


def _crop_layout_region(
    image: Image.Image,
    bbox_norm: tuple[float, float, float, float],
) -> Image.Image | None:
    """Crop the page-number region from a rendered page, or None if degenerate."""
    x_norm, y_norm, w_norm, h_norm = bbox_norm
    x0 = max(0, int(x_norm * image.width))
    y0 = max(0, int(y_norm * image.height))
    x1 = min(image.width, int((x_norm + w_norm) * image.width))
    y1 = min(image.height, int((y_norm + h_norm) * image.height))
    if x1 <= x0 or y1 <= y0:
        return None
    return image.crop((x0, y0, x1, y1))


def _detect_page_number_in_layout(
    image: Image.Image,
    bbox_norm: tuple[float, float, float, float],
    max_pages: int,
) -> int | None:
    cropped = _crop_layout_region(image, bbox_norm)
    if cropped is None:
        return None
    return detect_page_number_tesseract(
        cropped, already_cropped=True, max_pages=max_pages
    )


def _load_page_number_layout(blank_template_path: Path) -> PageNumberLayoutInfo | None:
    """
    Analyzes a blank assignment PDF to find the expected location of page numbers.
    Returns PageNumberLayoutInfo if a consistent region is found, otherwise None.
    """
    import fitz  # see the TYPE_CHECKING import above for why this is deferred

    doc = fitz.open(blank_template_path)
    try:
        # Try to find page numbers on the first few pages to get a robust layout
        candidate_bboxes_px: list[tuple[int, int, int, int]] = []
        page_dims: tuple[int, int] | None = None

        for pg_idx in range(min(len(doc), 3)): # Check first 3 pages
            page_image, current_page_w, current_page_h = render_page_to_image(doc, pg_idx, dpi=300)
            if page_dims is None:
                page_dims = (current_page_w, current_page_h)

            # Focus OCR on the bottom 20% of the page for page numbers
            bottom_crop_px = (0, int(current_page_h * 0.80), current_page_w, current_page_h)
            cropped_img = page_image.crop(bottom_crop_px)

            # Use PSM 6 (uniform block of text) for better footer recognition
            config = "--psm 6"
            data = safe_image_to_data(cropped_img, config=config)

            for i, text in enumerate(data["text"]):
                text = text.strip()
                if not text:
                    continue

                # Strip common decorations for detection
                token_clean = re.sub(r"[\[\]\(\)·•\-\.\|—,;]", "", text).strip()
                if not token_clean:
                    continue

                # Check if it looks like a page number (arabic, roman, or word)
                if token_clean.isdigit() or _word_to_int(token_clean) or _ROMAN_RE.fullmatch(token_clean):
                    # Convert bbox from cropped image coordinates to full page coordinates
                    x0_cropped = data["left"][i]
                    y0_cropped = data["top"][i]
                    x1_cropped = x0_cropped + data["width"][i]
                    y1_cropped = y0_cropped + data["height"][i]

                    x0_full = x0_cropped
                    y0_full = y0_cropped + bottom_crop_px[1] # Add offset from full page
                    x1_full = x1_cropped
                    y1_full = y1_cropped + bottom_crop_px[1]

                    candidate_bboxes_px.append((x0_full, y0_full, x1_full, y1_full))

        if not candidate_bboxes_px or page_dims is None:
            return None

        # Calculate a representative bounding box (e.g., median of all detected boxes)
        # This helps to smooth out minor OCR inaccuracies or slight shifts.
        x_coords = [bbox[0] for bbox in candidate_bboxes_px]
        y_coords = [bbox[1] for bbox in candidate_bboxes_px]
        widths = [bbox[2] - bbox[0] for bbox in candidate_bboxes_px]
        heights = [bbox[3] - bbox[1] for bbox in candidate_bboxes_px]

        median_x = median(x_coords)
        median_y = median(y_coords)
        median_w = median(widths)
        median_h = median(heights)

        # Create a slightly expanded bbox around the median to account for variations
        pad_x = median_w * 0.5
        pad_y = median_h * 0.5

        final_x0 = max(0, int(median_x - pad_x))
        final_y0 = max(0, int(median_y - pad_y))
        final_x1 = min(page_dims[0], int(median_x + median_w + pad_x))
        final_y1 = min(page_dims[1], int(median_y + median_h + pad_y))

        # Convert to normalized bbox
        bbox_norm_result = norm_bbox(
            page_dims[0], page_dims[1],
            x0=final_x0, y0=final_y0, x1=final_x1, y1=final_y1
        )

        # The template is in the correct order, so the number printed at this
        # region on each page defines the reference sequence that scanned
        # submissions are reordered against. Pages without a printed number
        # (e.g. an unnumbered title page) are recorded as None.
        page_labels: list[int | None] = []
        for pg_idx in range(len(doc)):
            page_image, _, _ = render_page_to_image(doc, pg_idx, dpi=150)
            page_labels.append(
                _detect_page_number_in_layout(
                    page_image, bbox_norm_result, max_pages=len(doc)
                )
            )

        return PageNumberLayoutInfo(
            bbox_norm=bbox_norm_result,
            page_w=page_dims[0],
            page_h=page_dims[1],
            total_pages=len(doc),
            page_labels=tuple(page_labels),
        )

    finally:
        doc.close()

def _order_pages_by_template(
    detected: list[int | None],
    page_labels: tuple[int | None, ...],
    *,
    extra_work_pages: bool = False,
) -> tuple[list[int], list[str], set[int]]:
    """Order scanned pages to match the blank template's label sequence.

    The template is assumed to be in the correct order, so each template
    position's printed label is the reference. A scanned page detected as
    label N goes to the template position printing N.

    Pages the detector couldn't place (no detected label, or a detection the
    template never prints / prints fewer times than detected) fill the remaining
    template positions in scan order, so an unnumbered title page stays at the
    front instead of being pushed to the back.

    With ``extra_work_pages`` set, only the positions the template *deliberately*
    leaves unnumbered (e.g. a title page) are filled from those leftovers;
    everything else is treated as an extra student-work page and appended after
    the template pages, so downstream extraction sees the graded pages first.

    Returns the new page order, any warnings, and the set of **scan indices**
    that overflowed past the template — the extra work pages. The caller turns
    those into final page positions and records them so the grade screen can
    offer the scratch work from every question (see ``extra_pages.py``).
    """
    warnings: list[str] = []
    printed_labels = {label for label in page_labels if label is not None}

    label_slots: dict[int, deque[int]] = defaultdict(deque)
    for position, label in enumerate(page_labels):
        if label is not None:
            label_slots[label].append(position)

    assigned: dict[int, int] = {}
    unassigned: list[int] = []
    for scan_index, num in enumerate(detected):
        slots = label_slots.get(num) if num is not None else None
        if slots:
            assigned[scan_index] = slots.popleft()
            continue
        if num is not None:
            if num in printed_labels:
                warnings.append(
                    f"Page number {num} was detected on more scanned pages than "
                    f"the template prints it; keeping the first occurrence."
                )
            else:
                warnings.append(
                    f"Detected page number {num} is not printed anywhere in the "
                    f"blank template; treating the page as unnumbered."
                )
        unassigned.append(scan_index)

    used_positions = set(assigned.values())
    if extra_work_pages:
        # Fill only the template's intentionally-unnumbered slots; push the rest
        # to the end as extra work pages.
        free_positions = deque(
            position
            for position, label in enumerate(page_labels)
            if label is None and position not in used_positions
        )
    else:
        free_positions = deque(
            position
            for position in range(len(page_labels))
            if position not in used_positions
        )
    overflow_position = len(page_labels)
    overflowed: set[int] = set()
    for scan_index in unassigned:
        if free_positions:
            assigned[scan_index] = free_positions.popleft()
        else:
            # More scanned pages than template positions (extra scratch paper),
            # or an extra work page when extra_work_pages is set.
            assigned[scan_index] = overflow_position
            overflow_position += 1
            overflowed.add(scan_index)

    if extra_work_pages and overflowed:
        warnings.append(
            f"Moved {len(overflowed)} extra page(s) to the end (not matched to a "
            f"template page)."
        )

    order = sorted(assigned, key=lambda scan_index: (assigned[scan_index], scan_index))
    return order, warnings, overflowed


def _order_pages_by_detected_number(
    detected: list[int | None],
) -> tuple[list[int], list[str]]:
    """Order pages by detected number; undetected pages move to the back."""
    warnings: list[str] = []
    page_map = [
        (num if num is not None else float("inf"), i)
        for i, num in enumerate(detected)
    ]
    sorted_map = sorted(page_map, key=lambda x: x[0])

    # Check for large jumps in the detected sequence
    detected_sequence = [m[0] for m in sorted_map if m[0] != float("inf")]
    for i in range(len(detected_sequence) - 1):
        if abs(detected_sequence[i + 1] - detected_sequence[i]) > 2:
            warnings.append(
                f"Large page number jump detected between detected pages: "
                f"{detected_sequence[i]} followed by {detected_sequence[i + 1]}."
            )
            break

    return [m[1] for m in sorted_map], warnings


@dataclass(frozen=True)
class ReorderResult:
    """Outcome of one PDF's ordering pass.

    Truthy exactly when the file was rewritten, so the historical
    ``if reorder_pdf_pages(...)`` reads unchanged, while callers that care can
    also read which pages ended up as extra student work.
    """

    changed: bool
    # Final 1-based page positions (after reordering) of pages that could not be
    # matched to a template page — the student's scratch/overflow sheets. Only
    # ever populated when ``extra_work_pages`` was requested *and* a template
    # gave us something to match against; empty otherwise, because without a
    # reference sequence "extra" is not a conclusion we can draw.
    extra_work_pages: tuple[int, ...] = ()

    def __bool__(self) -> bool:
        return self.changed


def reorder_pdf_pages(
    pdf_path: str | Path,
    *,
    ollama_config: OllamaVisionConfig | None = None,
    blank_template_layout: PageNumberLayoutInfo | None = None,
    extra_work_pages: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> ReorderResult:
    """
    Checks page numbers in a PDF and rearranges them if out of order.

    When ``extra_work_pages`` is set, pages the detector can't match to the
    template sequence (extra scratch/work sheets) are moved to the end so
    downstream extraction processes the graded template pages first, and their
    final page positions are reported on the result so the caller can record
    them for the grader (see ``extra_pages.py``).
    """
    import fitz  # see the TYPE_CHECKING import above for why this is deferred

    path = Path(pdf_path)
    reader = PdfReader(path)
    if len(reader.pages) <= 1:
        return ReorderResult(changed=False)

    doc = fitz.open(path)
    num_pages = len(doc)

    # When a blank template layout is provided, we use its total page count as the
    # expected sequence length and the upper bound for valid page number detection.
    expected_total = num_pages
    if blank_template_layout:
        expected_total = blank_template_layout.total_pages
        if num_pages != expected_total:
            print(f"Warning: PDF {path.name} has {num_pages} pages, but the template expects {expected_total}.", file=sys.stderr)

    detected: list[int | None] = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        for i in range(num_pages):
            # Tier 1 — born-digital text layer: fastest and exact, and needs no
            # rasterization. Only born-digital PDFs have one; a scan returns None.
            num = detect_page_number_text_layer(doc[i], max_pages=expected_total)

            # Only rasterize when the text layer didn't answer (scanned pages).
            if num is None:
                # Render at lower DPI for a faster check, but high enough for OCR.
                img, _, _ = render_page_to_image(doc, i, dpi=150)

                # Tier 1.5 — Opengrader QR: if the sheet was generated with QR
                # codes, the page number is encoded exactly. Free here — the page
                # is already rasterized — and beats every OCR tier below.
                from opengrader.core.ocr.qr_reader import read_code_from_image

                code = read_code_from_image(img)
                if code is not None and code.page is not None:
                    num = code.page

                # Tier 2 — OCR: look where the template prints page numbers first,
                # then the generic bottom strip.
                if num is None and blank_template_layout:
                    num = _detect_page_number_in_layout(
                        img, blank_template_layout.bbox_norm, max_pages=expected_total
                    )
                if num is None:
                    num = detect_page_number_tesseract(img, max_pages=expected_total)

                # Tier 3 — Ollama VLM: handwritten/stylized numbers OCR can't read.
                if num is None and ollama_config:
                    img_file = tmp_path / f"p{i}.png"
                    img.save(img_file)
                    num = extract_page_number(image_path=img_file, config=ollama_config)

            detected.append(num)

    doc.close()

    reorder_warnings: list[str] = []

    template_labels = blank_template_layout.page_labels if blank_template_layout else ()
    expected_unnumbered = 0
    overflowed: set[int] = set()
    if any(label is not None for label in template_labels):
        # The blank template is in the correct order: use its printed label
        # sequence as the ordering reference. Pages the template leaves
        # unnumbered (e.g. a title page) are expected to be undetected.
        new_order, order_warnings, overflowed = _order_pages_by_template(
            detected, template_labels, extra_work_pages=extra_work_pages
        )
        expected_unnumbered = sum(1 for label in template_labels if label is None)
    else:
        new_order, order_warnings = _order_pages_by_detected_number(detected)
    reorder_warnings.extend(order_warnings)

    # Translate the overflowed *scan* indices into the page positions they hold
    # in the reordered file — that is what a grader (and the submission
    # frontmatter) refers to. Computed before the "nothing moved" early return
    # below, because a scan that was already in order can still carry extra
    # pages worth recording.
    extra_positions = tuple(
        position + 1
        for position, scan_index in enumerate(new_order)
        if scan_index in overflowed
    )

    num_undetected_pages = sum(1 for num in detected if num is None)
    if num_undetected_pages > expected_unnumbered:
        reorder_warnings.append(f"Page numbers could not be detected for {num_undetected_pages} out of {num_pages} pages.")

    # Calculate how many pages were actually moved from their original position
    num_pages_moved = sum(
        1 for position, scan_index in enumerate(new_order) if position != scan_index
    )
    if num_pages_moved > 0 and num_pages_moved / num_pages > 0.15: # If more than 15% of pages were moved
        reorder_warnings.append(f"Significant reordering detected: {num_pages_moved} out of {num_pages} pages were moved.")

    if reorder_warnings:
        print(f"Warning: Reordering of {path.name} completed with concerns:", file=sys.stderr)
        for warning in reorder_warnings:
            print(f"  - {warning}", file=sys.stderr)

    if new_order == list(range(num_pages)):
        return ReorderResult(changed=False, extra_work_pages=extra_positions)

    writer = PdfWriter()
    for original_index in new_order:
        writer.add_page(reader.pages[original_index])

    with open(path, "wb") as f:
        writer.write(f)
    return ReorderResult(changed=True, extra_work_pages=extra_positions)