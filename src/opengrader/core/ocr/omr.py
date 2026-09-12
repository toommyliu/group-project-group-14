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

# Sample the inner disc to exclude printed outlines from fill measurements.
SAMPLE_RADIUS_SCALE = 0.72

# Unmarked fills measured 0.000-0.078 at 100-300 DPI.
EMPTY_BUBBLE_FILL_CEILING = 0.08

DEFAULT_READ_DPI = 200

FIDUCIAL_MIN_SIDE_FRACTION = 0.008
FIDUCIAL_MAX_SIDE_FRACTION = 0.060
FIDUCIAL_MAX_ASPECT = 1.9
FIDUCIAL_MIN_SOLIDITY = 0.60

FIDUCIAL_MAX_SIDE_IMBALANCE = 0.45
FIDUCIAL_MIN_AREA_FRACTION = 0.18


@dataclass(frozen=True)
class PageReadResult:
    page: int
    ok: bool
    readings: tuple[BubbleReading, ...] = ()
    registered: bool = False
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


@dataclass(frozen=True)
class SheetReadResult:
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
    if image.mode != "L":
        image = image.convert("L")
    return np.asarray(image, dtype=np.uint8)


def _binarize(gray: np.ndarray) -> np.ndarray:
    _threshold, binary = cv2.threshold(
        gray, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    return binary.astype(np.uint8)


def find_fiducial_candidates(binary: np.ndarray) -> list[tuple[float, float, float]]:
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
        if area <= 0 or area / float(bw * bh) < FIDUCIAL_MIN_SOLIDITY:
            continue
        candidates.append((bx + bw / 2.0, by + bh / 2.0, (bw + bh) / 2.0))
    return candidates


def _quad_is_plausible(
    corners: dict[str, tuple[float, float]], width: int, height: int
) -> bool:
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
        return None
    if not _quad_is_plausible(centers, width, height):
        return None
    return centers


def build_page_transform(
    binary: np.ndarray,
    layout: BubbleSheetLayout,
    page: int,
) -> np.ndarray | None:
    height, width = binary.shape[:2]
    marks = {mark.corner: mark for mark in layout.fiducials_on_page(page)}
    if len(marks) != 4:
        return None

    found = select_corner_marks(find_fiducial_candidates(binary), width, height)
    if found is None:
        return None

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
    cx, cy = _project(transform, bubble.cx * width, bubble.cy * height)
    # Transform a radius-offset point to account for image scaling.
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
    rows = layout.rows_on_page(page)
    if not rows:
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
    pages: list[PageReadResult] = []
    readings: list[BubbleReading] = []
    for index, image in enumerate(images, start=1):
        if index > layout.page_count:
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
    from opengrader.core.pdf_utils import render_pdf_page

    path = Path(pdf_path)
    images: list[Image.Image] = []
    for page_number in range(1, layout.page_count + 1):
        try:
            image, _width, _height = render_pdf_page(str(path), page_number, dpi=dpi)
        except ValueError:
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
