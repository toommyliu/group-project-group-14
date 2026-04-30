from opengrader.core.ocr import layout


PAGE_W = 1000
PAGE_H = 1400


def _line(
    text: str,
    bbox: tuple[int, int, int, int],
    *,
    confidence: float = 92.0,
    line_num: int = 1,
) -> layout.PageTextLine:
    x0, y0, x1, y1 = bbox
    tokens = text.split()
    token_width = max(1, (x1 - x0) // max(1, len(tokens)))
    words = []
    for index, token in enumerate(tokens):
        wx0 = x0 + index * token_width
        wx1 = x1 if index == len(tokens) - 1 else min(x1, wx0 + token_width - 4)
        words.append(
            layout.PageTextWord(
                text=token,
                confidence=confidence,
                bbox_px=(wx0, y0, wx1, y1),
                line_id=(1, 1, line_num),
            )
        )
    return layout.PageTextLine(
        text=text,
        confidence=confidence,
        bbox_px=bbox,
        words=tuple(words),
    )


def _field(
    field_id: str,
    bbox: list[float],
) -> layout.DetectedFieldRegion:
    return layout.DetectedFieldRegion(
        field_id=field_id,  # type: ignore[arg-type]
        field_kind="name" if field_id == "student_name" else "sid",
        page_number=1,
        crop_bbox_norm=bbox,
        anchor_bbox_norm=None,
        confidence=90.0,
    )


def test_detect_title_region_accepts_top_left_compact_title() -> None:
    title = _line("cmpe 30 quiz 2_5a", (50, 55, 310, 82), line_num=1)
    name = _line("Name Taylor Student", (360, 150, 760, 178), line_num=2)
    sid = _line("Student ID 012345678", (360, 218, 760, 246), line_num=3)

    region = layout._detect_title_region(
        [title, name, sid],
        page_w=PAGE_W,
        page_h=PAGE_H,
        first_question_y_px=330,
        fields=[
            _field("student_name", [0.34, 0.10, 0.46, 0.045]),
            _field("student_id", [0.34, 0.15, 0.46, 0.045]),
        ],
    )

    assert region is not None
    x, y, w, h = region.crop_bbox_norm
    assert x <= 0.05
    assert y <= 0.04
    assert x + w >= 0.31
    assert y + h < 0.10


def test_detect_title_region_splits_title_before_same_line_fields() -> None:
    mixed_header = _line(
        "CMPE 30 Quiz 2_5a (100 points) ID: 123456789 Name: Taylor Student",
        (165, 120, 855, 155),
        line_num=1,
    )
    instructions = _line(
        "No notes calculators or other aids allowed. Read all directions carefully.",
        (170, 215, 885, 250),
        confidence=96.0,
        line_num=2,
    )

    region = layout._detect_title_region(
        [mixed_header, instructions],
        page_w=PAGE_W,
        page_h=PAGE_H,
        first_question_y_px=330,
        fields=[
            _field("student_id", [0.43, 0.08, 0.20, 0.05]),
            _field("student_name", [0.64, 0.08, 0.25, 0.05]),
        ],
    )

    assert region is not None
    x, y, w, h = region.crop_bbox_norm
    assert x <= 0.17
    assert x + w < 0.58
    assert y <= 0.09
    assert y + h < 0.13


def test_detect_title_region_keeps_centered_generated_title() -> None:
    title = _line("CS146: Quiz 1 (A)", (360, 120, 640, 152), line_num=1)
    name = _line("Name", (335, 195, 390, 220), line_num=2)

    region = layout._detect_title_region(
        [title, name],
        page_w=PAGE_W,
        page_h=PAGE_H,
        first_question_y_px=420,
        fields=[_field("student_name", [0.32, 0.135, 0.38, 0.05])],
    )

    assert region is not None
    x, y, w, h = region.crop_bbox_norm
    assert x < 0.36
    assert x + w > 0.64
    assert y <= 0.09
    assert y + h < 0.13


def test_detect_title_region_does_not_promote_first_question_prompt() -> None:
    review_reasons: list[str] = []
    question = _line("1. Explain your answer.", (85, 120, 440, 150), line_num=1)

    region = layout._detect_title_region(
        [question],
        page_w=PAGE_W,
        page_h=PAGE_H,
        first_question_y_px=120,
        fields=[],
        review_reasons=review_reasons,
    )

    assert region is not None
    assert region.confidence == 35.0
    assert "title_region_fallback_header" in review_reasons
