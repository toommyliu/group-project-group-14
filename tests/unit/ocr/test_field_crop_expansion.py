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

    assert x0 <= int(page_w * 0.08)
    assert x1 >= int(page_w * 0.94)
    assert y0 <= label_bbox[1]
    assert y1 > label_bbox[3]
    assert (x1 - x0) >= int(page_w * 0.65)


def test_value_bbox_to_field_slice_enforces_minimum_width() -> None:
    page_w = 1800
    page_h = 3000
    tiny_value_bbox = (620, 260, 760, 300)

    x0, y0, x1, y1 = extractor._value_bbox_to_field_slice_px(
        tiny_value_bbox,
        page_w=page_w,
        page_h=page_h,
    )

    assert x1 - x0 >= int(page_w * 0.68)
    assert y1 > y0
