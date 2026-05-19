from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Callable

import fitz
import pytesseract
from PIL import Image
from pypdf import PdfReader, PdfWriter

from opengrader.core.ocr.extractor import render_page_to_image
from opengrader.core.ocr.ollama_vlm import OllamaVisionConfig, extract_page_number


def detect_page_number_tesseract(image: Image.Image) -> int | None:
    """Extract page number from the bottom of a page image using Tesseract."""
    w, h = image.size
    # Focus on the bottom 15% where page numbers usually reside
    bottom_crop = image.crop((0, int(h * 0.85), w, h))
    
    # PSM 6 (single block) is better for finding structured text lines like footers
    text = pytesseract.image_to_string(bottom_crop, config="--psm 6").strip()

    # 1. Look for explicit "Page X"
    page_match = re.search(r"page\s*(\d+)", text, re.IGNORECASE)
    if page_match:
        return int(page_match.group(1))

    # 2. Look for "X of Y" or "X / Y" (common in footers)
    of_match = re.search(r"(\d+)\s*(?:of|/)\s*\d+", text, re.IGNORECASE)
    if of_match:
        return int(of_match.group(1))
    
    # Look for digits in the footer text
    matches = re.findall(r"\d+", text)
    if matches:
        try:
            # Usually the page number is the first numeric token in the footer
            # (e.g. "1", "1 / 10").
            return int(matches[0])
        except ValueError:
            return None
    return None


def reorder_pdf_pages(
    pdf_path: str | Path,
    *,
    ollama_config: OllamaVisionConfig | None = None,
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
    page_map: list[tuple[int | float, int]] = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        for i in range(len(doc)):
            # Render at lower DPI for faster reordering check
            img, _, _ = render_page_to_image(doc, i, dpi=150)
            num = detect_page_number_tesseract(img)

            if num is None and ollama_config:
                img_file = tmp_path / f"p{i}.png"
                img.save(img_file)
                num = extract_page_number(image_path=img_file, config=ollama_config)

            page_map.append((num if num is not None else float("inf"), i))

    doc.close()

    # Sort by detected page number, keeping original order for ties/failures
    sorted_map = sorted(page_map, key=lambda x: x[0])
    if [m[1] for m in sorted_map] == list(range(len(reader.pages))):
        return False

    writer = PdfWriter()
    for _, original_index in sorted_map:
        writer.add_page(reader.pages[original_index])

    with open(path, "wb") as f:
        writer.write(f)
    return True