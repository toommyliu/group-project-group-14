from pathlib import Path

import fitz

from opengrader.core.ocr import extractor
from opengrader.core.ocr.extractor import OcrConfig, process_submission_pdf
from opengrader.core.pdf.templates.constants import LETTER_H, LETTER_W


def _create_blank_pdf(path: Path) -> None:
    doc = fitz.open()
    doc.new_page(width=LETTER_W, height=LETTER_H)
    doc.save(path)
    doc.close()


def test_process_submission_pdf_extracts_detected_questions(tmp_path: Path) -> None:
    pdf_path = tmp_path / "submission.pdf"
    _create_blank_pdf(pdf_path)

    def _fake_detect(*_args: object, **_kwargs: object) -> list[extractor._DetectedQuestionRegion]:
        return [
            extractor._DetectedQuestionRegion(
                page_number=1,
                question_number=1,
                bbox_norm=[0.1, 0.2, 0.8, 0.2],
                continuation=None,
            ),
            extractor._DetectedQuestionRegion(
                page_number=1,
                question_number=2,
                bbox_norm=[0.1, 0.5, 0.8, 0.2],
                continuation=None,
            ),
        ]

    original_detect = extractor._detect_question_regions
    extractor._detect_question_regions = _fake_detect
    try:
        result = process_submission_pdf(
            pdf_path=pdf_path,
            config=OcrConfig(),
            output_dir=tmp_path,
            response_format="png",
            dpi=72,
        )
    finally:
        extractor._detect_question_regions = original_detect

    extracted_ids = [question.question_id for question in result.questions]
    assert extracted_ids == ["q1", "q2"]

    for question in result.questions:
        assert question.output_path is not None
        assert Path(question.output_path).exists()
