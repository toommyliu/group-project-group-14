from pathlib import Path

import fitz
import pytest

from opengrader.core.pdf_utils import (
    HIGHLIGHT_RGB,
    MAX_RENDER_PIXELS,
    PdfRenderLimitError,
    render_page_to_image,
    render_page_with_highlights,
)
from opengrader.core.render_cache import (
    RenderedPage,
    clear_render_cache,
    configure_render_cache,
    document_fingerprint,
    render_cache_get,
    render_cache_max_bytes,
    render_cache_stats,
)

_ORIGINAL_MAX_BYTES = render_cache_max_bytes()


@pytest.fixture(autouse=True)
def _reset_render_cache():
    """Isolate every test in this module from the process-wide render cache.

    ``render_page_to_image`` reads and writes a module-level cache shared
    across the whole test session (see ``opengrader.core.render_cache``), so
    a test that renders a page must not leave entries or a resized budget
    behind for a later test — in this file or elsewhere.
    """
    clear_render_cache()
    configure_render_cache(max_bytes=_ORIGINAL_MAX_BYTES)
    try:
        yield
    finally:
        clear_render_cache()
        configure_render_cache(max_bytes=_ORIGINAL_MAX_BYTES)


def test_render_normal_page_succeeds() -> None:
    doc = fitz.open()
    doc.new_page(width=612, height=792)  # US Letter, points
    try:
        img, w, h = render_page_to_image(doc, 0, dpi=150)
        assert w > 0 and h > 0
        assert img.size == (w, h)
    finally:
        doc.close()


def test_render_rejects_oversized_page() -> None:
    """A page whose geometry would rasterize to billions of pixels is refused."""
    doc = fitz.open()
    # 14000 pt square at 300 DPI ≈ 58000×58000 px (~3.4 billion pixels), well
    # over the safety ceiling — a decompression-bomb-style malformed PDF.
    doc.new_page(width=14000, height=14000)
    try:
        with pytest.raises(PdfRenderLimitError):
            render_page_to_image(doc, 0, dpi=300)
    finally:
        doc.close()


def test_render_limit_is_a_valueerror() -> None:
    # ValueError subclass so existing bad-input handling around PDF parsing
    # keeps working without a new except clause.
    assert issubclass(PdfRenderLimitError, ValueError)
    assert MAX_RENDER_PIXELS > 0


def test_oversized_page_guard_fires_before_any_cache_interaction(
    tmp_path: Path,
) -> None:
    """The MAX_RENDER_PIXELS guard must raise before touching the cache.

    ``get_pixmap`` allocates the full bitmap up front, so the guard has to
    run ahead of the cache lookup too — an oversized page must raise
    identically whether or not some earlier call already cached this file at
    a smaller DPI, and it must never be able to populate an entry itself.
    """
    doc = fitz.open()
    doc.new_page(width=14000, height=14000)
    path = tmp_path / "huge.pdf"
    doc.save(str(path))
    doc.close()

    doc = fitz.open(str(path))
    try:
        before = render_cache_stats()
        with pytest.raises(PdfRenderLimitError):
            render_page_to_image(doc, 0, dpi=300)
        after = render_cache_stats()
        assert after.hits == before.hits
        assert after.misses == before.misses
        assert after.entries == before.entries
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# render_page_to_image <-> the render cache seam
# --------------------------------------------------------------------------- #


def _saved_two_page_pdf(tmp_path: Path) -> Path:
    doc = fitz.open()
    for _ in range(2):
        doc.new_page(width=200, height=300)
    path = tmp_path / "cached.pdf"
    doc.save(str(path))
    doc.close()
    return path


def test_repeated_render_is_pixel_identical_but_a_distinct_object_and_a_hit(
    tmp_path: Path,
) -> None:
    path = _saved_two_page_pdf(tmp_path)
    doc = fitz.open(str(path))
    try:
        before = render_cache_stats()
        img1, w1, h1 = render_page_to_image(doc, 0, dpi=100)
        mid = render_cache_stats()
        img2, w2, h2 = render_page_to_image(doc, 0, dpi=100)
        after = render_cache_stats()

        assert (w1, h1) == (w2, h2)
        assert img1 is not img2
        assert list(img1.getdata()) == list(img2.getdata())

        # First render: a miss that populates one entry.
        assert mid.misses == before.misses + 1
        assert mid.entries == before.entries + 1
        # Second render of the same (doc, page, dpi): a hit, no new entry.
        assert after.hits == mid.hits + 1
        assert after.misses == mid.misses
        assert after.entries == mid.entries

        # Mutating the first image must never corrupt what a later cache hit
        # returns (the cache stores packed samples, never the Image itself).
        img1.putpixel((0, 0), (0, 0, 0))
        img3, _, _ = render_page_to_image(doc, 0, dpi=100)
        assert img3.getpixel((0, 0)) == img2.getpixel((0, 0))
    finally:
        doc.close()


def test_different_dpi_values_are_different_cache_entries(tmp_path: Path) -> None:
    path = _saved_two_page_pdf(tmp_path)
    doc = fitz.open(str(path))
    try:
        before = render_cache_stats()
        render_page_to_image(doc, 0, dpi=100)
        render_page_to_image(doc, 0, dpi=150)
        after = render_cache_stats()

        assert after.misses == before.misses + 2
        assert after.hits == before.hits
        assert after.entries == before.entries + 2
    finally:
        doc.close()


def test_different_pages_are_different_cache_entries(tmp_path: Path) -> None:
    path = _saved_two_page_pdf(tmp_path)
    doc = fitz.open(str(path))
    try:
        before = render_cache_stats()
        render_page_to_image(doc, 0, dpi=100)
        render_page_to_image(doc, 1, dpi=100)
        after = render_cache_stats()

        assert after.misses == before.misses + 2
        assert after.entries == before.entries + 2
    finally:
        doc.close()


def test_cache_false_neither_reads_nor_writes_the_cache(tmp_path: Path) -> None:
    path = _saved_two_page_pdf(tmp_path)
    doc = fitz.open(str(path))
    try:
        before = render_cache_stats()
        render_page_to_image(doc, 0, dpi=100, cache=False)
        render_page_to_image(doc, 0, dpi=100, cache=False)
        after = render_cache_stats()

        assert after.hits == before.hits
        assert after.misses == before.misses
        assert after.entries == before.entries
        assert after.bytes_used == before.bytes_used

        # And an explicit cache=False render must not even see an entry that
        # a normal (cached) render for the same key already populated.
        render_page_to_image(doc, 1, dpi=100)  # cache=True (default) -> populates
        populated = render_cache_stats()
        render_page_to_image(doc, 1, dpi=100, cache=False)
        after_uncached = render_cache_stats()
        assert after_uncached.hits == populated.hits
        assert after_uncached.misses == populated.misses
    finally:
        doc.close()


def test_cache_disabled_via_zero_budget_falls_back_to_uncached_render(
    tmp_path: Path,
) -> None:
    path = _saved_two_page_pdf(tmp_path)
    doc = fitz.open(str(path))
    try:
        configure_render_cache(max_bytes=0)
        before = render_cache_stats()
        img, w, h = render_page_to_image(doc, 0, dpi=100)
        after = render_cache_stats()
        assert img.mode == "RGB"
        assert (w, h) == img.size
        assert after.hits == before.hits
        assert after.misses == before.misses
        assert after.entries == 0
    finally:
        doc.close()


def test_uncachable_stream_document_still_renders_correctly(tmp_path: Path) -> None:
    """A document with no file behind it renders fine but is never cached."""
    doc = fitz.open()
    doc.new_page(width=200, height=300)
    raw = doc.tobytes()
    doc.close()

    stream_doc = fitz.open(stream=raw, filetype="pdf")
    try:
        assert document_fingerprint(stream_doc) is None
        before = render_cache_stats()
        render_page_to_image(stream_doc, 0, dpi=100)
        after = render_cache_stats()
        assert after.entries == before.entries
        assert after.hits == before.hits
        assert after.misses == before.misses
    finally:
        stream_doc.close()


def test_rendered_image_matches_old_png_round_trip_mode_and_size(
    tmp_path: Path,
) -> None:
    """The direct-from-pixmap path must match what ``pix.tobytes("png")`` gave.

    ``render_page_to_image`` now builds the PIL image straight from the
    pixmap's packed samples instead of round-tripping through a PNG encode;
    this pins the externally-visible contract (mode, size, and pixel values)
    against the old approach so that optimization can never silently change
    what callers see.
    """
    path = _saved_two_page_pdf(tmp_path)
    doc = fitz.open(str(path))
    try:
        img, w, h = render_page_to_image(doc, 0, dpi=100, cache=False)
        assert img.mode == "RGB"
        assert img.size == (w, h)

        # Reference: the old approach, rendered independently for comparison.
        page = doc[0]
        zoom = 100 / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        from io import BytesIO

        from PIL import Image as PILImage

        reference = PILImage.open(BytesIO(pix.tobytes("png"))).convert("RGB")

        assert img.size == reference.size
        assert img.mode == reference.mode
        assert list(img.getdata()) == list(reference.getdata())
    finally:
        doc.close()


def test_render_cache_put_and_get_round_trip_via_public_api(tmp_path: Path) -> None:
    """Exercise the render_cache_put/get/RenderedPage seam pdf_utils relies on."""
    path = _saved_two_page_pdf(tmp_path)
    doc = fitz.open(str(path))
    try:
        fingerprint = document_fingerprint(doc)
        assert fingerprint is not None
        key = (fingerprint, 0, 100)
        assert render_cache_get(key) is None

        img, w, h = render_page_to_image(doc, 0, dpi=100)
        cached = render_cache_get(key)
        assert cached is not None
        assert isinstance(cached, RenderedPage)
        assert (cached.width, cached.height) == (w, h)
        assert cached.to_image().size == img.size
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# render_page_with_highlights (the grade screen's whole-page view)
# --------------------------------------------------------------------------- #


def _two_page_pdf(tmp_path: Path) -> Path:
    doc = fitz.open()
    for _ in range(2):
        doc.new_page(width=612, height=792)
    path = tmp_path / "scan.pdf"
    doc.save(str(path))
    doc.close()
    return path


def test_renders_the_requested_page_without_highlights(tmp_path: Path) -> None:
    image = render_page_with_highlights(str(_two_page_pdf(tmp_path)), 2, dpi=72)
    assert image.mode == "RGB"
    assert image.size == (612, 792)


def test_highlight_tints_the_region_and_leaves_the_rest_alone(tmp_path: Path) -> None:
    pdf = _two_page_pdf(tmp_path)
    plain = render_page_with_highlights(str(pdf), 1, dpi=72)
    marked = render_page_with_highlights(
        str(pdf), 1, [(0.25, 0.25, 0.5, 0.5)], dpi=72
    )
    # Inside the box the blank page is tinted toward the highlight colour...
    inside = marked.getpixel((306, 396))
    assert inside != plain.getpixel((306, 396))
    assert inside[0] > inside[2]  # warmer: more red than blue
    # ...and outside it the page is untouched, so nothing is obscured.
    assert marked.getpixel((10, 10)) == plain.getpixel((10, 10))


def test_outline_is_drawn_in_the_highlight_colour(tmp_path: Path) -> None:
    marked = render_page_with_highlights(
        str(_two_page_pdf(tmp_path)), 1, [(0.25, 0.25, 0.5, 0.5)], dpi=72
    )
    # The top edge of the box sits at y = 0.25 * 792 = 198.
    edge = marked.getpixel((306, 198))
    assert max(abs(a - b) for a, b in zip(edge, HIGHLIGHT_RGB)) <= 2


def test_page_number_outside_the_document_raises(tmp_path: Path) -> None:
    pdf = _two_page_pdf(tmp_path)
    with pytest.raises(ValueError):
        render_page_with_highlights(str(pdf), 3, dpi=72)
    with pytest.raises(ValueError):
        render_page_with_highlights(str(pdf), 0, dpi=72)
