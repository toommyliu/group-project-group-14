import hashlib
import io
from pathlib import Path

import cv2
import fitz
import numpy as np
from PIL import Image

OVERLAY_WIDTH = 1100
OVERLAY_SIGNATURE = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_TILES = (16, 6)
_REACH = 0.03
_PRINTED = 0.35
_ABOVE_PAPER = 0.15
_SPECK = 20
_TOLERANCE = 7
_MARGIN = 0.01
_SHARED = 0.75
_SHARED_MINIMUM = 3
_RAMP = np.array([(245, 158, 11), (220, 38, 38), (127, 29, 29)], dtype=np.float32)


def _darkness(page: fitz.Page, size: tuple[int, int]) -> np.ndarray:
    scale = fitz.Matrix(size[0] / page.rect.width, size[1] / page.rect.height)
    pixmap = page.get_pixmap(matrix=scale, colorspace=fitz.csRGB, alpha=False)
    rgb = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, 3)
    rgb = cv2.resize(rgb, size, interpolation=cv2.INTER_AREA)
    return 1 - rgb.min(axis=2).astype(np.float32) / 255


def _printed_on(scan: np.ndarray, template: np.ndarray, printed: np.ndarray) -> np.ndarray:
    height, width = template.shape
    small_scan = cv2.resize(scan, (width // 2, height // 2), interpolation=cv2.INTER_AREA)
    small_template = cv2.resize(template, (width // 2, height // 2), interpolation=cv2.INTER_AREA)
    reach = max(1, int(width // 2 * _REACH))
    padded = cv2.copyMakeBorder(small_scan, reach, reach, reach, reach, cv2.BORDER_CONSTANT, value=0)
    placed = np.zeros_like(printed)
    rows, columns = _TILES
    for row in range(rows):
        for column in range(columns):
            y0, y1 = row * height // rows, (row + 1) * height // rows
            x0, x1 = column * width // columns, (column + 1) * width // columns
            if not printed[y0:y1, x0:x1].any():
                continue
            tile = small_template[y0 // 2 : y1 // 2, x0 // 2 : x1 // 2]
            if tile.size == 0:
                continue
            region = padded[y0 // 2 : y1 // 2 + 2 * reach, x0 // 2 : x1 // 2 + 2 * reach]
            scores = cv2.matchTemplate(region, tile, cv2.TM_CCOEFF_NORMED)
            _, _, _, (best_x, best_y) = cv2.minMaxLoc(np.nan_to_num(scores, nan=-1))
            dy, dx = 2 * (best_y - reach), 2 * (best_x - reach)
            ty0, ty1, tx0, tx1 = max(y0 + dy, 0), min(y1 + dy, height), max(x0 + dx, 0), min(x1 + dx, width)
            if ty0 < ty1 and tx0 < tx1:
                placed[ty0:ty1, tx0:tx1] |= printed[ty0 - dy : ty1 - dy, tx0 - dx : tx1 - dx]
    return cv2.dilate(placed.astype(np.uint8), np.ones((_TOLERANCE, _TOLERANCE), np.uint8)) > 0


def _writing(scan: np.ndarray, template: np.ndarray, printed: np.ndarray) -> np.ndarray:
    ink = scan > float(np.median(scan)) + _ABOVE_PAPER
    ink &= ~_printed_on(scan, template, printed)
    height, width = ink.shape
    margin_y, margin_x = int(height * _MARGIN), int(width * _MARGIN)
    ink[:margin_y], ink[height - margin_y :], ink[:, :margin_x], ink[:, width - margin_x :] = 0, 0, 0, 0
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    keep = stats[:, cv2.CC_STAT_AREA] >= _SPECK
    keep[0] = False
    return keep[labels]


def writing_overlay(
    template: Path, submissions: list[Path], page: int, width: int = OVERLAY_WIDTH
) -> bytes:
    with fitz.open(template) as document:
        printed_page = document[page - 1]
        size = (width, round(width * printed_page.rect.height / printed_page.rect.width))
        template_darkness = _darkness(printed_page, size)
    printed = template_darkness > _PRINTED
    shape = printed.shape
    marks = []
    for path in submissions:
        with fitz.open(path) as document:
            if len(document) < page:
                continue
            scan = _darkness(document[page - 1], size)
        marks.append(np.packbits(_writing(scan, template_darkness, printed)))
    shared = np.zeros(printed.size, dtype=np.uint32)
    for mark in marks:
        shared += np.unpackbits(mark, count=printed.size)
    counts = shared.reshape(shape)
    if len(marks) >= _SHARED_MINIMUM:
        counts = np.where(counts >= _SHARED * len(marks), 0, counts)
    counts = cv2.dilate(counts.astype(np.float32), np.ones((3, 3), np.uint8))
    share = counts / max(len(marks), 1)
    position = np.clip(share * 2, 0, 2)
    lower = np.minimum(position.astype(int), 1)
    blend = (position - lower)[..., None]
    pixels = np.zeros((*shape, 4), dtype=np.uint8)
    pixels[..., :3] = (_RAMP[lower] * (1 - blend) + _RAMP[lower + 1] * blend).astype(np.uint8)
    pixels[..., 3] = np.where(counts > 0, 215 + 40 * share, 0).astype(np.uint8)
    output = io.BytesIO()
    Image.fromarray(pixels, "RGBA").save(output, format="PNG", optimize=True)
    return output.getvalue()
