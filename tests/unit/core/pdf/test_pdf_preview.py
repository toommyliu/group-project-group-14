import pytest
from pathlib import Path
import fitz
from PIL import Image

from opengrader.core.pdf.pdf_preview import get_pdf_page_count, pdf_page_to_image

def create_dummy_pdf(path: Path, pages: int = 1):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((50, 50), f"Page {i+1}")
    doc.save(path)
    doc.close()

def test_get_pdf_page_count(tmp_path):
    pdf_path = tmp_path / "test.pdf"
    create_dummy_pdf(pdf_path, pages=3)

    assert get_pdf_page_count(pdf_path) == 3

def test_get_pdf_page_count_nonexistent():
    assert get_pdf_page_count("nonexistent.pdf") == 0

def test_pdf_page_to_image(tmp_path):
    pdf_path = tmp_path / "test.pdf"
    create_dummy_pdf(pdf_path, pages=1)

    img = pdf_page_to_image(pdf_path, page=0)
    assert isinstance(img, Image.Image)
    assert img.mode == "RGB"

def test_pdf_page_to_image_out_of_range(tmp_path):
    pdf_path = tmp_path / "test.pdf"
    create_dummy_pdf(pdf_path, pages=1)

    with pytest.raises(IndexError):
        pdf_page_to_image(pdf_path, page=1)

def test_pdf_page_to_image_nonexistent():
    with pytest.raises(FileNotFoundError):
        pdf_page_to_image("nonexistent.pdf")
