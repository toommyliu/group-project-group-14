from pathlib import Path

import fitz

from opengrader.core.pdf.templates import generate_template_pdf
from opengrader.core.pdf.templates.qa_file import (
    parse_qa_markdown,
    qa_to_template_questions,
)


def _extract_pdf_text(path: Path) -> str:
    with fitz.open(path) as document:
        return "\n".join(page.get_text() for page in document)


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
