"""Optical mark recognition — read a generated bubble sheet back off a scan.

The read half of the OMR path; the geometry it reads *against* comes entirely
from `core/bubble_sheet.py`, which also drew the sheet. Kept in ``ocr/`` for the
same reason as `qr_reader.py`: this is where the cv2 dependency lives, leaving
`core/bubble_sheet` a pure, dependency-free leaf.

Why this is a measurement and not a recognition problem: we generated the
sheet, so we know where every circle is to the pixel. The only real work is
undoing whatever the scanner did to the page — shift, scale, rotation, the
keystone of a phone photo — and that is what the four corner registration
squares are for. Locate them, solve the perspective transform they imply, and
every bubble center follows exactly.

Two rules the module holds to, both borrowed from the scan pre-flight:

* **Fail honestly.** If the registration marks cannot be found, the page is
  *refused* (``PageReadResult.ok is False``) rather than read against an
  assumed alignment. A silently mis-registered sheet would score a whole
  student's exam against the wrong bubbles, which is far worse than saying "I
  could not read this page".
* **Thresholds are measured, not guessed.** ``EMPTY_BUBBLE_FILL_CEILING`` and
  the sampling radius below carry the numbers they were calibrated from.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np
from PIL import Image

from opengrader.core.bubble_sheet import (
    Bubble,
    BubbleReading,
    BubbleSheetLayout,
    Fiducial,
    MarkConfig,
    decide_question_mark,
)

# The printed outline is ink on *every* bubble, marked or not, so sampling the
# full circle would give an empty bubble a floor of roughly 25% fill at 200 DPI
# and leave almost no room between "empty" and "filled". Sampling only the
# inner disc excludes the ring entirely. 0.72 was chosen as the largest scale
# that still clears the outline plus antialiasing at 150-300 DPI while keeping
# most of the area a student actually fills.
SAMPLE_RADIUS_SCALE = 0.72

# Measured across rendered sheets at 100-300 DPI, clean and with a simulated
# scan (grey cast, gaussian noise, 1-12 degrees of skew, phone-photo keystone):
# an unmarked bubble samples 0.000-0.078 — only the faint grey choice letter
# falls inside the sampled disc — while a filled one samples 0.87-1.00, the low
# end being a light, slightly-off-center pencil mark. The decision threshold
# (`MarkConfig.min_fill`, 0.35) therefore sits ~4.5x above the worst empty
# reading and ~2.5x below the worst filled one, which is the margin that lets a
# scanner's cast or a sloppy mark move the numbers without changing an answer.
EMPTY_BUBBLE_FILL_CEILING = 0.08

DEFAULT_READ_DPI = 200

# Registration marks are found by searching the *whole* page for square blobs
# and then assigning the four extreme ones to corners, rather than by looking
# in fixed windows around where the layout says they should be. Fixed windows
# were the first design and they were wrong: every scanner leaves a margin, so
# the sheet is never exactly where the layout puts it, and a window generous
# enough to absorb that plus skew is generous enough to catch the wrong blob.
# Searching globally and validating the *shape* the four marks form is both
# more tolerant and harder to fool.
#
# A candidate must be roughly square and plausibly sized. The size band is wide
# because it is expressed against the image width and the sheet may fill
# anywhere from about half the frame (a photo with margin) to all of it.
FIDUCIAL_MIN_SIDE_FRACTION = 0.008
FIDUCIAL_MAX_SIDE_FRACTION = 0.060
FIDUCIAL_MAX_ASPECT = 1.9
FIDUCIAL_MIN_SOLIDITY = 0.60

# Validation on the quadrilateral the four chosen marks form. Opposite sides of
# the real rectangle stay close even under a steep photo angle, and the quad
# always covers most of the frame — a set of four text blobs will fail both.
FIDUCIAL_MAX_SIDE_IMBALANCE = 0.45
FIDUCIAL_MIN_AREA_FRACTION = 0.18


@dataclass(frozen=True)
class PageReadResult:
    """The outcome of reading one page of one student's sheet."""

    page: int
    ok: bool
    readings: tuple[BubbleReading, ...] = ()
    registered: bool = False
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


@dataclass(frozen=True)
class SheetReadResult:
    """Every page of one submission, plus the reason any page was refused."""

    pages: tuple[PageReadResult, ...] = ()
    readings: tuple[BubbleReading, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.pages) and all(page.ok for page in self.pages)

    @property
    def failures(self) -> tuple[str, ...]:
        return tuple(
            f"page {page.page}: {page.reason}" for page in self.pages if not page.ok
        )

    def by_question(self) -> dict[str, BubbleReading]:
        return {reading.question_id: reading for reading in self.readings}


def _to_gray(image: Image.Image) -> np.ndarray:
    """Grayscale numpy view of a PIL image, without touching the caller's copy."""
    if image.mode != "L":
        image = image.convert("L")
    return np.asarray(image, dtype=np.uint8)


def _binarize(gray: np.ndarray) -> np.ndarray:
    """Ink mask (1 = ink) via Otsu.

    Otsu picks the threshold from the page's own histogram, which is what makes
    the same code work on a crisp born-digital render and on a grey photocopy
    without a hand-tuned cut per scanner.
    """
    _threshold, binary = cv2.threshold(
        gray, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    return binary.astype(np.uint8)


def find_fiducial_candidates(binary: np.ndarray) -> list[tuple[float, float, float]]:
    """Every square, solid, plausibly-sized blob on the page.

    Returns ``(cx, cy, side)`` in pixels. A 3×3 opening runs first so scanner
    speckle cannot masquerade as a tiny mark; the real squares are ~40px across
    at 200 DPI and lose nothing to it.
    """
    height, width = binary.shape[:2]
    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _hierarchy = cv2.findContours(
        opened, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    min_side = FIDUCIAL_MIN_SIDE_FRACTION * width
    max_side = FIDUCIAL_MAX_SIDE_FRACTION * width

    candidates: list[tuple[float, float, float]] = []
    for contour in contours:
        bx, by, bw, bh = cv2.boundingRect(contour)
        if bw <= 0 or bh <= 0:
            continue
        if not (min_side <= bw <= max_side and min_side <= bh <= max_side):
            continue
        if max(bw / bh, bh / bw) > FIDUCIAL_MAX_ASPECT:
            continue
        area = float(cv2.contourArea(contour))
        # A registration mark is solid; a letter, a circle outline or a stray
        # stroke fills far less of its own bounding box.
        if area <= 0 or area / float(bw * bh) < FIDUCIAL_MIN_SOLIDITY:
            continue
        candidates.append((bx + bw / 2.0, by + bh / 2.0, (bw + bh) / 2.0))
    return candidates


def _quad_is_plausible(
    corners: dict[str, tuple[float, float]], width: int, height: int
) -> bool:
    """Sanity-check the rectangle four candidate marks claim to form.

    This is what stops the reader from locking onto four unrelated blobs on a
    page that is not a bubble sheet at all. Two independent checks: opposite
    sides of a real (even steeply photographed) rectangle stay close in length,
    and the marks always enclose most of the frame.
    """
    tl, tr, bl, br = (corners["tl"], corners["tr"], corners["bl"], corners["br"])

    def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
        return float(np.hypot(a[0] - b[0], a[1] - b[1]))

    top, bottom = distance(tl, tr), distance(bl, br)
    left, right = distance(tl, bl), distance(tr, br)
    if min(top, bottom, left, right) <= 0:
        return False
    for near, far in ((top, bottom), (left, right)):
        if abs(near - far) / max(near, far) > FIDUCIAL_MAX_SIDE_IMBALANCE:
            return False

    # Shoelace area of the quad, walked in order.
    ordered = [tl, tr, br, bl]
    area = 0.0
    for index in range(4):
        x0, y0 = ordered[index]
        x1, y1 = ordered[(index + 1) % 4]
        area += x0 * y1 - x1 * y0
    return abs(area) / 2.0 >= FIDUCIAL_MIN_AREA_FRACTION * width * height


def select_corner_marks(
    candidates: Sequence[tuple[float, float, float]],
    width: int,
    height: int,
) -> dict[str, tuple[float, float]] | None:
    """Assign four candidate blobs to page corners, or ``None``.

    The registration marks are by construction the outermost ink on the sheet,
    so each is the candidate that minimizes ``x + y`` (top-left), ``y - x``
    (top-right) and so on. Using those sums rather than distance-to-image-corner
    keeps the choice correct when the sheet sits off-center in the frame.
    """
    if len(candidates) < 4:
        return None

    corners = {
        "tl": min(candidates, key=lambda c: c[0] + c[1]),
        "tr": min(candidates, key=lambda c: -c[0] + c[1]),
        "bl": min(candidates, key=lambda c: c[0] - c[1]),
        "br": min(candidates, key=lambda c: -c[0] - c[1]),
    }
    centers = {name: (blob[0], blob[1]) for name, blob in corners.items()}
    if len({(round(x), round(y)) for x, y in centers.values()}) < 4:
        # The same blob won more than one corner: not four marks.
        return None
    if not _quad_is_plausible(centers, width, height):
        return None
    return centers


def build_page_transform(
    binary: np.ndarray,
    layout: BubbleSheetLayout,
    page: int,
) -> np.ndarray | None:
    """Solve the layout→pixel mapping from this page's registration marks.

    Returns a 3×3 perspective matrix, or ``None`` when the four marks could not
    be found and validated — the caller must then refuse the page rather than
    fall back to an assumed alignment, which would score the student against
    the wrong bubbles without ever saying so.
    """
    height, width = binary.shape[:2]
    marks = {mark.corner: mark for mark in layout.fiducials_on_page(page)}
    if len(marks) != 4:
        return None

    found = select_corner_marks(find_fiducial_candidates(binary), width, height)
    if found is None:
        return None

    # Source points are where the layout says the marks are, expressed in the
    # image's own pixel grid; destination points are where they actually are.
    order = ("tl", "tr", "br", "bl")
    source = np.array(
        [(marks[corner].cx * width, marks[corner].cy * height) for corner in order],
        dtype=np.float32,
    )
    destination = np.array([found[corner] for corner in order], dtype=np.float32)
    return cv2.getPerspectiveTransform(source, destination)


def _project(transform: np.ndarray, x: float, y: float) -> tuple[float, float]:
    point = np.array([x, y, 1.0], dtype=np.float64)
    mapped = transform @ point
    if abs(mapped[2]) < 1e-9:
        return x, y
    return float(mapped[0] / mapped[2]), float(mapped[1] / mapped[2])


def measure_bubble_fill(
    binary: np.ndarray,
    bubble: Bubble,
    transform: np.ndarray,
    width: int,
    height: int,
) -> float:
    """Fraction of one bubble's inner disc that is ink (0-1).

    The disc is sampled in *pixel* space after projection, so a rotated or
    keystoned page is measured on the actual ellipse the circle became rather
    than on an axis-aligned box that would clip it.
    """
    cx, cy = _project(transform, bubble.cx * width, bubble.cy * height)
    # Project a point one radius to the right to get the radius in pixels
    # *after* whatever scaling the transform applies.
    edge_x, edge_y = _project(
        transform, (bubble.cx + bubble.radius) * width, bubble.cy * height
    )
    radius = float(np.hypot(edge_x - cx, edge_y - cy)) * SAMPLE_RADIUS_SCALE
    if radius < 1.0:
        return 0.0

    x0 = max(0, int(np.floor(cx - radius)))
    x1 = min(width, int(np.ceil(cx + radius)) + 1)
    y0 = max(0, int(np.floor(cy - radius)))
    y1 = min(height, int(np.ceil(cy + radius)) + 1)
    if x1 <= x0 or y1 <= y0:
        return 0.0

    patch = binary[y0:y1, x0:x1]
    ys, xs = np.ogrid[y0:y1, x0:x1]
    mask = (xs - cx) ** 2 + (ys - cy) ** 2 <= radius * radius
    total = int(mask.sum())
    if total == 0:
        return 0.0
    return float(patch[mask].sum()) / float(total)


def read_page_image(
    image: Image.Image,
    layout: BubbleSheetLayout,
    page: int,
    *,
    mark_config: MarkConfig | None = None,
) -> PageReadResult:
    """Read every question on one rendered page of a sheet."""
    rows = layout.rows_on_page(page)
    if not rows:
        # A page with no questions on it (a cover or a stray sheet) is not a
        # failure — there is simply nothing to read.
        return PageReadResult(page=page, ok=True, readings=(), registered=False)

    gray = _to_gray(image)
    binary = _binarize(gray)
    height, width = binary.shape[:2]

    transform = build_page_transform(binary, layout, page)
    if transform is None:
        return PageReadResult(
            page=page,
            ok=False,
            reason=(
                "could not find the four corner registration squares — is this "
                "an Opengrader bubble sheet, and is the whole page in frame?"
            ),
        )

    readings: list[BubbleReading] = []
    for row in rows:
        fills = [
            (
                bubble.choice_label,
                measure_bubble_fill(binary, bubble, transform, width, height),
            )
            for bubble in row.bubbles
        ]
        readings.append(
            decide_question_mark(row.question_id, fills, config=mark_config)
        )

    return PageReadResult(
        page=page, ok=True, readings=tuple(readings), registered=True
    )


def read_sheet_images(
    images: Sequence[Image.Image],
    layout: BubbleSheetLayout,
    *,
    mark_config: MarkConfig | None = None,
) -> SheetReadResult:
    """Read a whole submission from its already-rendered page images."""
    pages: list[PageReadResult] = []
    readings: list[BubbleReading] = []
    for index, image in enumerate(images, start=1):
        if index > layout.page_count:
            # Extra sheets beyond the layout (scratch paper, a duplex back
            # side) carry no bubbles and are ignored rather than refused.
            break
        result = read_page_image(image, layout, index, mark_config=mark_config)
        pages.append(result)
        readings.extend(result.readings)
    return SheetReadResult(pages=tuple(pages), readings=tuple(readings))


def read_sheet_pdf(
    pdf_path: str | Path,
    layout: BubbleSheetLayout,
    *,
    dpi: int = DEFAULT_READ_DPI,
    mark_config: MarkConfig | None = None,
) -> SheetReadResult:
    """Render a submission PDF and read every bubble on it."""
    from opengrader.core.pdf_utils import render_pdf_page

    path = Path(pdf_path)
    images: list[Image.Image] = []
    for page_number in range(1, layout.page_count + 1):
        try:
            image, _width, _height = render_pdf_page(str(path), page_number, dpi=dpi)
        except ValueError:
            # Fewer pages than the layout expects: read what is there and let
            # the missing questions come back as unread, rather than failing
            # the whole submission over a page the scanner dropped.
            break
        images.append(image)
    if not images:
        return SheetReadResult(
            pages=(
                PageReadResult(
                    page=1, ok=False, reason=f"could not render any page of {path.name}"
                ),
            )
        )
    return read_sheet_images(images, layout, mark_config=mark_config)
