from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from opengrader.core.ocr.ink_extraction import (
    isolate_student_ink,
    register_template_page,
)


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def test_template_subtraction_keeps_far_apart_student_words_and_drops_specks() -> None:
    template = Image.new("RGB", (900, 500), "white")
    draw = ImageDraw.Draw(template)
    draw.text((50, 40), "3. Explain the result.", fill="black", font=_font(34))
    draw.line((40, 450, 860, 450), fill="black", width=3)

    scan = template.copy()
    scan_draw = ImageDraw.Draw(scan)
    scan_draw.text((80, 170), "yes", fill=(100, 100, 100), font=_font(48))
    scan_draw.text((650, 340), "no", fill=(115, 115, 115), font=_font(48))
    scan_draw.point((500, 250), fill="black")

    result = isolate_student_ink(scan, template, dpi=300)

    assert len(result.fragments) == 2
    assert result.fragments[0].bbox_px[0] < 200
    assert result.fragments[1].bbox_px[0] > 600
    isolated = np.asarray(result.image.convert("L"))
    assert int((isolated[35:100, 45:450] < 245).sum()) == 0
    assert int((isolated[160:240, 70:250] < 245).sum()) > 100


def test_scan_frame_is_not_reported_as_student_ink() -> None:
    template = Image.new("RGB", (800, 1000), "white")
    scan_array = np.full((1000, 800, 3), 255, dtype=np.uint8)
    cv2.line(scan_array, (15, 10), (15, 980), (0, 0, 0), 5)
    cv2.line(scan_array, (15, 980), (780, 980), (0, 0, 0), 5)
    cv2.putText(
        scan_array,
        "answer",
        (250, 450),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.2,
        (80, 80, 80),
        3,
    )

    result = isolate_student_ink(Image.fromarray(scan_array), template, dpi=300)

    assert len(result.fragments) == 1


def test_filled_choice_survives_inside_a_printed_box() -> None:
    template = Image.new("RGB", (500, 400), "white")
    template_draw = ImageDraw.Draw(template)
    template_draw.rectangle((200, 150, 250, 200), outline="black", width=4)

    scan = template.copy()
    scan_draw = ImageDraw.Draw(scan)
    scan_draw.rectangle((211, 161, 239, 189), fill=(80, 80, 80))

    result = isolate_student_ink(scan, template, dpi=300)

    assert len(result.fragments) == 1
    x, y, width, height = result.fragments[0].bbox_px
    assert x >= 205
    assert y >= 155
    assert width >= 20
    assert height >= 20


def test_page_registration_recovers_a_perspective_scan() -> None:
    template = Image.new("RGB", (900, 1100), "white")
    draw = ImageDraw.Draw(template)
    for row in range(8):
        draw.text(
            (60, 70 + row * 115),
            f"Question {row + 1}: alpha beta {row * 17}",
            fill="black",
            font=_font(30),
        )
        draw.rectangle((50, 55 + row * 115, 850, 145 + row * 115), outline="black")

    source = np.asarray(template)
    transform = cv2.getPerspectiveTransform(
        np.float32([[0, 0], [899, 0], [0, 1099], [899, 1099]]),
        np.float32([[18, 12], [875, 4], [7, 1080], [890, 1092]]),
    )
    scan = Image.fromarray(
        cv2.warpPerspective(
            source,
            transform,
            (900, 1100),
            borderValue=(255, 255, 255),
        )
    )

    registered = register_template_page(scan, template)

    assert registered.metrics.inliers >= 20
    assert registered.metrics.median_error_px < 3.0
    aligned = np.asarray(registered.aligned_template.convert("L"), dtype=np.int16)
    scan_gray = np.asarray(scan.convert("L"), dtype=np.int16)
    assert float(np.mean(np.abs(aligned - scan_gray))) < 12.0
