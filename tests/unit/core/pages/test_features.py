import random

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from opengrader.core.pages.features import align, page_box, page_features, rectify
from opengrader.core.pdf.templates.fonts import CMU_REGULAR_FILE as FONT


def _page(seed: int = 3) -> Image.Image:
    rng = random.Random(seed)
    image = Image.new("RGB", (850, 1100), "white")
    pen = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONT), 20)
    letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789()=+"
    for row in range(40):
        words = ("".join(rng.choice(letters) for _ in range(rng.randint(2, 9))) for _ in range(7))
        pen.text((60, 40 + row * 26), f"{row + 1}. " + " ".join(words), fill="black", font=font)
    pen.rectangle((60, 1060, 790, 1062), fill="black")
    return image


def _warp(image: Image.Image, matrix: np.ndarray) -> Image.Image:
    w, h = image.size
    return Image.fromarray(cv2.warpPerspective(np.asarray(image), matrix, (w, h), borderValue=(255, 255, 255)))


def _rotation(image: Image.Image, degrees: float) -> np.ndarray:
    w, h = image.size
    return np.vstack([cv2.getRotationMatrix2D((w / 2, h / 2), degrees, 1.0), [0, 0, 1]])


def test_a_skewed_copy_aligns_back_onto_the_original():
    original = _page()
    skewed = _warp(original, _rotation(original, 3.0))

    alignment = align(page_features(skewed), page_features(original))

    assert alignment is not None and alignment.inliers > 150
    assert abs(alignment.rotation_degrees) < 10 and alignment.quarter_turns == 0
    w, h = original.size
    moved = _rotation(original, 3.0) @ np.array([425, 300, 1.0])
    back = alignment.to_reference(np.array([[moved[0] / w, moved[1] / h]]))[0]
    assert abs(back[0] - 0.5) < 0.005 and abs(back[1] - 300 / h) < 0.005


def test_an_upside_down_scan_is_recognized_as_half_a_turn():
    original = _page()
    upside_down = original.rotate(180)

    alignment = align(page_features(upside_down), page_features(original))

    assert alignment is not None and alignment.quarter_turns == 2


def test_rectifying_a_perspective_photo_restores_the_page():
    original = _page()
    w, h = original.size
    photo_matrix = cv2.getPerspectiveTransform(
        np.float32([[0, 0], [w, 0], [w, h], [0, h]]),
        np.float32([[40, 30], [w - 20, 10], [w - 5, h - 15], [15, h - 40]]),
    ).astype(np.float64)
    photo = _warp(original, photo_matrix)

    alignment = align(page_features(photo), page_features(original))
    restored = np.asarray(rectify(photo, alignment, (w, h)).convert("L"), dtype=np.float32)

    reference = np.asarray(original.convert("L"), dtype=np.float32)
    assert np.mean(np.abs(restored[60:1040, 60:790] - reference[60:1040, 60:790])) < 12


def test_page_boxes_map_back_onto_the_scan():
    original = _page()
    shifted = _warp(original, np.array([[1, 0, 40.0], [0, 1, 25.0], [0, 0, 1]]))

    alignment = align(page_features(shifted), page_features(original))
    x0, y0, x1, y1 = page_box(alignment, (0.1, 0.2, 0.5, 0.3))

    assert abs(x0 - (0.1 + 40 / 850)) < 0.004 and abs(y0 - (0.2 + 25 / 1100)) < 0.004


def test_a_different_page_aligns_far_worse_than_the_same_page_and_a_blank_not_at_all():
    reference = page_features(_page(seed=3))
    same = align(page_features(_warp(_page(seed=3), _rotation(_page(), 1.0))), reference)
    different = align(page_features(_page(seed=99)), reference)

    assert different is None or different.inliers < 0.5 * same.inliers
    assert align(page_features(Image.new("RGB", (850, 1100), "white")), reference) is None
