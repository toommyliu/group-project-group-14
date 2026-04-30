from pathlib import Path

import fitz

from opengrader.core.pdf.templates.constants import LETTER_H, PRINT_SAFE_MARGIN
from opengrader.core.pdf.templates import generate_template_pdf
from opengrader.core.qa_file import (
    parse_qa_markdown,
    qa_to_template_questions,
)


def _extract_pdf_text(path: Path) -> str:
    with fitz.open(path) as document:
        return "\n".join(page.get_text() for page in document)


def _find_text_bbox(page: fitz.Page, text: str) -> fitz.Rect:
    text_dict = page.get_text("dict")
    for block in text_dict["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if span["text"] == text:
                    return fitz.Rect(span["bbox"])
    raise AssertionError(f"Could not find text in PDF page: {text!r}")


def test_generate_template_pdf_renders_multiple_choice_options(tmp_path: Path) -> None:
    content = """
- (4 points) Which structure is FIFO?
  - **Choices**
    - A) Stack
    - B) Queue
    - C) Tree
    - D) Graph
  - **Correct**
    - B

- (4 points) Answer both parts.
  - (2 points) Pick the prime number.
    - **Choices**
      - A) 4
      - B) 5
      - C) 6
    - **Correct**
      - B
  - (2 points) Explain why.
    - **Correct**
      - 5 is only divisible by 1 and 5
"""

    document = parse_qa_markdown(content)
    output_path = tmp_path / "multiple-choice-template.pdf"

    generate_template_pdf(
        output_path=output_path,
        version="A",
        questions=qa_to_template_questions(document),
        title="Template Quiz",
    )

    extracted_text = _extract_pdf_text(output_path)
    assert "A)" in extracted_text
    assert "B)" in extracted_text
    assert "Stack" in extracted_text
    assert "Queue" in extracted_text
    assert "Pick the prime number." in extracted_text
    assert "5" in extracted_text


def test_continued_header_and_page_number_are_inside_printable_area(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "continued-header-template.pdf"
    continued_header = "Template Quiz Continued"

    generate_template_pdf(
        output_path=output_path,
        version="A",
        questions=[("q1", 1, "Explain the result.")],
        title="Template Quiz",
        continued_page_header=continued_header,
        start_questions_on_first_page=False,
    )

    with fitz.open(output_path) as document:
        continued_page = document[1]
        header_bbox = _find_text_bbox(continued_page, continued_header)
        page_number_bbox = _find_text_bbox(continued_page, "2")

    assert header_bbox.y0 >= PRINT_SAFE_MARGIN - 1
    assert page_number_bbox.y1 <= LETTER_H - PRINT_SAFE_MARGIN + 1
