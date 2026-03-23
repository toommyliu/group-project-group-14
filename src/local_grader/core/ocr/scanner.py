from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np
from PIL import Image

@dataclass
class DetectedRegion:
    """A rectangular region detected in a template scan."""
    bbox: list[float]  # [x, y, w, h] normalized 0-1
    region_type: Literal["header_field", "question", "unknown"]
    confidence: float  # 0-100
    label: str | None = None

# Minimum area as fraction of page area to consider a contour
MIN_REGION_AREA_RATIO = 0.002
# Maximum area — skip huge rectangles (e.g., page borders)
MAX_REGION_AREA_RATIO = 0.5
# Minimum aspect ratio (w/h) to consider — skip very narrow/tall regions
MIN_ASPECT_RATIO = 0.3
# Header zone: regions with y-center in the top N% are header fields
HEADER_ZONE_RATIO = 0.15

def _find_rectangles(
    gray: np.ndarray,
    min_area: int,
    max_area: int,
) -> list[tuple[int, int, int, int]]:
    """
    Find rectangular contours in a grayscale image.

    Args:
        gray: Grayscale image as numpy array.
        min_area: Minimum area in pixels to consider a valid region.
        max_area: Maximum area in pixels to consider a valid region.
    
    Returns:
        List of (x, y, w, h) tuples for detected rectangles in pixel coordinates.
    """
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 10
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    dilated = cv2.dilate(binary, kernel, iterations=1)
    contours, _ = cv2.findContours(dilated, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    rectangles: list[tuple[int, int, int, int]] = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area or area > max_area:
            continue
        epsilon = 0.02 * cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, epsilon, True)
        if len(approx) < 4 or len(approx) > 8:
            continue
        x, y, w, h = cv2.boundingRect(approx)
        if w == 0 or h == 0:
            continue
        aspect = w / h
        if aspect < MIN_ASPECT_RATIO:
            continue
        rect_area = w * h
        rectangularity = area / rect_area
        if rectangularity < 0.6:
            continue
        rectangles.append((x, y, w, h))
    return rectangles


def _merge_overlapping(
    rects: list[tuple[int, int, int, int]],
    overlap_threshold: float = 0.5,
) -> list[tuple[int, int, int, int]]:
    """Merge rectangles that significantly overlap."""
    if not rects:
        return []
    merged: list[tuple[int, int, int, int]] = []
    used = [False] * len(rects)
    for i in range(len(rects)):
        if used[i]:
            continue
        x1, y1, w1, h1 = rects[i]
        ax0, ay0, ax1, ay1 = x1, y1, x1 + w1, y1 + h1

        for j in range(i + 1, len(rects)):
            if used[j]:
                continue
            x2, y2, w2, h2 = rects[j]
            bx0, by0, bx1, by1 = x2, y2, x2 + w2, y2 + h2

            # Compute intersection
            ix0 = max(ax0, bx0)
            iy0 = max(ay0, by0)
            ix1 = min(ax1, bx1)
            iy1 = min(ay1, by1)
            if ix0 < ix1 and iy0 < iy1:
                intersection = (ix1 - ix0) * (iy1 - iy0)
                smaller_area = min(w1 * h1, w2 * h2)
                if smaller_area > 0 and intersection / smaller_area > overlap_threshold:
                    # Merge: take union bounding box
                    ax0 = min(ax0, bx0)
                    ay0 = min(ay0, by0)
                    ax1 = max(ax1, bx1)
                    ay1 = max(ay1, by1)
                    used[j] = True
        merged.append((ax0, ay0, ax1 - ax0, ay1 - ay0))

    return merged


def _classify_region(
    x: float, y: float, w: float, h: float, page_h: float
) -> Literal["header_field", "question", "unknown"]:
    """Classify a region based on its position and size."""
    y_center = (y + h / 2) / page_h
    if y_center < HEADER_ZONE_RATIO:
        return "header_field"
    if h > 0.05:
        return "question"
    return "unknown"


def scan_template(
    image: Image.Image,
    min_area_ratio: float = MIN_REGION_AREA_RATIO,
    max_area_ratio: float = MAX_REGION_AREA_RATIO,
) -> list[DetectedRegion]:
    """
    Auto-detect rectangular regions in a template image.

    Scans for drawn boxes/rectangles and classifies them as
    header fields (name, ID) or question response areas.

    Args:
        image: PIL Image of the template page.
        min_area_ratio: Minimum region area as fraction of page area.
        max_area_ratio: Maximum region area as fraction of page area.

    Returns:
        List of DetectedRegion objects sorted top-to-bottom.
    """
    arr = np.array(image)
    if len(arr.shape) == 3:
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    else:
        gray = arr

    page_h, page_w = gray.shape[:2]
    page_area = page_w * page_h
    min_area = int(page_area * min_area_ratio)
    max_area = int(page_area * max_area_ratio)

    rects = _find_rectangles(gray, min_area, max_area)
    rects = _merge_overlapping(rects)

    # Sort by y position (top to bottom)
    rects.sort(key=lambda r: r[1])

    regions: list[DetectedRegion] = []
    for x, y, w, h in rects:
        # Normalize to 0-1
        x_norm = round(x / page_w, 3)
        y_norm = round(y / page_h, 3)
        w_norm = round(w / page_w, 3)
        h_norm = round(h / page_h, 3)

        region_type = _classify_region(x, y, w, h, page_h)
        confidence = min(95.0, 60.0 + (w * h) / page_area * 200)
        regions.append(
            DetectedRegion(
                bbox=[x_norm, y_norm, w_norm, h_norm],
                region_type=region_type,
                confidence=round(confidence, 1),
            )
        )

    return regions

def regions_to_spec_draft(
    regions: list[DetectedRegion],
    unit_id: str,
) -> dict:
    """
        Convert detected regions into a draft template spec JSON.
        Header fields are assigned IDs like field_1, field_2.
        Question regions are assigned IDs like q1, q2.
    """
    fields: list[dict] = []
    questions: list[dict] = []
    field_counter = 0
    question_counter = 0

    for region in regions:
        if region.region_type == "header_field":
            field_counter += 1
            fields.append(
                {
                    "id": region.label or f"field_{field_counter}",
                    "bbox": region.bbox,
                    "type": "text",
                    "required": True,
                }
            )
        elif region.region_type == "question":
            question_counter += 1
            questions.append(
                {
                    "id": region.label or f"q{question_counter}",
                    "bbox": region.bbox,
                    "response_type": "free_response",
                    "points": 10,
                }
            )

    page_spec: dict = {
        "page_number": 1,
        "anchors": [
            {"id": "tl", "bbox": [0.020, 0.020, 0.029, 0.023]},
            {"id": "tr", "bbox": [0.951, 0.020, 0.029, 0.023]},
            {"id": "bl", "bbox": [0.020, 0.950, 0.029, 0.023]},
            {"id": "br", "bbox": [0.951, 0.950, 0.029, 0.023]},
        ],
        "fields": fields,
        "questions": questions,
    }

    return {
        "schema_version": "1.0",
        "unit_id": unit_id,
        "pages": [page_spec],
    }
