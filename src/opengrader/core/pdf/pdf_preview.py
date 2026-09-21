from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image as PILImage

if TYPE_CHECKING:
    pass


def pdf_page_to_image(
    pdf_path: Path | str,
    *,
    page: int = 0,
    dpi: int = 150,
) -> PILImage.Image:
    import fitz

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(pdf_path)
    try:
        if page < 0 or page >= len(doc):
            raise IndexError(
                f"page {page} out of range for {pdf_path} ({len(doc)} pages)"
            )
        pdf_page = doc[page]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pixmap = pdf_page.get_pixmap(matrix=mat, annots=True)
        return PILImage.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    finally:
        doc.close()


def get_pdf_page_count(pdf_path: Path | str) -> int:
    import fitz

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return 0
    doc = fitz.open(pdf_path)
    try:
        return len(doc)
    finally:
        doc.close()
