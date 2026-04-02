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
                        "bbox": [0.1, 0.3, 0.8, 0.2],
                        "response_type": "free_response",
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


def test_apply_editor_regions_updates_only_editable_regions() -> None:
    spec = _sample_spec()
    regions = spec_to_editor_regions(spec)

    field_region = next(r for r in regions if r.region_type == "field")
    question_region = next(r for r in regions if r.region_type == "question")
    anchor_region = next(r for r in regions if r.region_type == "anchor")

    original_anchor_bbox = spec["pages"][0]["anchors"][0]["bbox"]

    field_region.bbox_norm = [0.2, 0.12, 0.25, 0.07]
    question_region.bbox_norm = [0.12, 0.34, 0.74, 0.19]
    anchor_region.bbox_norm = [0.40, 0.40, 0.10, 0.10]

    updated = apply_editor_regions_to_spec(spec, regions)

    assert updated["pages"][0]["fields"][0]["bbox"] == [0.2, 0.12, 0.25, 0.07]
    assert updated["pages"][0]["questions"][0]["bbox"] == [0.12, 0.34, 0.74, 0.19]
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
    doc.new_page(width=612, height=792)

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
    assert editor_detect_none._version_status_text() == "version=(none detected) expected=(none)"


def test_master_template_pdf_has_no_overlay_labels(tmp_path) -> None:
    input_pdf = tmp_path / "input.pdf"
    review_pdf = tmp_path / "review.pdf"
    master_pdf = tmp_path / "master.pdf"

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), "Template page", fontsize=12)
    page.draw_rect(fitz.Rect(100, 120, 260, 170), color=(0, 0, 0), width=0.8)
    doc.save(input_pdf)
    doc.close()

    regions = [
        EditorRegion(
            page_number=1,
            region_id="student_name",
            region_type="field",
            bbox_norm=[100 / 612, 120 / 792, 160 / 612, 50 / 792],
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
