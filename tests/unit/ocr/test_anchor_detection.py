import numpy as np

from opengrader.core.ocr.alignment import AnchorRegion, detect_anchor_presence
from opengrader.core.pdf.templates.anchor import anchor_bbox_map_norm


def _anchor_region() -> AnchorRegion:
    return AnchorRegion(id="tl", x_norm=0.1, y_norm=0.1, w_norm=0.2, h_norm=0.2)


def test_detect_anchor_presence_true_for_filled_square() -> None:
    page_h = 300
    page_w = 300
    image = np.full((page_h, page_w), 255, dtype=np.uint8)
    anchor = _anchor_region()

    x0 = int(round(anchor.x_norm * page_w))
    y0 = int(round(anchor.y_norm * page_h))
    x1 = int(round((anchor.x_norm + anchor.w_norm) * page_w))
    y1 = int(round((anchor.y_norm + anchor.h_norm) * page_h))

    image[y0:y1, x0:x1] = 0

    assert detect_anchor_presence(image, anchor, page_w, page_h)


def test_detect_anchor_presence_false_for_x_marker() -> None:
    page_h = 300
    page_w = 300
    image = np.full((page_h, page_w), 255, dtype=np.uint8)
    anchor = _anchor_region()

    x0 = int(round(anchor.x_norm * page_w))
    y0 = int(round(anchor.y_norm * page_h))
    x1 = int(round((anchor.x_norm + anchor.w_norm) * page_w))
    y1 = int(round((anchor.y_norm + anchor.h_norm) * page_h))

    # Border
    image[y0 : y0 + 2, x0:x1] = 0
    image[y1 - 2 : y1, x0:x1] = 0
    image[y0:y1, x0 : x0 + 2] = 0
    image[y0:y1, x1 - 2 : x1] = 0

    # X shape
    h = y1 - y0
    w = x1 - x0
    diag = min(h, w)
    ys = np.arange(diag)
    image[y0 + ys, x0 + ys] = 0
    image[y0 + ys, x1 - 1 - ys] = 0

    assert not detect_anchor_presence(image, anchor, page_w, page_h)


def test_anchor_bbox_map_norm_matches_expected_letter_geometry() -> None:
    bbox_map = anchor_bbox_map_norm()
    assert bbox_map["tl"] == [0.039, 0.03, 0.015, 0.011]
    assert bbox_map["tr"] == [0.946, 0.03, 0.015, 0.011]
    assert bbox_map["bl"] == [0.039, 0.958, 0.015, 0.011]
    assert bbox_map["br"] == [0.946, 0.958, 0.015, 0.011]
