from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

import numpy as np
from PIL import Image, ImageOps

from opengrader.core.submissions.scan_check import DEFAULT_DPI_FLOOR
from opengrader.core.submissions.splitter import _sanitize_filename

if TYPE_CHECKING:  # pragma: no cover
    pass


PAPER_SIZES_IN: dict[str, tuple[float, float]] = {
    "letter": (8.5, 11.0),
    "a4": (8.27, 11.69),
}

DEFAULT_OUTPUT_DPI = DEFAULT_DPI_FLOOR

ASPECT_SNAP_TOLERANCE = 0.04

MIN_DOCUMENT_AREA_FRACTION = 0.30

QUAD_INSET_FRACTION = 0.01

CANNY_SIGMA = 0.33

EDGE_DILATE_ITERATIONS = 2

APPROX_POLY_EPSILON_FRACTION = 0.02

FIDUCIAL_BLUR_KERNEL = (5, 5)

LIGHTING_CLAHE_CLIP_LIMIT = 1.5
LIGHTING_CLAHE_TILE_GRID = (8, 8)

DESKEW_MAX_ANGLE = 15.0

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".heic", ".heif"})
_HEIC_SUFFIXES = frozenset({".heic", ".heif"})

PHOTOS_EXTRA = "uv sync --extra photos"

DEFAULT_SUBMISSION_PREFIX = "submission"
DEFAULT_ZERO_PAD_WIDTH = 4


class PhotoIntakeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImageOutcome:
    source: Path
    method: str
    note: str = ""


@dataclass(frozen=True)
class StudentSubmission:
    token: str | None
    output_pdf: Path
    pages: tuple[Path, ...]


@dataclass(frozen=True)
class PhotoIntakeReport:
    grouping_mode: str
    outcomes: tuple[ImageOutcome, ...]
    submissions: tuple[StudentSubmission, ...]
    output_dir: Path
    dpi: float


@dataclass(frozen=True)
class PhotoGroup:
    token: str | None
    images: tuple[Path, ...]


_NUMERIC_CHUNK_RE = re.compile(r"(\d+)")


def _natural_sort_key(name: str) -> tuple:
    parts = _NUMERIC_CHUNK_RE.split(name)
    return tuple(int(part) if part.isdigit() else part.lower() for part in parts)


def discover_images(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    files = [
        path
        for path in input_path.iterdir()
        if path.is_file()
        and path.suffix.lower() in IMAGE_SUFFIXES
        and not path.name.startswith(".")
    ]
    return sorted(files, key=lambda path: _natural_sort_key(path.name))


def discover_student_subdirectories(input_path: Path) -> list[Path] | None:
    if not input_path.is_dir():
        return None
    loose = [
        path
        for path in input_path.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    ]
    if loose:
        return None
    subdirs = sorted(
        (
            path
            for path in input_path.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ),
        key=lambda path: _natural_sort_key(path.name),
    )
    with_images = [d for d in subdirs if discover_images(d)]
    return with_images or None


_TOKEN_SEPARATOR_RE = re.compile(r"^([A-Za-z0-9]+)[-_]")


def _filename_token(path: Path) -> str | None:
    match = _TOKEN_SEPARATOR_RE.match(path.stem)
    return match.group(1) if match else None


def _group_by_filename_token(
    images: Sequence[Path],
) -> dict[str, list[Path]] | None:
    tokens = [_filename_token(path) for path in images]
    if not images or any(token is None for token in tokens):
        return None
    groups: dict[str, list[Path]] = {}
    for path, token in zip(images, tokens):
        groups.setdefault(token, []).append(path)
    return groups


def plan_groups(
    input_path: Path, *, pages_per_photo_set: int | None = None
) -> tuple[str, list[PhotoGroup]]:
    if input_path.is_file():
        return "single_file", [PhotoGroup(token=None, images=(input_path,))]

    if pages_per_photo_set:
        images = discover_images(input_path)
        if not images:
            raise PhotoIntakeError(f"no images found in {input_path}")
        groups = [
            PhotoGroup(token=None, images=tuple(images[start : start + pages_per_photo_set]))
            for start in range(0, len(images), pages_per_photo_set)
        ]
        return "pages_per_photo_set", groups

    subdirs = discover_student_subdirectories(input_path)
    if subdirs is not None:
        groups = [
            PhotoGroup(token=directory.name, images=tuple(discover_images(directory)))
            for directory in subdirs
        ]
        return "subdirectories", groups

    images = discover_images(input_path)
    if not images:
        raise PhotoIntakeError(f"no images found in {input_path}")

    token_groups = _group_by_filename_token(images)
    if token_groups is not None:
        order: list[str] = []
        for path in images:
            token = _filename_token(path)
            assert token is not None
            if token not in order:
                order.append(token)
        groups = [
            PhotoGroup(token=token, images=tuple(token_groups[token]))
            for token in order
        ]
        return "filename_token", groups

    return "ungrouped", [PhotoGroup(token=None, images=(path,)) for path in images]


def _output_filename(group: PhotoGroup, index: int) -> str:
    if group.token:
        return f"{_sanitize_filename(group.token)}.pdf"
    return f"{DEFAULT_SUBMISSION_PREFIX}-{index:0{DEFAULT_ZERO_PAD_WIDTH}d}.pdf"


_heif_registered = False


def _heif_available() -> bool:
    try:
        return importlib.util.find_spec("pillow_heif") is not None
    except (ImportError, ValueError):
        return False


def _register_heif_opener() -> None:
    global _heif_registered
    if _heif_registered:
        return
    import pillow_heif  # noqa: PLC0415

    pillow_heif.register_heif_opener()
    _heif_registered = True


def _require_heic_support(paths: Sequence[Path]) -> None:
    offending = [path for path in paths if path.suffix.lower() in _HEIC_SUFFIXES]
    if not offending:
        return
    if _heif_available():
        _register_heif_opener()
        return
    raise PhotoIntakeError(
        f"{offending[0].name} is a HEIC/HEIF photo, which Pillow cannot read "
        f"without the optional pillow-heif package. Install it with: {PHOTOS_EXTRA}"
    )


def load_oriented_image(path: Path) -> Image.Image:
    if path.suffix.lower() in _HEIC_SUFFIXES:
        _require_heic_support([path])
    with Image.open(path) as opened:
        opened.load()
        transposed = ImageOps.exif_transpose(opened)
    return transposed.convert("RGB")


def _fiducial_quad_in_region(gray_region: np.ndarray) -> np.ndarray | None:
    import cv2
    from opengrader.core.ocr import omr

    blurred = cv2.GaussianBlur(gray_region, FIDUCIAL_BLUR_KERNEL, 0)
    _threshold, binary = cv2.threshold(
        blurred, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    height, width = gray_region.shape[:2]
    candidates = omr.find_fiducial_candidates(binary.astype(np.uint8))
    corners = omr.select_corner_marks(candidates, width, height)
    if corners is None:
        return None
    return np.array(
        [corners["tl"], corners["tr"], corners["br"], corners["bl"]],
        dtype=np.float32,
    )


def _quad_bounds(
    quad: np.ndarray, shape: tuple[int, int], *, margin: float = 0.02
) -> tuple[int, int, int, int]:
    height, width = shape
    x0, y0 = quad.min(axis=0)
    x1, y1 = quad.max(axis=0)
    pad_x = (x1 - x0) * margin
    pad_y = (y1 - y0) * margin
    return (
        max(0, int(x0 - pad_x)),
        max(0, int(y0 - pad_y)),
        min(width, int(x1 + pad_x)),
        min(height, int(y1 + pad_y)),
    )


def _whiten_outside_quad(gray: np.ndarray, quad: np.ndarray) -> np.ndarray:
    import cv2

    mask = np.zeros(gray.shape[:2], dtype=np.uint8)
    cv2.fillConvexPoly(mask, quad.astype(np.int32), 255)
    return np.where(mask > 0, gray, 255).astype(np.uint8)


def _find_fiducial_quad(
    gray: np.ndarray, *, document_quad: np.ndarray | None = None
) -> np.ndarray | None:
    """Returns [top-left, top-right, bottom-right, bottom-left] in original image coordinates."""
    if document_quad is not None:
        x0, y0, x1, y1 = _quad_bounds(document_quad, gray.shape[:2])
        if x1 > x0 and y1 > y0:
            flattened = _whiten_outside_quad(gray, document_quad)
            found = _fiducial_quad_in_region(flattened[y0:y1, x0:x1])
            if found is not None:
                return found + np.array([x0, y0], dtype=np.float32)
    return _fiducial_quad_in_region(gray)


def _order_points(pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=np.float32)
    total = pts.sum(axis=1)
    diff = pts[:, 1] - pts[:, 0]
    ordered = np.zeros((4, 2), dtype=np.float32)
    ordered[0] = pts[np.argmin(total)]
    ordered[2] = pts[np.argmax(total)]
    ordered[1] = pts[np.argmin(diff)]
    ordered[3] = pts[np.argmax(diff)]
    return ordered


def _auto_canny(gray: np.ndarray, sigma: float = CANNY_SIGMA) -> np.ndarray:
    import cv2
    median = float(np.median(gray))
    lower = int(max(0, (1.0 - sigma) * median))
    upper = int(min(255, (1.0 + sigma) * median))
    return cv2.Canny(gray, lower, upper)


def _find_document_quad(gray: np.ndarray) -> np.ndarray | None:
    import cv2
    height, width = gray.shape[:2]
    frame_area = float(height * width)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = _auto_canny(blurred)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=EDGE_DILATE_ITERATIONS)

    contours, _hierarchy = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    best: np.ndarray | None = None
    best_area = MIN_DOCUMENT_AREA_FRACTION * frame_area
    for contour in contours:
        area = cv2.contourArea(contour)
        if area <= best_area:
            continue
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, APPROX_POLY_EPSILON_FRACTION * perimeter, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        best_area = area
        best = approx.reshape(4, 2).astype(np.float32)

    if best is None:
        return None
    return _order_points(best)


def _measure_output_size(
    quad: np.ndarray, *, paper_size_in: tuple[float, float], dpi_floor: float
) -> tuple[int, int, float]:
    tl, tr, br, bl = quad
    top = float(np.hypot(*(tr - tl)))
    bottom = float(np.hypot(*(br - bl)))
    left = float(np.hypot(*(bl - tl)))
    right = float(np.hypot(*(br - tr)))
    if min(top, bottom, left, right) <= 1.0:
        raise ValueError("degenerate page quad")

    measured_w = (top + bottom) / 2.0
    measured_h = (left + right) / 2.0

    paper_w_in, paper_h_in = paper_size_in
    target_aspect = paper_w_in / paper_h_in
    measured_aspect = measured_w / measured_h
    if abs(measured_aspect - target_aspect) / target_aspect <= ASPECT_SNAP_TOLERANCE:
        measured_h = measured_w / target_aspect

    dpi = measured_w / paper_w_in
    if dpi < dpi_floor:
        scale = dpi_floor / dpi
        measured_w *= scale
        measured_h *= scale
        dpi = dpi_floor

    return max(1, int(round(measured_w))), max(1, int(round(measured_h))), dpi


def _warp_quad(array: np.ndarray, quad: np.ndarray, out_w: int, out_h: int) -> np.ndarray:
    import cv2
    inset_quad = _inset_quad(quad, QUAD_INSET_FRACTION)
    destination = np.array(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(inset_quad.astype(np.float32), destination)
    return cv2.warpPerspective(
        array,
        transform,
        (out_w, out_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _inset_quad(quad: np.ndarray, fraction: float) -> np.ndarray:
    points = quad.astype(np.float64)
    centre = points.mean(axis=0)
    return centre + (points - centre) * (1.0 - fraction)


def _deskew(image: Image.Image) -> Image.Image:
    import cv2
    gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _threshold, binary = cv2.threshold(
        blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    coords = cv2.findNonZero(binary)
    if coords is None or len(coords) < 50:
        return image

    angle = cv2.minAreaRect(coords)[-1]
    # minAreaRect's width/height choice can shift its angle by 90 degrees.
    if angle < -45:
        angle = 90 + angle
    if abs(angle) < 0.3 or abs(angle) > DESKEW_MAX_ANGLE:
        return image

    return image.rotate(angle, resample=Image.BICUBIC, fillcolor=(255, 255, 255), expand=True)


def _normalize_lighting(image: Image.Image) -> Image.Image:
    import cv2
    array = np.asarray(image.convert("RGB"))
    lab = cv2.cvtColor(array, cv2.COLOR_RGB2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(
        clipLimit=LIGHTING_CLAHE_CLIP_LIMIT, tileGridSize=LIGHTING_CLAHE_TILE_GRID
    )
    l_channel = clahe.apply(l_channel)
    normalized = cv2.merge((l_channel, a_channel, b_channel))
    rgb = cv2.cvtColor(normalized, cv2.COLOR_LAB2RGB)
    return Image.fromarray(rgb)


def rectify_image(
    image: Image.Image,
    *,
    paper: str = "letter",
    dpi_floor: float = DEFAULT_OUTPUT_DPI,
    allow_perspective: bool = True,
) -> tuple[Image.Image, str, str]:
    import cv2
    paper_size = PAPER_SIZES_IN.get(paper)
    if paper_size is None:
        raise PhotoIntakeError(
            f"unknown paper size {paper!r}; choose from {sorted(PAPER_SIZES_IN)}"
        )

    rgb = image.convert("RGB")
    array = np.asarray(rgb)
    gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)

    if allow_perspective:
        document_quad = _find_document_quad(gray)
        quad = _find_fiducial_quad(gray, document_quad=document_quad)
        method = "fiducials"
        if quad is None:
            quad = document_quad
            method = "edges"
        if quad is not None:
            try:
                out_w, out_h, _dpi = _measure_output_size(
                    quad, paper_size_in=paper_size, dpi_floor=dpi_floor
                )
                warped = _warp_quad(array, quad, out_w, out_h)
            except ValueError:
                pass
            else:
                rectified = _normalize_lighting(Image.fromarray(warped))
                return rectified, method, ""

    note = (
        "--no-perspective requested; deskew only"
        if not allow_perspective
        else (
            "no page quad could be found (no registration marks, and no "
            "confident document edge); imported flat rather than dropped"
        )
    )
    deskewed = _normalize_lighting(_deskew(rgb))
    return deskewed, "deskew", note


def _write_pdf(pages: Sequence[Image.Image], destination: Path, *, dpi: float) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    first, *rest = (page.convert("RGB") for page in pages)
    first.save(
        destination,
        format="PDF",
        save_all=True,
        append_images=rest,
        resolution=dpi,
    )


def import_photos(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    pages_per_photo_set: int | None = None,
    dpi: float = DEFAULT_OUTPUT_DPI,
    paper: str = "letter",
    perspective: bool = True,
    progress_callback: Callable[[str], None] | None = None,
) -> PhotoIntakeReport:
    source = Path(input_path)
    if not source.exists():
        raise PhotoIntakeError(f"input path not found: {source}")
    destination_dir = Path(output_dir)

    mode, groups = plan_groups(source, pages_per_photo_set=pages_per_photo_set)
    if not groups:
        raise PhotoIntakeError(f"no images found in {source}")

    _require_heic_support([path for group in groups for path in group.images])

    emit = progress_callback or (lambda _message: None)

    outcomes: list[ImageOutcome] = []
    submissions: list[StudentSubmission] = []

    for index, group in enumerate(groups, start=1):
        pages: list[Image.Image] = []
        page_sources: list[Path] = []
        for image_path in group.images:
            emit(f"Importing {image_path.name}…")
            try:
                oriented = load_oriented_image(image_path)
            except PhotoIntakeError:
                raise
            except Exception as exc:  # noqa: BLE001
                outcomes.append(
                    ImageOutcome(source=image_path, method="unreadable", note=str(exc))
                )
                continue

            rectified, method, note = rectify_image(
                oriented, paper=paper, dpi_floor=dpi, allow_perspective=perspective
            )
            outcomes.append(ImageOutcome(source=image_path, method=method, note=note))
            pages.append(rectified)
            page_sources.append(image_path)

        if not pages:
            continue

        destination = destination_dir / _output_filename(group, index)
        _write_pdf(pages, destination, dpi=dpi)
        submissions.append(
            StudentSubmission(token=group.token, output_pdf=destination, pages=tuple(page_sources))
        )

    return PhotoIntakeReport(
        grouping_mode=mode,
        outcomes=tuple(outcomes),
        submissions=tuple(submissions),
        output_dir=destination_dir,
        dpi=dpi,
    )
