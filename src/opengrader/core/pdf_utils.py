from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable

from opengrader.core.render_cache import (
    RenderedPage,
    document_fingerprint,
    render_cache_get,
    render_cache_max_bytes,
    render_cache_put,
)

if TYPE_CHECKING:
    import fitz
    from PIL import Image

# Regex patterns for robust parsing of question numbers and point values.
#
# Points spec accepts the regular form ``(N points)`` plus the two bonus
# forms we render: ``(N bonus points)`` and ``(N points (bonus))``.
POINTS_SPEC = (
    r"\(\s*(\d+(?:\.\d+)?)\s*(?:bonus\s+)?"
    r"(?:points?|pts)(?:\s*\(\s*bonus\s*\))?\s*\)"
)

# Applied to fitz-extracted text from a PDF text layer (blank assignment
# or text-layer student PDF).
QUESTION_PREFIX_RE = re.compile(
    r"^\s*(\d+)\.\s*" + POINTS_SPEC + r"\s*(.*)$",
    re.IGNORECASE,
)

# Patterns applied to pytesseract OCR output from a rendered scan page —
# used purely for *alignment* (locating question prefixes on the paper).
SCAN_QUESTION_LINE_RE = re.compile(
    r"^\s*(\d{1,2})\s*[\.)]\s*\(\s*(\d+(?:\.\d+)?)\s*(?:bonus\s+)?(?:points?|pts)\b",
    re.IGNORECASE,
)

SCAN_QUESTION_MAX_X_NORM = 0.30

# Ceiling on the number of pixels a single page may rasterize to. PyMuPDF
# allocates the full bitmap for ``get_pixmap`` up front, so a malformed or
# malicious PDF that declares an enormous page geometry (e.g. a 20000×20000 pt
# MediaBox) would otherwise try to allocate tens of billions of pixels and
# exhaust memory before Pillow ever sees the data. Legitimate scans stay well
# under this: US-Letter at 600 DPI is ~34 MP, ISO A0 at 300 DPI is ~140 MP, so
# 300 MP leaves generous headroom while turning the bomb into a clean error.
MAX_RENDER_PIXELS = 300_000_000


class PdfRenderLimitError(ValueError):
    """A page would rasterize to an unreasonably large image.

    Raised instead of letting a malformed/malicious PDF exhaust memory during
    rasterization. A ``ValueError`` subclass so existing ``except ValueError``
    handling around PDF parsing treats it as the bad-input error it is.
    """


@dataclass(frozen=True)
class QuestionLayoutInfo:
    """Stores parsed question info from a blank assignment or text layer."""

    qnum: int
    page: int
    y_top_norm: float
    y_bottom_norm: float
    x_left_norm: float
    x_right_norm: float
    points: float
    prompt: str


@dataclass(frozen=True)
class BlankAssignmentTemplate:
    """Stores the overall structure extracted from a blank assignment PDF."""

    questions: dict[int, QuestionLayoutInfo] = field(default_factory=dict)


@dataclass(frozen=True)
class ScanQuestionPosition:
    """Stores a detected question position from a rendered scan page."""

    qnum: int
    page: int
    y_top_norm: float


def render_page_to_image(
    doc: fitz.Document, page_idx: int, dpi: int = 300, *, cache: bool = True
) -> tuple[Image.Image, int, int]:
    """
    Render one PDF page into a PIL image.

    This is the single rasterization seam for the whole pipeline, so it is also
    where the render cache lives: ``scan-check``, boundary detection, reorder,
    QR reading and the extractor all reach the same page through here, and by
    default the second and later callers get the first one's raster back
    instead of paying for it again (see :mod:`opengrader.core.render_cache`).
    Every caller still receives its own fresh, mutable image — the cache stores
    packed samples, never the ``Image`` object — so a stage that deskews or
    draws in place cannot disturb the next one. Pass ``cache=False`` for a
    one-shot render that should neither read nor populate the cache.

    The pixmap is handed to Pillow directly rather than round-tripped through
    ``pix.tobytes("png")``. That encode/decode was ~80% of this function's
    cost (191 ms → 37 ms per 150-DPI page on a real scan) and produced the same
    pixels, since ``alpha=False`` already gives packed 8-bit RGB.
    """
    # Import lazily: fitz (PyMuPDF, ~110ms cold) is only needed once a page is
    # actually rendered, and this module is imported by a lot of code (grade
    # screen, extraction, feedback PDFs) that only wants the cheap bbox/regex
    # helpers below. The caller already holds an open ``doc``, so this rarely
    # even re-imports — Python caches the module after the first render.
    import fitz

    page = doc[page_idx]
    zoom = dpi / 72.0
    # Guard BEFORE get_pixmap: PyMuPDF sizes the bitmap from the page rect × zoom
    # and allocates it immediately, so an oversized page must be rejected here,
    # not after the (potentially huge) allocation. It runs ahead of the cache
    # lookup too, so an oversized page raises identically whether or not some
    # earlier call happened to store it.
    rect = page.rect
    target_w = rect.width * zoom
    target_h = rect.height * zoom
    if target_w * target_h > MAX_RENDER_PIXELS:
        raise PdfRenderLimitError(
            f"page {page_idx} would render to {target_w:.0f}x{target_h:.0f} px "
            f"({target_w * target_h / 1e6:.0f} MP) at {dpi} DPI, exceeding the "
            f"{MAX_RENDER_PIXELS // 1_000_000} MP safety limit"
        )

    key: tuple[str, int, int] | None = None
    if cache and render_cache_max_bytes() > 0:
        fingerprint = document_fingerprint(doc)
        if fingerprint is not None:
            key = (fingerprint, page_idx, dpi)
            hit = render_cache_get(key)
            if hit is not None:
                return hit.to_image(), hit.width, hit.height

    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    rendered = RenderedPage.from_pixmap(pix)
    if key is not None:
        render_cache_put(key, rendered)
    return rendered.to_image(), rendered.width, rendered.height


# Colour of the "this is the region your crop came from" outline drawn over a
# full page. A warm orange (the app's Nord-ish accent palette) reads clearly
# over black handwriting on white paper without hiding either. The fill alpha is
# deliberately faint — it must tint the region, never wash out the ink under it.
HIGHLIGHT_RGB = (208, 135, 112)
HIGHLIGHT_FILL_ALPHA = 38

# Colour of an identity-redaction block (``core/submissions/identity_redaction``).
# Fully opaque and near-black: unlike the highlight, this must *destroy* what is
# under it, and it has to be unmistakably a deliberate bar rather than a smudge
# on the scan.
REDACTION_RGB = (22, 24, 30)


def render_pdf_page(
    pdf_path: str,
    page_number: int,
    *,
    dpi: int = 200,
) -> tuple[Image.Image, int, int]:
    """Render one **1-based** page of a PDF file to ``(image, width, height)``.

    The file-path counterpart of :func:`render_page_to_image`, which needs an
    already-open document. Split out so a caller can inspect the raw page (to
    locate an identity header, say) before anything is drawn on it.

    Raises ``ValueError`` for a page number outside the document (and
    ``PdfRenderLimitError`` via ``render_page_to_image`` for an oversized page).
    """
    import fitz  # see render_page_to_image for why this is deferred

    doc = fitz.open(str(pdf_path))
    try:
        page_index = page_number - 1
        if page_index < 0 or page_index >= len(doc):
            raise ValueError(
                f"page {page_number} is outside {pdf_path} (1-{len(doc)})"
            )
        return render_page_to_image(doc, page_index, dpi=dpi)
    finally:
        doc.close()


def draw_page_overlays(
    image: Image.Image,
    width: int,
    height: int,
    *,
    highlights: Iterable[tuple[float, float, float, float]] = (),
    redactions: Iterable[tuple[float, float, float, float]] = (),
) -> Image.Image:
    """Draw crop outlines and identity redactions onto a rendered page.

    Redactions are painted first and opaquely, so a highlight outline stays
    visible where the two meet and no blending can leave the covered pixels
    partly legible. Highlights are then composited from a transparent overlay,
    so their faint fill tints the page instead of washing out the ink under it
    (PIL's rectangle fill does no alpha blending of its own).
    """
    boxes = list(highlights)
    blocks = list(redactions)
    if not boxes and not blocks:
        return image.convert("RGB")

    from PIL import Image as PILImage, ImageDraw

    base = image.convert("RGBA")

    if blocks:
        block_draw = ImageDraw.Draw(base)
        for bbox in blocks:
            x0, y0, x1, y1 = bbox_norm_to_px(bbox, width, height)
            if x1 <= x0 or y1 <= y0:
                continue
            block_draw.rectangle(
                [x0, y0, x1 - 1, y1 - 1], fill=(*REDACTION_RGB, 255)
            )

    if not boxes:
        return base.convert("RGB")

    overlay = PILImage.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    # Scale the outline with the render so it stays visible at any DPI, but
    # never thinner than 2px (invisible) on a small page.
    line_width = max(2, round(min(width, height) / 300))
    for bbox in boxes:
        x0, y0, x1, y1 = bbox_norm_to_px(bbox, width, height)
        draw.rectangle(
            [x0, y0, x1 - 1, y1 - 1],
            fill=(*HIGHLIGHT_RGB, HIGHLIGHT_FILL_ALPHA),
            outline=(*HIGHLIGHT_RGB, 255),
            width=line_width,
        )
    return PILImage.alpha_composite(base, overlay).convert("RGB")


def render_page_with_highlights(
    pdf_path: str,
    page_number: int,
    highlights: Iterable[tuple[float, float, float, float]] = (),
    *,
    dpi: int = 200,
    redactions: Iterable[tuple[float, float, float, float]] = (),
) -> Image.Image:
    """Render one 1-based PDF page, outlining normalized bboxes on it.

    The whole-page counterpart to the response crops: same page, same
    coordinates, but with the crop's region marked instead of cut out, so a
    grader can see an answer *in context* (and read the "continued on back"
    the crop excluded). ``highlights`` are the normalized ``(x, y, w, h)``
    boxes of ``QuestionSourceSegment``; pass none for a plain page render.
    ``redactions`` are painted out opaquely (see :func:`draw_page_overlays`).

    Raises ``ValueError`` for a page number outside the document (and
    ``PdfRenderLimitError`` via ``render_page_to_image`` for an oversized page).
    """
    image, width, height = render_pdf_page(pdf_path, page_number, dpi=dpi)
    return draw_page_overlays(
        image, width, height, highlights=highlights, redactions=redactions
    )


def clamp_bbox_norm(bbox: Iterable[float]) -> tuple[float, float, float, float]:
    """Clamp normalized coordinates to the [0, 1] range."""
    x, y, w, h = bbox
    nx = max(0.0, min(1.0, x))
    ny = max(0.0, min(1.0, y))
    nw = max(0.0, min(1.0 - nx, w))
    nh = max(0.0, min(1.0 - ny, h))
    return (round(nx, 4), round(ny, 4), round(nw, 4), round(nh, 4))


def clamp_bbox_px(
    bbox_px: tuple[int, int, int, int],
    page_w: int,
    page_h: int,
) -> tuple[int, int, int, int]:
    """Clamp pixel coordinates to page dimensions."""
    x0, y0, x1, y1 = bbox_px
    x0 = max(0, min(page_w - 1, x0))
    y0 = max(0, min(page_h - 1, y0))
    x1 = max(1, min(page_w, x1))
    y1 = max(1, min(page_h, y1))
    if x1 <= x0:
        x1 = min(page_w, x0 + 1)
    if y1 <= y0:
        y1 = min(page_h, y0 + 1)
    return (x0, y0, x1, y1)


def norm_bbox(
    page_w: float,
    page_h: float,
    *,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> tuple[float, float, float, float]:
    """Normalize PDF-space coordinates to fractions of the page size.

    Returns (x, y, w, h) in normalized 0-1 coordinates.
    """
    pw, ph = max(page_w, 1.0), max(page_h, 1.0)
    nx0 = x0 / pw
    ny0 = y0 / ph
    nx1 = x1 / pw
    ny1 = y1 / ph
    return (
        round(nx0, 4),
        round(ny0, 4),
        round(max(0.0, nx1 - nx0), 4),
        round(max(0.0, ny1 - ny0), 4),
    )


def bbox_norm_to_px(
    bbox_norm: Iterable[float], page_w: int, page_h: int
) -> tuple[int, int, int, int]:
    """Convert normalized [x, y, w, h] to pixel [x0, y0, x1, y1]."""
    x, y, w, h = clamp_bbox_norm(bbox_norm)
    x0 = int(round(x * page_w))
    y0 = int(round(y * page_h))
    x1 = int(round((x + w) * page_w))
    y1 = int(round((y + h) * page_h))
    return clamp_bbox_px((x0, y0, x1, y1), page_w=page_w, page_h=page_h)


def crop_norm(
    image: Image.Image,
    bbox_norm: tuple[float, float, float, float],
    *,
    pad_x: float = 0.0,
    pad_y: float = 0.0,
) -> Image.Image | None:
    """Crop an image using normalized [x, y, w, h] coordinates with optional padding."""
    width, height = image.size
    x, y, w, h = bbox_norm
    x0 = max(0, int(round((x - pad_x) * width)))
    y0 = max(0, int(round((y - pad_y) * height)))
    x1 = min(width, int(round((x + w + pad_x) * width)))
    y1 = min(height, int(round((y + h + pad_y) * height)))
    if x1 <= x0 or y1 <= y0:
        return None
    return image.crop((x0, y0, x1, y1))


# Grey level below which a pixel counts as ink rather than paper. Suits
# crisply-rendered born-digital text, whose strokes really are near-black.
INK_LEVEL = 100

# The same cut for **scanned** paper, where ink is not black. A scanner's optics,
# the paper's own brightness and JPEG ringing lift a pen stroke into the middle
# greys, so the strict cut above misses it almost entirely: measured on the
# sample scans, a written page is only 0.00002-0.0007 dark-than-100 (blank paper
# reads 0.000000, so the two are indistinguishable) but a clean 0.006-0.014
# darker-than-180. Most starkly, a faint *pencil* name measures 0.000000 at 100
# and 0.0068 at 180 — invisible versus obvious. Any code deciding "is there
# anything on this scanned paper" wants this one.
SCANNED_INK_LEVEL = 180


def ink_fraction(image: Image.Image, *, level: int = INK_LEVEL) -> float | None:
    """Fraction of an image's pixels darker than ``level`` — i.e. ink, not paper.

    The one definition of "is there anything on this paper", shared by identity
    redaction (is this strip blank, or just unreadable?) and the scan pre-flight
    (is this whole page blank, is content running off the edge?) so the two can
    never disagree about how the measurement is taken.

    ``level`` is a knob because the right cut depends on what is being asked.
    Crisp born-digital text is genuinely near-black, but a *scanned* pen stroke
    lands around grey 150-180 — on real sample scans, only 0.07% of a written
    page is darker than the default 100, against 1.3% darker than 180. A caller
    deciding "is this whole sheet blank" therefore needs a far more generous cut
    than one deciding "is this strip too dark to be empty paper".

    Returns ``None`` only when the image cannot be measured at all — an empty
    image legitimately has *no* ink and returns ``0.0``, which is a different
    answer from "we couldn't tell".
    """
    try:
        histogram = image.convert("L").histogram()
    except Exception:
        return None
    total = sum(histogram)
    if total <= 0:
        return 0.0
    return sum(histogram[: max(0, min(len(histogram), level))]) / total


def calculate_page_offsets(deltas_by_page: dict[int, list[float]]) -> dict[int, float]:
    """Calculate the median vertical offset for each page from raw deltas."""
    offsets: dict[int, float] = {}
    for page, deltas in deltas_by_page.items():
        if not deltas:
            continue
        offsets[page] = statistics.median(deltas)
    return offsets


def safe_image_to_data(
    image: Image.Image,
    *,
    lang: str = "eng",
    config: str = "--psm 6",
) -> dict[str, list[Any]]:
    """Run Tesseract word extraction and return an empty result on failure."""

    try:
        from pytesseract import image_to_data, Output
        return image_to_data(
            image,
            lang=lang,
            config=config,
            output_type=Output.DICT,
        )
    except Exception:
        return {
            "text": [],
            "conf": [],
            "left": [],
            "top": [],
            "width": [],
            "height": [],
            "block_num": [],
            "par_num": [],
            "line_num": [],
        }