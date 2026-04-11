from enum import Enum

from .constants import ANCHOR_PADDING, ANCHOR_SIZE, LETTER_H, LETTER_W


class Anchor(str, Enum):
    TopLeft = "tl"
    TopRight = "tr"
    Bottomleft = "bl"
    BottomRight = "br"


ANCHOR_LOCATIONS = set(Anchor)


def anchor_bbox_map_norm(
    page_w: float = LETTER_W,
    page_h: float = LETTER_H,
    precision: int = 3,
) -> dict[str, list[float]]:
    margin_x = ANCHOR_PADDING / page_w
    margin_y = ANCHOR_PADDING / page_h
    size_x = ANCHOR_SIZE / page_w
    size_y = ANCHOR_SIZE / page_h

    return {
        Anchor.TopLeft: [
            round(margin_x, precision),
            round(margin_y, precision),
            round(size_x, precision),
            round(size_y, precision),
        ],
        Anchor.TopRight: [
            round(1.0 - margin_x - size_x, precision),
            round(margin_y, precision),
            round(size_x, precision),
            round(size_y, precision),
        ],
        Anchor.Bottomleft: [
            round(margin_x, precision),
            round(1.0 - margin_y - size_y, precision),
            round(size_x, precision),
            round(size_y, precision),
        ],
        Anchor.BottomRight: [
            round(1.0 - margin_x - size_x, precision),
            round(1.0 - margin_y - size_y, precision),
            round(size_x, precision),
            round(size_y, precision),
        ],
    }


def anchor_entries_norm(
    page_w: float = LETTER_W,
    page_h: float = LETTER_H,
    precision: int = 3,
) -> list[dict[str, list[float] | str]]:
    bbox_map = anchor_bbox_map_norm(page_w=page_w, page_h=page_h, precision=precision)
    return [
        {"id": Anchor.TopLeft, "bbox": bbox_map[Anchor.TopLeft]},
        {"id": Anchor.TopRight, "bbox": bbox_map[Anchor.TopRight]},
        {"id": Anchor.Bottomleft, "bbox": bbox_map[Anchor.Bottomleft]},
        {"id": Anchor.BottomRight, "bbox": bbox_map[Anchor.BottomRight]},
    ]
