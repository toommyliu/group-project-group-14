from pathlib import Path

import fitz

from opengrader.core.ocr import layout
from opengrader.core.ocr import extractor
from opengrader.core.ocr.extractor import (
    OcrConfig,
    process_submission_pdf,
    results_to_dict,
)
from opengrader.core.ocr.layout import (
    DetectedFieldRegion,
    DetectedQuestionRegion,
    SubmissionLayout,
)
from opengrader.core.pdf.templates.constants import LETTER_H, LETTER_W
from opengrader.core.question_types import FREE_RESPONSE, MULTIPLE_CHOICE


def _create_blank_pdf(path: Path, *, page_count: int = 1) -> None:
    doc = fitz.open()
    for _ in range(page_count):
        doc.new_page(width=LETTER_W, height=LETTER_H)
    doc.save(path)
    doc.close()


def test_process_submission_pdf_extracts_detected_questions(tmp_path: Path) -> None:
    pdf_path = tmp_path / "submission.pdf"
    _create_blank_pdf(pdf_path)

    def _fake_scan_layout(*_args: object, **_kwargs: object) -> SubmissionLayout:
        return SubmissionLayout(
            title_region=None,
            fields=[],
            review_reasons=[],
            questions=[
                DetectedQuestionRegion(
                    question_id="q1",
                    page_number=1,
                    question_number=1,
                    crop_bbox_norm=[0.1, 0.2, 0.8, 0.2],
                    continuation_segments=[],
                    confidence=95.0,
                ),
                DetectedQuestionRegion(
                    question_id="q2",
                    page_number=1,
                    question_number=2,
                    crop_bbox_norm=[0.1, 0.5, 0.8, 0.2],
                    continuation_segments=[],
                    confidence=95.0,
                ),
            ],
        )

    original_scan_layout = extractor.scan_submission_layout
    original_classify = extractor.classify_question_image
    extractor.scan_submission_layout = _fake_scan_layout
    extractor.classify_question_image = lambda _image: MULTIPLE_CHOICE
    try:
        result = process_submission_pdf(
            pdf_path=pdf_path,
            config=OcrConfig(),
            output_dir=tmp_path,
            response_format="png",
            dpi=72,
        )
    finally:
        extractor.scan_submission_layout = original_scan_layout
        extractor.classify_question_image = original_classify

    extracted_ids = [question.question_id for question in result.questions]
    assert extracted_ids == ["q1", "q2"]

    for question in result.questions:
        assert question.question_type == MULTIPLE_CHOICE
        assert question.output_path is not None
        assert Path(question.output_path).exists()

    serialized = results_to_dict(result)
    assert serialized["questions"][0]["question_type"] == MULTIPLE_CHOICE


def test_question_extraction_result_defaults_to_free_response() -> None:
    result = extractor.QuestionExtractionResult(question_id="q1", page_number=1)

    assert result.question_type == FREE_RESPONSE


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


def test_question_x_bounds_stay_tight_without_structural_box() -> None:
    anchors = [
        layout._QuestionAnchor(
            page_number=1,
            question_number=1,
            bbox_px=(145, 400, 165, 430),
            line_bbox_px=(145, 400, 760, 430),
            confidence=95.0,
        ),
        layout._QuestionAnchor(
            page_number=1,
            question_number=2,
            bbox_px=(150, 760, 170, 790),
            line_bbox_px=(150, 760, 780, 790),
            confidence=95.0,
        ),
    ]

    x0, x1 = layout._estimate_question_x_bounds(
        anchors,
        primitives_by_page={1: []},
        dims={1: (1000, 1400)},
    )

    assert 0.11 < x0 < 0.13
    assert x1 == 0.94


def test_question_crop_expansion_adds_padding() -> None:
    expanded = extractor._expand_question_bbox_px(
        (120, 200, 900, 500),
        page_w=1000,
        page_h=1400,
    )

    assert expanded[0] < 120
    assert expanded[2] > 900


def test_process_submission_pdf_saves_field_crops_with_submission_and_hoisted_copy(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "submission.pdf"
    _create_blank_pdf(pdf_path)

    def _fake_scan_layout(*_args: object, **_kwargs: object) -> SubmissionLayout:
        return SubmissionLayout(
            title_region=None,
            questions=[],
            review_reasons=[],
            fields=[
                DetectedFieldRegion(
                    field_id="student_name",
                    field_kind="name",
                    page_number=1,
                    crop_bbox_norm=[0.03, 0.04, 0.55, 0.08],
                    anchor_bbox_norm=None,
                    confidence=92.0,
                ),
                DetectedFieldRegion(
                    field_id="student_id",
                    field_kind="sid",
                    page_number=1,
                    crop_bbox_norm=[0.03, 0.14, 0.55, 0.08],
                    anchor_bbox_norm=None,
                    confidence=92.0,
                ),
            ],
        )

    original_scan_layout = extractor.scan_submission_layout
    try:
        extractor.scan_submission_layout = _fake_scan_layout

        result = process_submission_pdf(
            pdf_path=pdf_path,
            config=OcrConfig(),
            output_dir=tmp_path,
            response_format="png",
            dpi=72,
        )
    finally:
        extractor.scan_submission_layout = original_scan_layout

    expected_submission_dir = tmp_path / "responses" / "submission" / "field_crops"
    expected_hoist_dir = tmp_path / "field_crops" / "submission"

    crop_paths = [Path(field.crop_path) for field in result.fields if field.crop_path]
    assert len(crop_paths) == 2
    assert {path.name for path in crop_paths} == {"name_p1.png", "id_p1.png"}
    for crop_path in crop_paths:
        assert crop_path.parent == expected_submission_dir
        assert crop_path.exists()
        assert (expected_hoist_dir / crop_path.name).exists()


def test_save_layout_debug_receives_final_question_bboxes_for_continuations(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "submission.pdf"
    _create_blank_pdf(pdf_path, page_count=2)

    def _fake_scan_layout(*_args: object, **_kwargs: object) -> SubmissionLayout:
        return SubmissionLayout(
            title_region=None,
            fields=[],
            review_reasons=[],
            questions=[
                DetectedQuestionRegion(
                    question_id="q1",
                    page_number=1,
                    question_number=1,
                    crop_bbox_norm=[0.1, 0.82, 0.8, 0.16],
                    continuation_segments=[(2, [0.1, 0.04, 0.8, 0.18])],
                    confidence=95.0,
                ),
            ],
        )

    captured: dict[int, list[tuple[str, tuple[int, int, int, int]]]] = {}

    def _fake_write_layout_debug(
        _layout: SubmissionLayout,
        _rendered_pages: dict[int, tuple[object, int, int]],
        _output_dir: Path,
        *,
        question_crop_bboxes_by_page: dict[
            int, list[tuple[str, tuple[int, int, int, int]]]
        ]
        | None = None,
    ) -> None:
        assert question_crop_bboxes_by_page is not None
        captured.update(question_crop_bboxes_by_page)

    original_scan_layout = extractor.scan_submission_layout
    original_write_layout_debug = extractor.write_layout_debug
    try:
        extractor.scan_submission_layout = _fake_scan_layout
        extractor.write_layout_debug = _fake_write_layout_debug
        process_submission_pdf(
            pdf_path=pdf_path,
            config=OcrConfig(),
            output_dir=tmp_path,
            response_format="png",
            dpi=72,
            save_layout_debug=True,
        )
    finally:
        extractor.scan_submission_layout = original_scan_layout
        extractor.write_layout_debug = original_write_layout_debug

    assert set(captured) == {1, 2}
    assert [question_id for question_id, _bbox in captured[1]] == ["q1"]
    assert [question_id for question_id, _bbox in captured[2]] == ["q1"]
