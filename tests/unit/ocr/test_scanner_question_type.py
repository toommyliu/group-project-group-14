from PIL import Image, ImageDraw

from opengrader.core.ocr import scanner
from opengrader.core.question_types import FREE_RESPONSE, MULTIPLE_CHOICE


def _template_with_regions() -> Image.Image:
    image = Image.new("RGB", (1000, 1400), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 50, 470, 130), outline=(0, 0, 0), width=5)
    draw.rectangle((80, 260, 920, 620), outline=(0, 0, 0), width=5)
    draw.text((130, 360), "A) Stack", fill=(0, 0, 0))
    draw.text((130, 430), "B) Queue", fill=(0, 0, 0))
    draw.rectangle((80, 720, 920, 1080), outline=(0, 0, 0), width=5)
    draw.text((130, 820), "Explain your answer.", fill=(0, 0, 0))
    return image


def test_scan_template_sets_question_type_for_question_regions() -> None:
    original_classify = scanner.classify_question_image
    call_count = {"count": 0}

    def _fake_classify(_image: Image.Image) -> str:
        call_count["count"] += 1
        return MULTIPLE_CHOICE if call_count["count"] == 1 else FREE_RESPONSE

    scanner.classify_question_image = _fake_classify
    try:
        regions = scanner.scan_template(_template_with_regions())
    finally:
        scanner.classify_question_image = original_classify

    header_regions = [r for r in regions if r.region_type == "header_field"]
    question_regions = [r for r in regions if r.region_type == "question"]

    assert header_regions
    assert all(region.question_type is None for region in header_regions)
    assert [region.question_type for region in question_regions] == [
        MULTIPLE_CHOICE,
        FREE_RESPONSE,
    ]
