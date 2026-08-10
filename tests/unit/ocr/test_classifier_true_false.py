from __future__ import annotations

from PIL import Image, ImageDraw

from opengrader.core.ocr.question_classifier import classify_question_image
from opengrader.core.question_types import FREE_RESPONSE, MULTIPLE_CHOICE


def _blank(width: int = 1000, height: int = 1000) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (width, height), color=(255, 255, 255))
    return image, ImageDraw.Draw(image)


def test_stacked_true_false_words_classify_as_multiple_choice() -> None:
    image, draw = _blank()
    # True stacked above False, left-aligned in the answer region.
    draw.text((150, 300), "True", fill=(0, 0, 0))
    draw.text((150, 500), "False", fill=(0, 0, 0))
    assert classify_question_image(image) == MULTIPLE_CHOICE


def test_prose_mentioning_true_stays_free_response() -> None:
    image, draw = _blank()
    # A single "true" without a paired "false" must not trip the detector.
    draw.text((150, 300), "this is true", fill=(0, 0, 0))
    assert classify_question_image(image) == FREE_RESPONSE
