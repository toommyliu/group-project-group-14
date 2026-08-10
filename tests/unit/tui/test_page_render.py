from pathlib import Path

import fitz
import pytest
from PIL import Image

from opengrader.core.pdf_utils import REDACTION_RGB
from opengrader.core.submissions.page_context import PageContextEntry
from opengrader.tui.page_render import render_page_context_png


def _pdf(tmp_path: Path) -> Path:
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    path = tmp_path / "scan.pdf"
    doc.save(str(path))
    doc.close()
    return path


def _identified_pdf(tmp_path: Path) -> Path:
    """A scan whose first page carries a Name/Student ID header, as a real one does."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text(fitz.Point(72, 150), "Name: Ann Lee", fontsize=16)
    page.insert_text(fitz.Point(72, 180), "Student ID: 012345678", fontsize=16)
    page.insert_text(fitz.Point(72, 600), "continued on the back", fontsize=16)
    path = tmp_path / "identified.pdf"
    doc.save(str(path))
    doc.close()
    return path


def _ocr_available() -> bool:
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
    except Exception:
        return False
    return True


needs_ocr = pytest.mark.skipif(
    not _ocr_available(), reason="requires a Tesseract binary"
)


def _row_is_redacted(png: Path, y_fraction: float) -> bool:
    image = Image.open(png).convert("RGB")
    y = int(image.height * y_fraction)
    row = [image.getpixel((x, y)) for x in range(0, image.width, 17)]
    return all(pixel == REDACTION_RGB for pixel in row)


def test_renders_a_page_to_png(tmp_path):
    dest = tmp_path / "page.png"
    entry = PageContextEntry(page_number=1, kind="response", highlights=((0.1, 0.1, 0.5, 0.2),))
    assert render_page_context_png(_pdf(tmp_path), entry, dest, dpi=72).ok is True
    assert dest.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic number


def test_extra_page_renders_without_highlights(tmp_path):
    dest = tmp_path / "page.png"
    entry = PageContextEntry(page_number=1, kind="extra")
    assert render_page_context_png(_pdf(tmp_path), entry, dest, dpi=72).ok is True
    assert dest.exists()


def test_missing_pdf_fails_cleanly_without_writing(tmp_path):
    # A moved or deleted scan must leave the grader on the crop, not crash.
    dest = tmp_path / "page.png"
    entry = PageContextEntry(page_number=1, kind="response")
    assert render_page_context_png(tmp_path / "gone.pdf", entry, dest).ok is False
    assert not dest.exists()


def test_page_past_the_end_fails_cleanly(tmp_path):
    dest = tmp_path / "page.png"
    entry = PageContextEntry(page_number=9, kind="extra")
    assert render_page_context_png(_pdf(tmp_path), entry, dest, dpi=72).ok is False
    assert not dest.exists()


def test_no_pdf_path_is_a_clean_false(tmp_path):
    # Stub/demo submissions carry no source PDF at all.
    entry = PageContextEntry(page_number=1, kind="response")
    assert render_page_context_png("", entry, tmp_path / "page.png").ok is False


# --- anonymous grading ----------------------------------------------------- #
#
# The whole-page view stays available to every grader; what changes outside
# teacher mode is that the sheet's Name/Student-ID header never reaches the PNG.


@needs_ocr
def test_identity_header_is_painted_out_for_anonymous_grading(tmp_path):
    dest = tmp_path / "page.png"
    entry = PageContextEntry(page_number=1, kind="response")
    outcome = render_page_context_png(
        _identified_pdf(tmp_path), entry, dest, redact_identity=True
    )
    assert outcome.ok is True
    assert outcome.redacted is True
    # 150/792 and 180/792 are the two header rows; the work at 600/792 is not.
    assert _row_is_redacted(dest, 150 / 792) is True
    assert _row_is_redacted(dest, 180 / 792) is True
    assert _row_is_redacted(dest, 600 / 792) is False


@needs_ocr
def test_teacher_mode_renders_the_page_untouched(tmp_path):
    dest = tmp_path / "page.png"
    entry = PageContextEntry(page_number=1, kind="response")
    outcome = render_page_context_png(_identified_pdf(tmp_path), entry, dest)
    assert outcome.ok is True
    assert outcome.redacted is False
    assert _row_is_redacted(dest, 150 / 792) is False


@needs_ocr
def test_redaction_never_covers_the_answer_being_graded(tmp_path):
    # A crop region overlapping the header wins: hiding the answer under review
    # would defeat the reason the page view exists.
    dest = tmp_path / "page.png"
    entry = PageContextEntry(
        page_number=1, kind="response", highlights=((0.0, 0.15, 1.0, 0.5),)
    )
    outcome = render_page_context_png(
        _identified_pdf(tmp_path), entry, dest, redact_identity=True
    )
    assert outcome.ok is True
    assert _row_is_redacted(dest, 0.30) is False


def test_a_page_is_refused_when_the_header_cannot_be_located(tmp_path, monkeypatch):
    # No working OCR means no trustworthy redaction, so anonymous grading falls
    # back to refusing the page — the one case that needs teacher mode.
    import pytesseract

    def boom(*args, **kwargs):
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(pytesseract, "image_to_data", boom)
    dest = tmp_path / "page.png"
    entry = PageContextEntry(page_number=1, kind="response")
    outcome = render_page_context_png(
        _identified_pdf(tmp_path), entry, dest, dpi=72, redact_identity=True
    )
    assert outcome.ok is False
    assert not dest.exists()
    assert "teacher mode" in outcome.reason
