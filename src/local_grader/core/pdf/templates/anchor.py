from enum import Enum

class Anchor(Enum):
    TopLeft = "tl"
    TopRight = "tr"
    Bottomleft = "bl"
    BottomRight = "br"

ANCHOR_LOCATIONS = set(a.value for a in Anchor)