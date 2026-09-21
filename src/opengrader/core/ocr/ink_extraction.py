from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import cv2
import numpy as np
from PIL import Image


class TemplateRegistrationError(RuntimeError):
    """Raised when a blank page cannot be registered safely to a scan."""


@dataclass(frozen=True)
class RegistrationMetrics:
    matches: int
    inliers: int
    inlier_ratio: float
    median_error_px: float


@dataclass(frozen=True)
class RegisteredTemplatePage:
    aligned_template: Image.Image
    metrics: RegistrationMetrics
    homography: np.ndarray = field(repr=False, compare=False)
    template_size: tuple[int, int]


@dataclass(frozen=True)
class InkFragment:
    """One spatially distinct student-ink fragment within a crop."""

    bbox_px: tuple[int, int, int, int]
    ink_pixels: int


@dataclass(frozen=True)
class StudentInk:
    """Student-only pixels and their spatial fragments for one crop."""

    image: Image.Image
    mask: Image.Image
    fragments: tuple[InkFragment, ...]
    unmatched_ink_ratio: float | None = None

    @property
    def has_ink(self) -> bool:
        return bool(self.fragments)


_REGISTRATION_MAX_DIMENSION = 1800
_MIN_GOOD_MATCHES = 12
_MIN_INLIERS = 8
_MIN_INLIER_RATIO = 0.25
_MAX_MEDIAN_ERROR_PX = 8.0


def register_template_page(
    scan: Image.Image,
    template: Image.Image,
    *,
    max_dimension: int = _REGISTRATION_MAX_DIMENSION,
) -> RegisteredTemplatePage:
    """Warp a blank template page into the coordinate system of a scanned page."""

    scan_rgb = np.asarray(scan.convert("RGB"))
    template_rgb = np.asarray(template.convert("RGB"))
    scan_small, scan_scale = _registration_image(scan_rgb, max_dimension)
    template_small, template_scale = _registration_image(
        template_rgb, max_dimension
    )

    sift = cv2.SIFT_create(nfeatures=5000, contrastThreshold=0.02)
    template_points, template_descriptors = sift.detectAndCompute(
        template_small, None
    )
    scan_points, scan_descriptors = sift.detectAndCompute(scan_small, None)
    if template_descriptors is None or scan_descriptors is None:
        raise TemplateRegistrationError(
            "not enough printed detail to align the blank page to the scan"
        )

    matcher = cv2.BFMatcher(cv2.NORM_L2)
    pairs = matcher.knnMatch(template_descriptors, scan_descriptors, k=2)
    good = [
        first
        for first, second in pairs
        if first.distance < 0.74 * second.distance
    ]
    if len(good) < _MIN_GOOD_MATCHES:
        raise TemplateRegistrationError(
            f"only {len(good)} reliable page features matched; "
            f"need at least {_MIN_GOOD_MATCHES}"
        )

    template_xy = np.float32(
        [template_points[match.queryIdx].pt for match in good]
    ).reshape(-1, 1, 2)
    scan_xy = np.float32(
        [scan_points[match.trainIdx].pt for match in good]
    ).reshape(-1, 1, 2)
    homography_small, inlier_mask = cv2.findHomography(
        template_xy, scan_xy, cv2.RANSAC, 4.0
    )
    if homography_small is None or inlier_mask is None:
        raise TemplateRegistrationError("page feature matches did not form a homography")

    inliers = inlier_mask.reshape(-1).astype(bool)
    inlier_count = int(inliers.sum())
    inlier_ratio = inlier_count / len(good)
    if inlier_count < _MIN_INLIERS or inlier_ratio < _MIN_INLIER_RATIO:
        raise TemplateRegistrationError(
            "page alignment was ambiguous: "
            f"{inlier_count}/{len(good)} feature matches agreed"
        )

    projected = cv2.perspectiveTransform(template_xy[inliers], homography_small)
    errors = np.linalg.norm(projected - scan_xy[inliers], axis=2).reshape(-1)
    median_error_small = float(np.median(errors))
    median_error_px = median_error_small / scan_scale
    if median_error_px > _MAX_MEDIAN_ERROR_PX:
        raise TemplateRegistrationError(
            "page alignment is too imprecise: median feature error "
            f"{median_error_px:.1f}px"
        )

    template_to_small = np.array(
        [[template_scale, 0.0, 0.0], [0.0, template_scale, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    small_to_scan = np.array(
        [[1.0 / scan_scale, 0.0, 0.0], [0.0, 1.0 / scan_scale, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    homography = small_to_scan @ homography_small @ template_to_small
    scan_height, scan_width = scan_rgb.shape[:2]
    aligned = cv2.warpPerspective(
        template_rgb,
        homography,
        (scan_width, scan_height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )

    return RegisteredTemplatePage(
        aligned_template=Image.fromarray(aligned),
        metrics=RegistrationMetrics(
            matches=len(good),
            inliers=inlier_count,
            inlier_ratio=inlier_ratio,
            median_error_px=median_error_px,
        ),
        homography=homography,
        template_size=(template_rgb.shape[1], template_rgb.shape[0]),
    )


def isolate_student_ink(
    scan_crop: Image.Image,
    aligned_template_crop: Image.Image,
    *,
    dpi: int = 300,
) -> StudentInk:
    """Remove registered printed content while retaining added pen and pencil marks."""

    scan_rgb = np.asarray(scan_crop.convert("RGB"))
    template_rgb = np.asarray(aligned_template_crop.convert("RGB"))
    if scan_rgb.shape != template_rgb.shape:
        raise ValueError("scan and aligned template crops must have identical dimensions")

    scan_gray = cv2.cvtColor(scan_rgb, cv2.COLOR_RGB2GRAY)
    template_gray = cv2.cvtColor(template_rgb, cv2.COLOR_RGB2GRAY)
    scan_ink = _ink_mask(scan_gray, threshold=238, min_contrast=5)
    template_ink = _ink_mask(template_gray, threshold=246, min_contrast=3)

    # A few pixels of page curl or local scanner stretch remain after a global
    # homography.  A 1.3 mm guard covers that drift without swallowing normal
    # pen strokes that cross a printed baseline.
    guard_radius = max(2, round(dpi * 0.05))
    guard_size = guard_radius * 2 + 1
    template_guard = cv2.dilate(
        template_ink,
        cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (guard_size, guard_size)
        ),
    )
    student_mask = cv2.bitwise_and(scan_ink, cv2.bitwise_not(template_guard))

    # A filled checkbox or bubble can sit entirely inside the printed guard.
    # Keep pixels darker than every nearby blank-template pixel so those marks
    # survive without reintroducing slightly displaced printed edges.
    comparison_radius = max(2, round(dpi * 0.02))
    comparison_size = comparison_radius * 2 + 1
    local_template_floor = cv2.erode(
        template_gray,
        cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (comparison_size, comparison_size)
        ),
    )
    added_darkness = (
        (scan_gray.astype(np.int16) + 28 < local_template_floor.astype(np.int16))
        & (scan_gray < 220)
    ).astype(np.uint8) * 255
    added_darkness = _remove_small_components(
        added_darkness,
        min_area=max(8, round(40 * (dpi / 300) ** 2)),
    )
    student_mask = cv2.bitwise_or(student_mask, added_darkness)

    close_size = max(1, round(dpi / 300))
    if close_size > 1:
        student_mask = cv2.morphologyEx(
            student_mask,
            cv2.MORPH_CLOSE,
            np.ones((close_size, close_size), dtype=np.uint8),
        )
    student_mask = _remove_page_edge_lines(student_mask)

    fragments = find_ink_fragments(student_mask, dpi=dpi)
    retained = np.zeros_like(student_mask)
    for fragment in fragments:
        x, y, width, height = fragment.bbox_px
        retained[y : y + height, x : x + width] = student_mask[
            y : y + height, x : x + width
        ]

    isolated = np.full_like(scan_rgb, 255)
    isolated[retained > 0] = scan_rgb[retained > 0]
    template_ink_pixels = int(np.count_nonzero(template_ink))
    return StudentInk(
        image=Image.fromarray(isolated),
        mask=Image.fromarray(cv2.bitwise_not(retained)),
        fragments=fragments,
        unmatched_ink_ratio=(
            float(np.count_nonzero(retained)) / template_ink_pixels
            if template_ink_pixels
            else None
        ),
    )


def find_ink_fragments(mask: np.ndarray, *, dpi: int = 300) -> tuple[InkFragment, ...]:
    """Find every non-speck ink cluster without assuming where an answer belongs."""

    binary = _as_foreground_mask(mask)
    min_component_area = max(3, round(4 * (dpi / 300) ** 2))
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(binary, 8)
    component_mask = np.zeros_like(binary)
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area >= min_component_area:
            component_mask[labels == label] = 255

    if not np.any(component_mask):
        return ()

    join_width = max(3, round(dpi * 0.10))
    join_height = max(1, round(dpi * 0.03))
    joined = cv2.dilate(
        component_mask,
        cv2.getStructuringElement(cv2.MORPH_RECT, (join_width, join_height)),
    )
    group_count, group_labels, _group_stats, _ = cv2.connectedComponentsWithStats(
        joined, 8
    )

    min_fragment_ink = max(8, round(10 * (dpi / 300) ** 2))
    fragments: list[InkFragment] = []
    for group in range(1, group_count):
        pixels = component_mask[(group_labels == group) & (component_mask > 0)]
        ink_pixels = int(pixels.size)
        if ink_pixels < min_fragment_ink:
            continue
        ys, xs = np.nonzero((group_labels == group) & (component_mask > 0))
        x0, x1 = int(xs.min()), int(xs.max()) + 1
        y0, y1 = int(ys.min()), int(ys.max()) + 1
        fragments.append(
            InkFragment(
                bbox_px=(x0, y0, x1 - x0, y1 - y0),
                ink_pixels=ink_pixels,
            )
        )

    return tuple(_reading_order(fragments))


def crop_student_ink(
    ink: StudentInk,
    box: tuple[int, int, int, int],
    *,
    dpi: int = 300,
) -> StudentInk:
    """Crop an isolated page and recalculate fragments in the new coordinates."""

    image = ink.image.crop(box)
    visual_mask = ink.mask.crop(box)
    foreground = (np.asarray(visual_mask) < 128).astype(np.uint8) * 255
    fragments = find_ink_fragments(foreground, dpi=dpi)
    return StudentInk(image=image, mask=visual_mask, fragments=fragments)


def _registration_image(
    rgb: np.ndarray, max_dimension: int
) -> tuple[np.ndarray, float]:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    scale = min(1.0, max_dimension / max(height, width))
    if scale < 1.0:
        gray = cv2.resize(
            gray,
            (round(width * scale), round(height * scale)),
            interpolation=cv2.INTER_AREA,
        )
    return gray, scale


def _ink_mask(gray: np.ndarray, *, threshold: int, min_contrast: int) -> np.ndarray:
    background = cv2.GaussianBlur(gray, (0, 0), sigmaX=12, sigmaY=12)
    contrast = cv2.subtract(background, gray)
    mask = ((gray < threshold) & (contrast >= min_contrast)).astype(np.uint8) * 255
    return mask


def _as_foreground_mask(mask: np.ndarray) -> np.ndarray:
    if mask.ndim != 2:
        raise ValueError("ink mask must be a two-dimensional array")
    if mask.dtype == np.bool_:
        return mask.astype(np.uint8) * 255
    return (mask > 0).astype(np.uint8) * 255


def _remove_small_components(mask: np.ndarray, *, min_area: int) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    cleaned = np.zeros_like(mask)
    for label in range(1, count):
        if int(stats[label, cv2.CC_STAT_AREA]) >= min_area:
            cleaned[labels == label] = 255
    return cleaned


def _remove_page_edge_lines(mask: np.ndarray) -> np.ndarray:
    """Drop sparse scan-frame remnants without treating interior underlines as noise."""

    height, width = mask.shape
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    cleaned = mask.copy()
    for label in range(1, count):
        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        component_width = int(stats[label, cv2.CC_STAT_WIDTH])
        component_height = int(stats[label, cv2.CC_STAT_HEIGHT])
        area = int(stats[label, cv2.CC_STAT_AREA])
        touches_edge = (
            x < width * 0.05
            or y < height * 0.05
            or x + component_width > width * 0.95
            or y + component_height > height * 0.95
        )
        long = component_width > width * 0.35 or component_height > height * 0.35
        sparse = area / max(1, component_width * component_height) < 0.18
        edge_rule = (
            component_width > width * 0.35
            and component_height < height * 0.03
        ) or (
            component_height > height * 0.35
            and component_width < width * 0.03
        )
        if touches_edge and long and (sparse or edge_rule):
            cleaned[labels == label] = 0
    return cleaned


def _reading_order(fragments: Sequence[InkFragment]) -> list[InkFragment]:
    if not fragments:
        return []

    ordered = sorted(fragments, key=lambda fragment: fragment.bbox_px[1])
    lines: list[list[InkFragment]] = []
    for fragment in ordered:
        _x, y, _width, height = fragment.bbox_px
        center = y + height / 2
        for line in lines:
            line_centers = [item.bbox_px[1] + item.bbox_px[3] / 2 for item in line]
            line_heights = [item.bbox_px[3] for item in line]
            if abs(center - float(np.median(line_centers))) <= max(
                height, float(np.median(line_heights))
            ) * 0.55:
                line.append(fragment)
                break
        else:
            lines.append([fragment])

    lines.sort(key=lambda line: min(item.bbox_px[1] for item in line))
    result: list[InkFragment] = []
    for line in lines:
        result.extend(sorted(line, key=lambda fragment: fragment.bbox_px[0]))
    return result
