from pathlib import Path

from PIL import Image, ImageDraw

from opengrader.core.ocr.extractor import (
    FieldExtractionResult,
    OcrConfig,
    OcrResult,
    QuestionExtractionResult,
    SubmissionResult,
)
from opengrader.core.submissions import student_file


def _draw_subquestion_boxes(path: Path) -> None:
    image = Image.new("RGB", (1200, 1600), color=(255, 255, 255))
    draw = ImageDraw.Draw(image)

    draw.rectangle((70, 70, 1130, 1520), outline=(0, 0, 0), width=5)
    draw.rectangle((95, 250, 1105, 810), outline=(0, 0, 0), width=5)
    draw.rectangle((95, 860, 1105, 1450), outline=(0, 0, 0), width=5)

    image.save(path)


def test_split_question_response_image_splits_nested_subquestion_boxes(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "q2.png"
    _draw_subquestion_boxes(image_path)

    split = student_file._split_question_response_image(
        question_id="q2",
        image_path=image_path,
    )

    assert [question_id for question_id, _ in split] == ["q2a", "q2b"]
    assert all(path.exists() for _, path in split)


def test_split_question_response_image_falls_back_to_ocr_markers(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "q6.png"
    Image.new("RGB", (1000, 2400), color=(255, 255, 255)).save(image_path)

    def _fake_safe_image_to_data(*_args: object, **_kwargs: object) -> dict[str, list]:
        return {
            "text": ["a)", "b)", "c)", "d)", "ignore"],
            "conf": [96.0, 95.0, 94.0, 93.0, 80.0],
            "left": [120, 120, 120, 120, 800],
            "top": [160, 760, 1360, 1960, 1700],
            "width": [30, 30, 30, 30, 100],
            "height": [28, 28, 28, 28, 30],
            "block_num": [1, 1, 1, 1, 1],
            "par_num": [1, 1, 1, 1, 1],
            "line_num": [1, 2, 3, 4, 5],
        }

    original_detect_boxes = student_file._detect_subquestion_boxes_norm
    original_safe_image_to_data = student_file._safe_image_to_data
    try:
        student_file._detect_subquestion_boxes_norm = lambda _image: []
        student_file._safe_image_to_data = _fake_safe_image_to_data

        split = student_file._split_question_response_image(
            question_id="q6",
            image_path=image_path,
        )

        assert [question_id for question_id, _ in split] == ["q6a", "q6b", "q6c", "q6d"]
        assert all(path.exists() for _, path in split)
    finally:
        student_file._detect_subquestion_boxes_norm = original_detect_boxes
        student_file._safe_image_to_data = original_safe_image_to_data


def test_build_student_submission_document_respects_split_subquestions_flag(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "out"
    response_dir = output_dir / "responses" / "student-0001"
    response_dir.mkdir(parents=True, exist_ok=True)

    q1 = response_dir / "q1.png"
    q1a = response_dir / "q1a.png"
    q1b = response_dir / "q1b.png"

    Image.new("RGB", (200, 120), color=(255, 255, 255)).save(q1)
    Image.new("RGB", (200, 90), color=(255, 255, 255)).save(q1a)
    Image.new("RGB", (200, 90), color=(255, 255, 255)).save(q1b)

    def _fake_process_submission_pdf(*_args: object, **_kwargs: object) -> SubmissionResult:
        return SubmissionResult(
            filename="student-0001.pdf",
            version_validated=True,
            fields=[
                FieldExtractionResult(
                    field_id="student_name",
                    ocr_result=OcrResult(text="Jane Student", confidence=99.0, bbox_px=(0, 0, 0, 0)),
                    validated=True,
                ),
                FieldExtractionResult(
                    field_id="student_id",
                    ocr_result=OcrResult(text="123456789", confidence=99.0, bbox_px=(0, 0, 0, 0)),
                    validated=True,
                ),
            ],
            questions=[
                QuestionExtractionResult(
                    question_id="q1",
                    page_number=1,
                    output_path=str(q1),
                )
            ],
        )

    split_call_count = {"count": 0}

    def _fake_split_question_response_image(
        *,
        question_id: str,
        image_path: Path,
    ) -> list[tuple[str, Path]]:
        split_call_count["count"] += 1
        assert question_id == "q1"
        assert image_path == q1
        return [("q1a", q1a), ("q1b", q1b)]

    def _fake_ocr_image_text(path: str | Path, _config: OcrConfig) -> str:
        return Path(path).stem

    original_process = student_file.process_submission_pdf
    original_extract_title = student_file.extract_title_text_from_submission
    original_ocr_image_text = student_file.ocr_image_text
    original_split = student_file._split_question_response_image
    try:
        student_file.process_submission_pdf = _fake_process_submission_pdf
        student_file.extract_title_text_from_submission = (
            lambda *_args, **_kwargs: "CMPE 195: Homework 1 (A)"
        )
        student_file.ocr_image_text = _fake_ocr_image_text
        student_file._split_question_response_image = _fake_split_question_response_image

        split_result = student_file.build_student_submission_document(
            pdf_path=tmp_path / "student-0001.pdf",
            output_dir=output_dir,
            ocr_config=OcrConfig(),
            split_subquestions=True,
        )

        assert [q.question_id for q in split_result.document.questions] == ["q1a", "q1b"]
        assert split_call_count["count"] == 1

        split_call_count["count"] = 0
        no_split_result = student_file.build_student_submission_document(
            pdf_path=tmp_path / "student-0001.pdf",
            output_dir=output_dir,
            ocr_config=OcrConfig(),
            split_subquestions=False,
        )

        assert [q.question_id for q in no_split_result.document.questions] == ["q1"]
        assert split_call_count["count"] == 0
    finally:
        student_file.process_submission_pdf = original_process
        student_file.extract_title_text_from_submission = original_extract_title
        student_file.ocr_image_text = original_ocr_image_text
        student_file._split_question_response_image = original_split
