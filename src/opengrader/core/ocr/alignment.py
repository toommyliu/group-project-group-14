from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np
from PIL import Image

from .utils import apply_grayscale


@dataclass
class AnchorRegion:
    id: str
    x_norm: float
    y_norm: float
    w_norm: float
    h_norm: float


class FeatureMatcher(Protocol):
    def detect_keypoints(self, image: np.ndarray) -> tuple: ...

    def match_keypoints(
        self,
        reference: np.ndarray,
        candidate: np.ndarray,
        kp_ref: tuple,
        kp_cand: tuple,
    ) -> tuple: ...


class OpenCVMatcher:
    def __init__(self) -> None:
        self.detector = cv2.SIFT_create()

    def detect_keypoints(self, image: np.ndarray) -> tuple:
        return self.detector.detectAndCompute(image, None)

    def match_keypoints(
        self,
        reference: np.ndarray,
        candidate: np.ndarray,
        kp_ref: tuple,
        kp_cand: tuple,
    ) -> tuple:
        if (
            kp_ref is None
            or kp_cand is None
            or kp_ref[1] is None
            or kp_cand[1] is None
            or len(kp_ref[0]) == 0
            or len(kp_cand[0]) == 0
        ):
            return [], None
        bf = cv2.BFMatcher()
        matches = bf.knnMatch(kp_ref[1], kp_cand[1], k=2)
        good = []
        for pair in matches:
            if len(pair) < 2:
                continue
            m, n = pair
            if m.distance < 0.75 * n.distance:
                good.append(m)
        if len(good) < 4:
            return [], None
        src_pts = np.float32([kp_ref[0][m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst_pts = np.float32([kp_cand[0][m.trainIdx].pt for m in good]).reshape(
            -1, 1, 2
        )
        M, _ = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
        return good, M


def crop_anchor_region(
    image: np.ndarray,
    anchor: AnchorRegion,
    page_w: int,
    page_h: int,
) -> np.ndarray:
    x0 = int(round(anchor.x_norm * page_w))
    y0 = int(round(anchor.y_norm * page_h))
    x1 = int(round((anchor.x_norm + anchor.w_norm) * page_w))
    y1 = int(round((anchor.y_norm + anchor.h_norm) * page_h))

    x0 = max(0, min(page_w, x0))
    y0 = max(0, min(page_h, y0))
    x1 = max(0, min(page_w, x1))
    y1 = max(0, min(page_h, y1))

    if x1 <= x0 or y1 <= y0:
        return image[0:0, 0:0]
    return image[y0:y1, x0:x1]


def compute_transform(
    reference_image: np.ndarray,
    candidate_image: np.ndarray,
    reference_anchors: list[AnchorRegion],
    candidate_anchors: list[AnchorRegion] | None,
    matcher: FeatureMatcher | None = None,
) -> np.ndarray | None:
    if matcher is None:
        matcher = OpenCVMatcher()
    ref_gray = apply_grayscale(reference_image)
    cand_gray = apply_grayscale(candidate_image)
    kp_ref, desc_ref = matcher.detect_keypoints(ref_gray)
    kp_cand, desc_cand = matcher.detect_keypoints(cand_gray)
    _, M = matcher.match_keypoints(
        reference_image, candidate_image, (kp_ref, desc_ref), (kp_cand, desc_cand)
    )
    return M


def align_image(
    image: Image.Image,
    reference_image: Image.Image | None,
    reference_anchors: list[AnchorRegion] | None,
    candidate_anchors: list[AnchorRegion] | None,
    matcher: FeatureMatcher | None = None,
) -> Image.Image:
    if reference_image is None or not reference_anchors:
        return image
    ref_arr = np.array(reference_image)
    cand_arr = np.array(image)
    M = compute_transform(
        ref_arr, cand_arr, reference_anchors, candidate_anchors, matcher
    )
    if M is None:
        return image
    h, w = ref_arr.shape[:2]
    aligned = cv2.warpPerspective(cand_arr, M, (w, h))
    return Image.fromarray(aligned)


def preprocess_for_ocr(image: Image.Image, deskew: bool = True) -> Image.Image:
    """
    Apply binarization (black-and-white) and deskewing (tries to correct for rotation).
    """
    arr = np.array(image)
    gray = apply_grayscale(arr)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # TODO: suggestion to use Ollama to try and deskew?
    if deskew:
        coords = cv2.findNonZero(binary)
        if coords is not None:
            angle = cv2.minAreaRect(coords)[2]
            if angle < -45:
                angle = 90 + angle
            elif angle > 45:
                angle = angle - 90
            else:
                angle = angle
            if abs(angle) > 0.5:
                M = cv2.getRotationMatrix2D(
                    (gray.shape[1] / 2, gray.shape[0] / 2), angle, 1.0
                )
                binary = cv2.warpAffine(binary, M, (gray.shape[1], gray.shape[0]))
    return Image.fromarray(binary)


def detect_anchor_presence(
    image: np.ndarray,
    anchor: AnchorRegion,
    page_w: int,
    page_h: int,
    dark_threshold: int = 140,
    fill_ratio_threshold: float = 0.35,
) -> bool:
    """Check if an anchor region contains a filled marker"""
    region = crop_anchor_region(image, anchor, page_w, page_h)
    if region.size == 0:
        return False
    gray = apply_grayscale(region)
    _, binary_inv = cv2.threshold(gray, dark_threshold, 255, cv2.THRESH_BINARY_INV)
    if binary_inv.size == 0:
        return False

    h, w = binary_inv.shape[:2]
    margin_x = max(1, int(round(w * 0.18)))
    margin_y = max(1, int(round(h * 0.18)))

    if margin_x * 2 >= w or margin_y * 2 >= h:
        inner = binary_inv
    else:
        inner = binary_inv[margin_y : h - margin_y, margin_x : w - margin_x]

    if inner.size == 0:
        return False

    full_ratio = float(np.count_nonzero(binary_inv)) / float(binary_inv.size)
    inner_ratio = float(np.count_nonzero(inner)) / float(inner.size)

    return inner_ratio >= fill_ratio_threshold and full_ratio >= (
        fill_ratio_threshold * 0.5
    )
