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

POINTS_SPEC = (
    r"\(\s*(\d+(?:\.\d+)?)\s*(?:bonus\s+)?"
    r"(?:points?|pts)(?:\s*\(\s*bonus\s*\))?\s*\)"
)

QUESTION_PREFIX_RE = re.compile(
    r"^\s*(\d+)\.\s*" + POINTS_SPEC + r"\s*(.*)$",
    re.IGNORECASE,
)

SCAN_QUESTION_LINE_RE = re.compile(
    r"^\s*(\d{1,2})\s*[\.)]\s*\(\s*(\d+(?:\.\d+)?)\s*(?:bonus\s+)?(?:points?|pts)\b",
    re.IGNORECASE,
)

SCAN_QUESTION_MAX_X_NORM = 0.30

MAX_RENDER_PIXELS = 300_000_000


class PdfRenderLimitError(ValueError):
    pass


@dataclass(frozen=True)
class QuestionLayoutInfo:
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
    questions: dict[int, QuestionLayoutInfo] = field(default_factory=dict)


@dataclass(frozen=True)
class ScanQuestionPosition:
    qnum: int
    page: int
    y_top_norm: float


def render_page_to_image(
    doc: fitz.Document, page_idx: int, dpi: int = 300, *, cache: bool = True
) -> tuple[Image.Image, int, int]:
    """Returns a fresh mutable image even on cache hits. cache=False bypasses reads and writes."""
    import fitz

    page = doc[page_idx]
    zoom = dpi / 72.0
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


HIGHLIGHT_RGB = (208, 135, 112)
HIGHLIGHT_FILL_ALPHA = 38

REDACTION_RGB = (22, 24, 30)


def render_pdf_page(
    pdf_path: str,
    page_number: int,
    *,
    dpi: int = 200,
) -> tuple[Image.Image, int, int]:
    """Page numbers are 1-based; returns (image, width, height) in pixels."""
    import fitz

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
    image, width, height = render_pdf_page(pdf_path, page_number, dpi=dpi)
    return draw_page_overlays(
        image, width, height, highlights=highlights, redactions=redactions
    )


def clamp_bbox_norm(bbox: Iterable[float]) -> tuple[float, float, float, float]:
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
    width, height = image.size
    x, y, w, h = bbox_norm
    x0 = max(0, int(round((x - pad_x) * width)))
    y0 = max(0, int(round((y - pad_y) * height)))
    x1 = min(width, int(round((x + w + pad_x) * width)))
    y1 = min(height, int(round((y + h + pad_y) * height)))
    if x1 <= x0 or y1 <= y0:
        return None
    return image.crop((x0, y0, x1, y1))


INK_LEVEL = 100

# Scanned handwriting needs a higher threshold than digital text.
SCANNED_INK_LEVEL = 180


def ink_fraction(image: Image.Image, *, level: int = INK_LEVEL) -> float | None:
    """None means measurement failed; 0.0 includes empty images."""
    try:
        histogram = image.convert("L").histogram()
    except Exception:
        return None
    total = sum(histogram)
    if total <= 0:
        return 0.0
    return sum(histogram[: max(0, min(len(histogram), level))]) / total


def calculate_page_offsets(deltas_by_page: dict[int, list[float]]) -> dict[int, float]:
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
