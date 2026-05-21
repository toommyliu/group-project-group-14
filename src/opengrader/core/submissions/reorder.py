from __future__ import annotations

import re
import tempfile
from pathlib import Path
import sys
from dataclasses import dataclass
from statistics import median
from typing import Callable

import fitz
import pytesseract
from PIL import Image
from pypdf import PdfReader, PdfWriter

from opengrader.core.ocr.ollama_vlm import OllamaVisionConfig, extract_page_number
from opengrader.core.pdf_utils import norm_bbox, render_page_to_image, safe_image_to_data


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


def detect_page_number_tesseract(
    image: Image.Image, already_cropped: bool = False, max_pages: int | None = None
) -> int | None:
    """Extract page number from the bottom of a page image using Tesseract."""
    if not already_cropped:
        w, h = image.size
        # Focus on the bottom 15% where page numbers usually reside
        image = image.crop((0, int(h * 0.85), w, h))

    # PSM 6 (single block) is better for finding structured text lines like footers
    text = pytesseract.image_to_string(image, config="--psm 6").strip()
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


@dataclass(frozen=True)
class PageNumberLayoutInfo:
    # Normalized bounding box (x, y, w, h) where page numbers are expected
    bbox_norm: tuple[float, float, float, float]
    # Page dimensions (width, height) from the blank exam for context
    page_w: int
    page_h: int

def _load_page_number_layout(blank_exam_path: Path) -> PageNumberLayoutInfo | None:
    """
    Analyzes a blank exam PDF to find the expected location of page numbers.
    Returns PageNumberLayoutInfo if a consistent region is found, otherwise None.
    """
    doc = fitz.open(blank_exam_path)
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
        return PageNumberLayoutInfo(bbox_norm=bbox_norm_result, page_w=page_dims[0], page_h=page_dims[1])

    finally:
        doc.close()

def reorder_pdf_pages(
    pdf_path: str | Path,
    *,
    ollama_config: OllamaVisionConfig | None = None,
    blank_exam_page_number_layout: PageNumberLayoutInfo | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> bool:
    """
    Checks page numbers in a PDF and rearranges them if out of order.
    Returns True if the file was modified, False otherwise.
    """
    path = Path(pdf_path)
    reader = PdfReader(path)
    if len(reader.pages) <= 1:
        return False

    doc = fitz.open(path)
    num_pages = len(doc)
    page_map: list[tuple[int | float, int]] = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        num_undetected_pages = 0
        tmp_path = Path(tmp_dir)
        for i in range(len(doc)):
            # Render at lower DPI for faster reordering check, but high enough for OCR
            img, _, _ = render_page_to_image(doc, i, dpi=150)
            
            num = None
            if blank_exam_page_number_layout:
                # Use the provided layout to crop a specific region
                layout_x_norm, layout_y_norm, layout_w_norm, layout_h_norm = blank_exam_page_number_layout.bbox_norm
                
                # Scale normalized bbox to current image dimensions
                x0 = int(layout_x_norm * img.width)
                y0 = int(layout_y_norm * img.height)
                x1 = int((layout_x_norm + layout_w_norm) * img.width)
                y1 = int((layout_y_norm + layout_h_norm) * img.height)
                
                # Ensure crop coordinates are within image bounds
                x0 = max(0, x0)
                y0 = max(0, y0)
                x1 = min(img.width, x1)
                y1 = min(img.height, y1)

                if x1 <= x0 or y1 <= y0: # Invalid crop area, fall back to generic
                    num = detect_page_number_tesseract(img, max_pages=num_pages)
                else:
                    cropped_img_for_layout = img.crop((x0, y0, x1, y1))
                    num = detect_page_number_tesseract(
                        cropped_img_for_layout, already_cropped=True, max_pages=num_pages
                    )

            if num is None:
                num = detect_page_number_tesseract(img, max_pages=num_pages)
            
            if num is None and ollama_config:
                img_file = tmp_path / f"p{i}.png"
                img.save(img_file)
                num = extract_page_number(image_path=img_file, config=ollama_config)
            if num is None:
                num_undetected_pages += 1

            page_map.append((num if num is not None else float("inf"), i))

    doc.close()

    # Fill "holes" (inf values) to avoid reordering pages where detection failed
    # but the page is likely in the correct spot.
    final_nums = [p[0] for p in page_map]
    i = 0
    while i < num_pages:
        if final_nums[i] == float("inf"):
            start_idx = i
            while i < num_pages and final_nums[i] == float("inf"):
                i += 1
            end_idx = i - 1
            
            prev_val = final_nums[start_idx - 1] if start_idx > 0 else 0
            next_val = final_nums[i] if i < num_pages else float("inf")
            
            if next_val != float("inf"):
                # Linear interpolation for the gap
                step = (next_val - prev_val) / (end_idx - start_idx + 2)
                for j in range(start_idx, end_idx + 1):
                    final_nums[j] = prev_val + step * (j - start_idx + 1)
            else:
                # No upper bound, just increment
                for j in range(start_idx, end_idx + 1):
                    final_nums[j] = prev_val + (j - start_idx + 1)
        else:
            i += 1

    page_map = [(final_nums[idx], idx) for idx in range(num_pages)]

    # Sort by detected page number, keeping original order for ties/failures
    sorted_map = sorted(page_map, key=lambda x: x[0])

    reorder_warnings: list[str] = []

    if num_undetected_pages > 0:
        reorder_warnings.append(f"Page numbers could not be detected for {num_undetected_pages} out of {num_pages} pages.")

    # Calculate how many pages were actually moved from their original position
    original_indices = list(range(num_pages))
    current_indices_in_new_order = [m[1] for m in sorted_map]
    num_pages_moved = sum(1 for i, (orig_idx, curr_idx) in enumerate(zip(original_indices, current_indices_in_new_order)) if orig_idx != curr_idx)

    if num_pages_moved > 0 and num_pages_moved / num_pages > 0.15: # If more than 15% of pages were moved
        reorder_warnings.append(f"Significant reordering detected: {num_pages_moved} out of {num_pages} pages were moved.")

    # Check for large jumps in the final sequence (after interpolation)
    for i in range(num_pages - 1):
        if final_nums[i] != float("inf") and final_nums[i+1] != float("inf"):
            if abs(final_nums[i+1] - final_nums[i]) > 2: # A jump of more than 2 pages is suspicious
                reorder_warnings.append(f"Large page number jump detected: page {i+1} (detected {final_nums[i]}) followed by page {i+2} (detected {final_nums[i+1]}).")
                break # Only report the first large jump

    if reorder_warnings:
        print(f"Warning: Reordering of {path.name} completed with concerns:", file=sys.stderr)
        for warning in reorder_warnings:
            print(f"  - {warning}", file=sys.stderr)

    # Sort by detected page number, keeping original order for ties/failures
    if [m[1] for m in sorted_map] == list(range(len(reader.pages))):
        return False

    writer = PdfWriter()
    for _, original_index in sorted_map:
        writer.add_page(reader.pages[original_index])

    with open(path, "wb") as f:
        writer.write(f)
    return True