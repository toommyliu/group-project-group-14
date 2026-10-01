from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

MATCH_MAX_DIMENSION = 1400
_SIFT_FEATURES = 3000
_SIFT_CONTRAST = 0.02
_RATIO_TEST = 0.75
_RANSAC_UNITS = 3.0
MIN_INLIERS = 25
_MIN_INLIER_RATIO = 0.15


@dataclass(frozen=True)
class PageFeatures:
    points: np.ndarray
    descriptors: np.ndarray | None
    aspect: float

    @property
    def count(self) -> int:
        return 0 if self.descriptors is None else len(self.descriptors)


@dataclass(frozen=True)
class Alignment:
    homography: np.ndarray
    inliers: int
    matches: int
    median_error: float

    @property
    def rotation_degrees(self) -> float:
        a, b = self.homography[0, 0], self.homography[1, 0]
        return math.degrees(math.atan2(b, a))

    @property
    def quarter_turns(self) -> int:
        return round(self.rotation_degrees / 90) % 4

    def to_reference(self, points: np.ndarray) -> np.ndarray:
        return _apply(self.homography, points)

    def to_page(self, points: np.ndarray) -> np.ndarray:
        return _apply(np.linalg.inv(self.homography), points)


def gray_for_matching(image: Image.Image) -> np.ndarray:
    gray = np.asarray(image.convert("L"))
    height, width = gray.shape
    scale = min(1.0, MATCH_MAX_DIMENSION / max(height, width))
    if scale < 1.0:
        gray = cv2.resize(gray, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    return gray


def page_features(image: Image.Image | np.ndarray) -> PageFeatures:
    gray = image if isinstance(image, np.ndarray) else gray_for_matching(image)
    height, width = gray.shape
    sift = cv2.SIFT_create(nfeatures=_SIFT_FEATURES, contrastThreshold=_SIFT_CONTRAST)
    keypoints, descriptors = sift.detectAndCompute(gray, None)
    points = np.float32([kp.pt for kp in keypoints]).reshape(-1, 2) / np.float32([width, height])
    return PageFeatures(points=points, descriptors=descriptors, aspect=height / width)


def align(page: PageFeatures, reference: PageFeatures, *, min_inliers: int = MIN_INLIERS) -> Alignment | None:
    if page.count < min_inliers or reference.count < min_inliers:
        return None
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    pairs = matcher.knnMatch(page.descriptors, reference.descriptors, k=2)
    good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < _RATIO_TEST * pair[1].distance]
    if len(good) < min_inliers:
        return None
    ref_px = np.float32([1.0, reference.aspect])
    page_px = np.float32([1.0, page.aspect])
    src = (page.points[[m.queryIdx for m in good]] * page_px * 1000).reshape(-1, 1, 2)
    dst = (reference.points[[m.trainIdx for m in good]] * ref_px * 1000).reshape(-1, 1, 2)
    homography_px, mask = cv2.findHomography(src, dst, cv2.RANSAC, _RANSAC_UNITS)
    if homography_px is None or mask is None:
        return None
    inliers = mask.ravel().astype(bool)
    count = int(inliers.sum())
    if count < min_inliers or count / len(good) < _MIN_INLIER_RATIO:
        return None
    errors = np.linalg.norm(cv2.perspectiveTransform(src[inliers], homography_px) - dst[inliers], axis=2)
    to_norm_page = np.diag([1000.0, 1000.0 * page.aspect, 1.0])
    from_norm_ref = np.diag([1 / 1000.0, 1 / (1000.0 * reference.aspect), 1.0])
    homography = from_norm_ref @ homography_px @ to_norm_page
    homography = homography / homography[2, 2]
    if not _plausible(homography):
        return None
    return Alignment(homography=homography, inliers=count, matches=len(good), median_error=float(np.median(errors)) / 1000)


def rectify(image: Image.Image, alignment: Alignment, size: tuple[int, int]) -> Image.Image:
    width, height = image.size
    to_norm = np.diag([1 / width, 1 / height, 1.0])
    to_px = np.diag([size[0], size[1], 1.0])
    matrix = to_px @ alignment.homography @ to_norm
    warped = cv2.warpPerspective(
        np.asarray(image.convert("RGB")), matrix, size, flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255),
    )
    return Image.fromarray(warped)


def page_box(alignment: Alignment, box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = box
    corners = alignment.to_page(np.array([[x0, y0], [x1, y0], [x0, y1], [x1, y1]], dtype=np.float64))
    xs, ys = np.clip(corners[:, 0], 0, 1), np.clip(corners[:, 1], 0, 1)
    return float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())


def _apply(matrix: np.ndarray, points: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    projected = np.hstack([points, np.ones((len(points), 1))]) @ matrix.T
    return projected[:, :2] / projected[:, 2:3]


def _plausible(homography: np.ndarray) -> bool:
    corners = _apply(homography, np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float64))
    area = 0.5 * abs(sum(corners[i, 0] * corners[(i + 1) % 4, 1] - corners[(i + 1) % 4, 0] * corners[i, 1] for i in range(4)))
    if not 0.25 < area < 4.0:
        return False
    edges = [corners[(i + 1) % 4] - corners[i] for i in range(4)]
    cross = [edges[i][0] * edges[(i + 1) % 4][1] - edges[i][1] * edges[(i + 1) % 4][0] for i in range(4)]
    if not (all(c > 0 for c in cross) or all(c < 0 for c in cross)):
        return False
    return bool(np.linalg.det(homography[:2, :2]) > 0)
