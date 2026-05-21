from __future__ import annotations

import io
import re
import statistics
from dataclasses import dataclass, field
import fitz
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from PIL import Image

# Regex patterns for robust parsing of question numbers and point values.
#
# Points spec accepts the regular form ``(N points)`` plus the two bonus
# forms we render: ``(N bonus points)`` and ``(N points (bonus))``.
POINTS_SPEC = (
    r"\(\s*(\d+(?:\.\d+)?)\s*(?:bonus\s+)?"
    r"(?:points?|pts)(?:\s*\(\s*bonus\s*\))?\s*\)"
)

# Applied to fitz-extracted text from a PDF text layer (blank exam
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


@dataclass(frozen=True)
class QuestionLayoutInfo:
    """Stores parsed question info from a blank exam or text layer."""

    qnum: int
    page: int
    y_top_norm: float
    y_bottom_norm: float
    x_left_norm: float
    x_right_norm: float
    points: float
    prompt: str


@dataclass(frozen=True)
class BlankExamTemplate:
    """Stores the overall structure extracted from a blank exam PDF."""

    questions: dict[int, QuestionLayoutInfo] = field(default_factory=dict)


@dataclass(frozen=True)
class ScanQuestionPosition:
    """Stores a detected question position from a rendered scan page."""

    qnum: int
    page: int
    y_top_norm: float


def render_page_to_image(
    doc: fitz.Document, page_idx: int, dpi: int = 300
) -> tuple[Image.Image, int, int]:
    """
    Render one PDF page into a PIL image.
    """
    page = doc[page_idx]
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    buf = pix.tobytes("png")
    from PIL import Image
    img = Image.open(io.BytesIO(buf))
    w, h = img.size
    return img, w, h


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