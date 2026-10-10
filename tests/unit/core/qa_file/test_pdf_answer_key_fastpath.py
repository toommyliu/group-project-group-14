import fitz
import pytest
from PIL import Image

from opengrader.core.qa_file import pdf_answer_key
from opengrader.core.qa_file.pdf_answer_key import (
    _ocr_looks_unreliable,
    _text_from_region,
    extract_content,
)


class _StubVisionReader:
    """Minimal VisionReader stub recording calls for the escalation tests.

    `pdf_answer_key.extract_content` calls `extract_question_prompt_and_answer`
    on whatever reader it is given, rather than a module-level function, so
    these tests inject this stub as `vision_reader=` instead of monkeypatching
    a module attribute that no longer exists.
    """

    def __init__(self, calls: list[str], answer: tuple[str, str]) -> None:
        self._calls = calls
        self._answer = answer

    def extract_question_prompt_and_answer(self, *, image_path, math_latex=False):
        self._calls.append("vlm")
        return self._answer


def _blank_scan_doc() -> fitz.Document:
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    return doc


def _born_digital_doc() -> fitz.Document:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "1. (5 points) What is 2+2? Solution: 4")
    return doc


def test_text_from_region_reads_born_digital_and_empty_for_scan() -> None:
    doc = _born_digital_doc()
    text = _text_from_region(doc, 1, (0.0, 0.05, 1.0, 0.25))
    assert "What is 2+2?" in text and "Solution: 4" in text

    blank = fitz.open()
    blank.new_page(width=612, height=792)
    assert _text_from_region(blank, 1, (0.0, 0.0, 1.0, 1.0)) == ""


def test_extract_content_uses_text_layer_and_skips_ocr(monkeypatch) -> None:
    def _boom(*_args, **_kwargs):
        raise AssertionError("pytesseract was called on a born-digital region")

    monkeypatch.setattr(pdf_answer_key.pytesseract, "image_to_string", _boom)

    doc = _born_digital_doc()
    rendered = {1: Image.new("RGB", (612, 792), "white")}
    bboxes = {1: (1, (0.0, 0.05, 1.0, 0.25))}

    seen: list[tuple[int, int]] = []
    blocks = extract_content(
        doc, rendered, bboxes, max_q=1, vision_reader=None,
        progress=lambda cur, total: seen.append((cur, total)),
    )

    assert seen == [(1, 1)]
    joined = "\n".join(blocks)
    assert "(5 points) What is 2+2?" in joined
    assert "- 4" in joined


def test_ocr_looks_unreliable_signals() -> None:
    assert _ocr_looks_unreliable("", 95.0) is True
    assert _ocr_looks_unreliable("ab", 95.0) is True
    assert _ocr_looks_unreliable("some garbled words here", 40.0) is True
    assert _ocr_looks_unreliable("a clean printed answer line", 85.0) is False


def test_confident_ocr_skips_the_vlm(monkeypatch) -> None:
    calls: list[str] = []
    reader = _StubVisionReader(calls, ("VLM PROMPT", "VLM ANSWER"))

    monkeypatch.setattr(
        pdf_answer_key.pytesseract,
        "image_to_string",
        lambda *_a, **_k: "1. (5 points) What is 2+2? Solution: 4",
    )
    monkeypatch.setattr(pdf_answer_key, "_ocr_mean_confidence", lambda _img: 90.0)

    doc = _blank_scan_doc()
    rendered = {1: Image.new("RGB", (612, 792), "white")}
    blocks = extract_content(
        doc, rendered, {1: (1, (0.0, 0.0, 1.0, 0.5))}, max_q=1,
        vision_reader=reader,
    )

    assert calls == []
    assert "- 4" in "\n".join(blocks)


def test_weak_ocr_escalates_to_the_vlm(monkeypatch) -> None:
    calls: list[str] = []
    reader = _StubVisionReader(calls, ("What is 2+2?", "four"))

    monkeypatch.setattr(pdf_answer_key.pytesseract, "image_to_string", lambda *_a, **_k: "")
    monkeypatch.setattr(pdf_answer_key, "_ocr_mean_confidence", lambda _img: 0.0)

    doc = _blank_scan_doc()
    rendered = {1: Image.new("RGB", (612, 792), "white")}
    blocks = extract_content(
        doc, rendered, {1: (1, (0.0, 0.0, 1.0, 0.5))}, max_q=1,
        vision_reader=reader,
    )

    assert calls == ["vlm"]
    assert "four" in "\n".join(blocks)


def _pointless_doc() -> fitz.Document:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "1. What is 2+2? Solution: 4")
    return doc


def _extract_one(doc, **options):  # noqa: ANN001, ANN003, ANN202
    rendered = {1: Image.new("RGB", (612, 792), "white")}
    bboxes = {1: (1, (0.0, 0.05, 1.0, 0.25))}
    return "\n".join(extract_content(doc, rendered, bboxes, max_q=1, **options))


def test_a_question_with_no_points_gets_the_standard_and_is_counted() -> None:
    defaulted: list[int] = []

    text = _extract_one(_pointless_doc(), defaulted=defaulted)

    assert "- (10 points) What is 2+2?" in text
    assert "    - Partially Correct (-5)\n    - Incorrect (-10)" in text
    assert defaulted == [1]


def test_the_blank_templates_points_are_used_before_the_default() -> None:
    defaulted: list[int] = []

    text = _extract_one(
        _pointless_doc(), template_points={1: 4.0}, default_points=6, defaulted=defaulted
    )

    assert "- (4 points) What is 2+2?" in text
    assert "    - Partially Correct (-2)\n    - Incorrect (-4)" in text
    assert defaulted == []


def test_points_read_on_the_answer_key_win_over_both() -> None:
    defaulted: list[int] = []

    text = _extract_one(
        _born_digital_doc(), template_points={1: 4.0}, default_points=6, defaulted=defaulted
    )

    assert "- (5 points) What is 2+2?" in text
    assert defaulted == []


def test_a_question_that_was_not_found_still_gets_the_chosen_default() -> None:
    defaulted: list[int] = []
    blocks = extract_content(
        _blank_scan_doc(), {}, {}, max_q=1, default_points=6, defaulted=defaulted
    )

    assert blocks[0].startswith("- (6 points) [Question 1 - Text not detected]")
    assert blocks[0].endswith("    - Partially Correct (-3)\n    - Incorrect (-6)")
    assert defaulted == [1]
