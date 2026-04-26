from PIL import Image, ImageDraw

from opengrader.core.ocr import extractor


def test_label_to_field_bbox_returns_wide_horizontal_slice() -> None:
    page_w = 2200
    page_h = 3400
    label_bbox = (140, 220, 360, 300)
    # Value box to the right of the label.
    boxes_norm = [[0.22, 0.064, 0.58, 0.032]]

    x0, y0, x1, y1 = extractor._label_to_field_bbox_px(
        label_bbox,
        page_w,
        page_h,
        boxes_norm,
    )

    assert x0 <= int(page_w * 0.10)
    assert x1 >= int(page_w * 0.82)
    assert y0 <= label_bbox[1] - 8
    assert y1 > label_bbox[3]
    assert int(page_w * 0.42) <= (x1 - x0) <= int(page_w * 0.84)


def test_value_bbox_to_field_slice_enforces_minimum_width() -> None:
    page_w = 1800
    page_h = 3000
    tiny_value_bbox = (620, 260, 760, 300)

    x0, y0, x1, y1 = extractor._value_bbox_to_field_slice_px(
        tiny_value_bbox,
        page_w=page_w,
        page_h=page_h,
    )

    assert x1 - x0 >= int(page_w * 0.42)
    assert y1 > y0


def test_detect_dynamic_field_regions_falls_back_to_line_parsing() -> None:
    page_w = 2000
    page_h = 3000
    words = [
        extractor._OcrWord(
            text="Name:",
            conf=95.0,
            left=120,
            top=160,
            width=120,
            height=40,
            line_id=(1, 1, 1),
        ),
        extractor._OcrWord(
            text="Alice",
            conf=93.0,
            left=320,
            top=160,
            width=180,
            height=42,
            line_id=(1, 1, 1),
        ),
        extractor._OcrWord(
            text="Student",
            conf=96.0,
            left=120,
            top=240,
            width=180,
            height=40,
            line_id=(1, 1, 2),
        ),
        extractor._OcrWord(
            text="ID:",
            conf=94.0,
            left=320,
            top=240,
            width=80,
            height=40,
            line_id=(1, 1, 2),
        ),
        extractor._OcrWord(
            text="012345678",
            conf=92.0,
            left=460,
            top=238,
            width=260,
            height=44,
            line_id=(1, 1, 2),
        ),
    ]

    regions = extractor._detect_dynamic_field_regions(words, page_w, page_h, boxes_norm=[])

    assert "name" in regions
    assert "sid" in regions


def test_detect_dynamic_field_regions_uses_header_underlines_when_label_ocr_is_missing() -> None:
    page_w = 2000
    page_h = 3000
    image = Image.new("RGB", (page_w, page_h), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.line((820, 710, 1580, 710), fill=(0, 0, 0), width=4)
    draw.line((820, 910, 1580, 910), fill=(0, 0, 0), width=4)

    words = [
        extractor._OcrWord(
            text="Student",
            conf=96.0,
            left=520,
            top=860,
            width=170,
            height=38,
            line_id=(1, 1, 1),
        ),
        extractor._OcrWord(
            text="ID",
            conf=95.0,
            left=710,
            top=860,
            width=52,
            height=38,
            line_id=(1, 1, 1),
        ),
    ]

    regions = extractor._detect_dynamic_field_regions(
        words,
        page_w,
        page_h,
        boxes_norm=[],
        page_image=image,
    )

    assert "name" in regions
    assert "sid" in regions
    assert regions["name"][1] < regions["sid"][1]


def test_trim_question_region_preserves_answer_written_after_prompt() -> None:
    image = Image.new("RGB", (1000, 220), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)

    # Prompt-like content near the top, followed by a short same-line answer.
    for x0, x1 in [(40, 120), (135, 220), (235, 330), (345, 430), (445, 540)]:
        draw.rectangle((x0, 50, x1, 72), fill=(90, 90, 90))
    draw.line(
        (660, 43, 674, 43, 664, 66, 678, 66),
        fill=(115, 115, 115),
        width=5,
    )
    draw.ellipse((680, 41, 698, 69), outline=(115, 115, 115), width=5)

    trimmed = extractor._trim_question_region(image, margin_px=8)

    assert trimmed.width > 650
    assert trimmed.height < 80


def test_trim_question_region_ignores_long_border_lines() -> None:
    image = Image.new("RGB", (1400, 900), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)

    # Prompt-like content near the top.
    draw.rectangle((120, 60, 1180, 92), fill=(90, 90, 90))
    # Handwritten answer content.
    draw.rectangle((360, 210, 520, 310), fill=(120, 120, 120))
    # Full-width border lines that should not dominate trimming.
    draw.line((0, 840, 1399, 840), fill=(80, 80, 80), width=6)
    draw.line((8, 0, 8, 899), fill=(80, 80, 80), width=4)
    draw.line((1390, 0, 1390, 899), fill=(80, 80, 80), width=4)

    trimmed = extractor._trim_question_region(image, margin_px=8)

    assert trimmed.height < 420
    assert trimmed.width < 1250
    assert trimmed.height > 100
