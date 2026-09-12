import fitz
import pytest
from PIL import Image

from opengrader.core.qa_file import pdf_answer_key
from opengrader.core.qa_file.pdf_answer_key import (
    _ocr_looks_unreliable,
    _text_from_region,
    extract_content,
)


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
        doc, rendered, bboxes, max_q=1, ollama_config=None,
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

    def _record_vlm(**_kwargs):
        calls.append("vlm")
        return ("VLM PROMPT", "VLM ANSWER")

    monkeypatch.setattr(pdf_answer_key, "extract_question_prompt_and_answer", _record_vlm)
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
        ollama_config=object(),
    )

    assert calls == []
    assert "- 4" in "\n".join(blocks)


def test_weak_ocr_escalates_to_the_vlm(monkeypatch) -> None:
    calls: list[str] = []

    def _record_vlm(**_kwargs):
        calls.append("vlm")
        return ("What is 2+2?", "four")

    monkeypatch.setattr(pdf_answer_key, "extract_question_prompt_and_answer", _record_vlm)
    monkeypatch.setattr(pdf_answer_key.pytesseract, "image_to_string", lambda *_a, **_k: "")
    monkeypatch.setattr(pdf_answer_key, "_ocr_mean_confidence", lambda _img: 0.0)

    doc = _blank_scan_doc()
    rendered = {1: Image.new("RGB", (612, 792), "white")}
    blocks = extract_content(
        doc, rendered, {1: (1, (0.0, 0.0, 1.0, 0.5))}, max_q=1,
        ollama_config=object(),
    )

    assert calls == ["vlm"]
    assert "four" in "\n".join(blocks)
