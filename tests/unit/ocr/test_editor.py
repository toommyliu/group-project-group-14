import fitz

from opengrader.core.ocr.editor import (
    EditorRegion,
    apply_editor_regions_to_spec,
    border_strip_rects,
    clamp_bbox_norm,
    render_master_template_pdf,
    render_review_pdf,
    spec_to_editor_regions,
)
from opengrader.cli.ocr_preview_pdf import LiveRegionEditor
from opengrader.core.pdf.templates.constants import QuestionType, LETTER_W, LETTER_H


def _sample_spec() -> dict:
    return {
        "schema_version": "1.0",
        "unit_id": "unit-1",
        "pages": [
            {
                "page_number": 1,
                "anchors": [
                    {"id": "tl", "bbox": [0.02, 0.02, 0.03, 0.03]},
                    {"id": "tr", "bbox": [0.95, 0.02, 0.03, 0.03]},
                ],
                "title_bbox": [0.2, 0.06, 0.6, 0.08],
                "fields": [
                    {
                        "id": "student_name",
                        "bbox": [0.1, 0.1, 0.3, 0.06],
                        "type": "name",
                    }
                ],
                "questions": [
                    {
                        "id": "q1",
                        "bbox": [0.1, 0.3, 0.8, 0.08],
                        "prompt_bbox": [0.1, 0.3, 0.8, 0.08],
                        "full_bbox": [0.1, 0.3, 0.8, 0.28],
                        "answer_bbox": [0.12, 0.39, 0.76, 0.18],
                        "response_type": QuestionType.FREE_RESPONSE,
                        "subquestions": [
                            {
                                "id": "q1a",
                                "bbox": [0.13, 0.5, 0.74, 0.05],
                                "prompt_bbox": [0.13, 0.5, 0.74, 0.05],
                                "full_bbox": [0.12, 0.49, 0.76, 0.16],
                                "answer_bbox": [0.13, 0.55, 0.74, 0.09],
                                "response_type": QuestionType.FREE_RESPONSE,
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_spec_to_editor_regions_locks_anchors() -> None:
    regions = spec_to_editor_regions(_sample_spec())

    anchors = [r for r in regions if r.region_type == "anchor"]
    non_anchors = [r for r in regions if r.region_type != "anchor"]

    assert anchors
    assert all(r.locked for r in anchors)
    assert non_anchors
    assert all(not r.locked for r in non_anchors)


def test_spec_to_editor_regions_includes_question_answer_and_subquestion_regions() -> None:
    regions = spec_to_editor_regions(_sample_spec())

    region_types = {region.region_type for region in regions}
    assert "title" in region_types
    assert "question" in region_types
    assert "question_answer" in region_types
    assert "question_full" in region_types
    assert "subquestion" in region_types
    assert "subquestion_answer" in region_types
    assert "subquestion_full" in region_types


def test_apply_editor_regions_updates_only_editable_regions() -> None:
    spec = _sample_spec()
    regions = spec_to_editor_regions(spec)

    title_region = next(r for r in regions if r.region_type == "title")
    field_region = next(r for r in regions if r.region_type == "field")
    question_region = next(r for r in regions if r.region_type == "question")
    question_answer_region = next(
        r for r in regions if r.region_type == "question_answer"
    )
    question_full_region = next(r for r in regions if r.region_type == "question_full")
    subquestion_region = next(r for r in regions if r.region_type == "subquestion")
    subquestion_answer_region = next(
        r for r in regions if r.region_type == "subquestion_answer"
    )
    subquestion_full_region = next(
        r for r in regions if r.region_type == "subquestion_full"
    )
    anchor_region = next(r for r in regions if r.region_type == "anchor")

    original_anchor_bbox = spec["pages"][0]["anchors"][0]["bbox"]

    title_region.bbox_norm = [0.21, 0.07, 0.58, 0.07]
    field_region.bbox_norm = [0.2, 0.12, 0.25, 0.07]
    question_region.bbox_norm = [0.12, 0.34, 0.74, 0.05]
    question_answer_region.bbox_norm = [0.14, 0.41, 0.70, 0.15]
    question_full_region.bbox_norm = [0.11, 0.33, 0.77, 0.27]
    subquestion_region.bbox_norm = [0.14, 0.52, 0.70, 0.04]
    subquestion_answer_region.bbox_norm = [0.14, 0.56, 0.70, 0.08]
    subquestion_full_region.bbox_norm = [0.13, 0.51, 0.72, 0.14]
    anchor_region.bbox_norm = [0.40, 0.40, 0.10, 0.10]

    updated = apply_editor_regions_to_spec(spec, regions)

    q1 = updated["pages"][0]["questions"][0]
    q1a = q1["subquestions"][0]

    assert updated["pages"][0]["title_bbox"] == [0.21, 0.07, 0.58, 0.07]
    assert updated["pages"][0]["fields"][0]["bbox"] == [0.2, 0.12, 0.25, 0.07]
    assert q1["bbox"] == [0.12, 0.34, 0.74, 0.05]
    assert q1["prompt_bbox"] == [0.12, 0.34, 0.74, 0.05]
    assert q1["answer_bbox"] == [0.14, 0.41, 0.7, 0.15]
    assert q1["full_bbox"] == [0.11, 0.33, 0.77, 0.27]

    assert q1a["bbox"] == [0.14, 0.52, 0.7, 0.04]
    assert q1a["prompt_bbox"] == [0.14, 0.52, 0.7, 0.04]
    assert q1a["answer_bbox"] == [0.14, 0.56, 0.7, 0.08]
    assert q1a["full_bbox"] == [0.13, 0.51, 0.72, 0.14]

    assert updated["pages"][0]["anchors"][0]["bbox"] == original_anchor_bbox


def test_clamp_bbox_norm_constrains_bounds_and_size() -> None:
    bbox = clamp_bbox_norm([-0.5, 0.99, 2.0, 0.0], min_width=0.1, min_height=0.2)
    x, y, w, h = bbox

    assert 0.0 <= x <= 1.0
    assert 0.0 <= y <= 1.0
    assert w >= 0.1
    assert h >= 0.2
    assert x + w <= 1.0
    assert y + h <= 1.0


def test_border_strip_rects_returns_four_nonempty_edges() -> None:
    rect = fitz.Rect(100, 200, 300, 260)
    strips = border_strip_rects(rect, thickness=2.0)

    assert len(strips) == 4
    for strip in strips:
        assert strip.width > 0
        assert strip.height > 0


def test_live_region_editor_version_status_text() -> None:
    doc = fitz.open()
    doc.new_page(width=LETTER_W, height=LETTER_H)

    regions = [
        EditorRegion(
            page_number=1,
            region_id="q1",
            region_type="question",
            bbox_norm=[0.1, 0.1, 0.2, 0.2],
            locked=False,
            source_index=0,
        )
    ]

    editor = LiveRegionEditor(
        doc,
        regions,
        detected_version="A",
        expected_versions=["A", "B"],
    )

    assert editor._version_status_text() == "version=A expected=A"

    editor_detect_none = LiveRegionEditor(
        doc,
        regions,
        detected_version=None,
        expected_versions=[],
    )
    assert (
        editor_detect_none._version_status_text()
        == "version=(none detected) expected=(none)"
    )


def test_master_template_pdf_has_no_overlay_labels(tmp_path) -> None:
    input_pdf = tmp_path / "input.pdf"
    review_pdf = tmp_path / "review.pdf"
    master_pdf = tmp_path / "master.pdf"

    doc = fitz.open()
    page = doc.new_page(width=LETTER_W, height=LETTER_H)
    page.insert_text((72, 72), "Template page", fontsize=12)
    page.draw_rect(fitz.Rect(100, 120, 260, 170), color=(0, 0, 0), width=0.8)
    doc.save(input_pdf)
    doc.close()

    regions = [
        EditorRegion(
            page_number=1,
            region_id="student_name",
            region_type="field",
            bbox_norm=[100 / LETTER_W, 120 / LETTER_H, 160 / LETTER_W, 50 / LETTER_H],
            locked=False,
            source_index=0,
        )
    ]

    render_review_pdf(input_pdf, review_pdf, regions)
    render_master_template_pdf(input_pdf, master_pdf, regions)

    review_doc = fitz.open(review_pdf)
    review_text = review_doc[0].get_text("text")
    review_doc.close()

    master_doc = fitz.open(master_pdf)
    master_text = master_doc[0].get_text("text")
    master_doc.close()

    assert "field:student_name" in review_text
    assert "field:student_name" not in master_text
