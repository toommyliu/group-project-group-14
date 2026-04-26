from pathlib import Path

import fitz

from opengrader.core.ocr import extractor
from opengrader.core.ocr.extractor import OcrConfig, OcrResult, process_submission_pdf
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


def test_match_box_for_marker_prefers_tighter_question_box() -> None:
    marker = extractor._QuestionMarker(
        page_number=1,
        question_number=1,
        x_px=120,
        y_px=400,
        conf=97.0,
        has_points_hint=True,
    )

    matched = extractor._match_box_for_marker(
        marker,
        page_w=1000,
        page_h=2000,
        boxes_norm=[
            [0.05, 0.10, 0.90, 0.82],
            [0.08, 0.18, 0.84, 0.20],
        ],
    )

    assert matched is not None
    assert matched[1] < 0.20
    assert matched[3] < 0.30


def test_detect_question_regions_uses_structural_boundary_when_next_marker_missing() -> None:
    def _fake_marker_extract(
        page_number: int,
        _words: list[extractor._OcrWord],
        _page_w: int,
        _page_h: int,
    ) -> list[extractor._QuestionMarker]:
        return [
            extractor._QuestionMarker(
                page_number=page_number,
                question_number=1,
                x_px=120,
                y_px=360,
                conf=95.0,
                has_points_hint=True,
            )
        ]

    original_marker_extract = extractor._extract_question_markers_for_page
    extractor._extract_question_markers_for_page = _fake_marker_extract
    try:
        regions = extractor._detect_question_regions(
            page_words={1: []},
            page_dims={1: (1000, 2000)},
            page_boxes={1: [[0.08, 0.16, 0.84, 0.22], [0.08, 0.49, 0.84, 0.22]]},
        )
    finally:
        extractor._extract_question_markers_for_page = original_marker_extract

    assert len(regions) == 1
    _, y, _, h = regions[0].bbox_norm
    assert (y + h) < 0.49


def test_process_submission_pdf_saves_field_crops_with_submission_and_hoisted_copy(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "submission.pdf"
    _create_blank_pdf(pdf_path)

    def _fake_dynamic_slices(
        _words: list[extractor._OcrWord],
        _page_w: int,
        _page_h: int,
        _boxes_norm: list[list[float]],
        *,
        page_image: object | None = None,
    ) -> dict[str, dict[str, tuple[int, int, int, int]]]:
        _ = page_image
        return {
            "name": {"primary": (20, 30, 360, 95)},
            "sid": {"primary": (20, 120, 360, 185)},
        }

    def _fake_dynamic_ocr(
        _page_image: object,
        _bbox_px: tuple[int, int, int, int],
        _base_config: OcrConfig,
        field: dict,
    ) -> OcrResult:
        if field.get("type") == "sid":
            return OcrResult(text="123456789", confidence=99.0, bbox_px=(0, 0, 0, 0))
        return OcrResult(text="Jamie Student", confidence=99.0, bbox_px=(0, 0, 0, 0))

    original_detect_questions = extractor._detect_question_regions
    original_dynamic_slices = extractor._detect_dynamic_field_slices
    original_dynamic_ocr = extractor._ocr_field_from_dynamic_region
    try:
        extractor._detect_question_regions = lambda *_args, **_kwargs: []
        extractor._detect_dynamic_field_slices = _fake_dynamic_slices
        extractor._ocr_field_from_dynamic_region = _fake_dynamic_ocr

        result = process_submission_pdf(
            pdf_path=pdf_path,
            config=OcrConfig(),
            output_dir=tmp_path,
            response_format="png",
            dpi=72,
        )
    finally:
        extractor._detect_question_regions = original_detect_questions
        extractor._detect_dynamic_field_slices = original_dynamic_slices
        extractor._ocr_field_from_dynamic_region = original_dynamic_ocr

    expected_submission_dir = tmp_path / "responses" / "submission" / "field_crops"
    expected_hoist_dir = tmp_path / "field_crops" / "submission"

    crop_paths = [Path(field.crop_path) for field in result.fields if field.crop_path]
    assert len(crop_paths) == 2
    for crop_path in crop_paths:
        assert crop_path.parent == expected_submission_dir
        assert crop_path.exists()
        assert (expected_hoist_dir / crop_path.name).exists()
