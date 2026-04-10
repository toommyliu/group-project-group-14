from pathlib import Path

import fitz

from opengrader.core.ocr.extractor import OcrConfig, process_submission_pdf
from opengrader.core.pdf.templates.constants import QuestionType, LETTER_W, LETTER_H


def _create_blank_pdf(path: Path) -> None:
    doc = fitz.open()
    doc.new_page(width=LETTER_W, height=LETTER_H)
    doc.save(path)
    doc.close()


def test_process_submission_pdf_extracts_subquestions(tmp_path: Path) -> None:
    pdf_path = tmp_path / "submission.pdf"
    _create_blank_pdf(pdf_path)

    spec = {
        "schema_version": "1.0",
        "unit_id": "unit-1",
        "pages": [
            {
                "page_number": 1,
                "anchors": [],
                "fields": [],
                "questions": [
                    {
                        "id": "q1",
                        "bbox": [0.1, 0.1, 0.8, 0.45],
                        "response_type": QuestionType.FREE_RESPONSE.value,
                        "points": 20,
                        "subquestions": [
                            {
                                "id": "q1a",
                                "bbox": [0.12, 0.18, 0.76, 0.18],
                                "response_type": QuestionType.FREE_RESPONSE.value,
                                "points": 10,
                            },
                            {
                                "id": "q1b",
                                "bbox": [0.12, 0.38, 0.76, 0.15],
                                "response_type": QuestionType.FREE_RESPONSE.value,
                                "points": 10,
                            },
                        ],
                    },
                    {
                        "id": "q2",
                        "bbox": [0.1, 0.6, 0.8, 0.25],
                        "response_type": QuestionType.FREE_RESPONSE.value,
                        "points": 10,
                    },
                ],
            }
        ],
    }

    result = process_submission_pdf(
        pdf_path=pdf_path,
        spec=spec,
        config=OcrConfig(),
        output_dir=tmp_path,
        response_format="png",
        dpi=72,
    )

    extracted_ids = [question.question_id for question in result.questions]
    assert extracted_ids == ["q1a", "q1b", "q2"]

    for question in result.questions:
        assert question.output_path is not None
        assert Path(question.output_path).exists()
