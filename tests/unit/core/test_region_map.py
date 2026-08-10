"""Region maps: the format, its guardrails, and the seam into extraction.

The expensive failure this feature can have is silent: a map applied to the
wrong assignment mis-crops every answer of every student and nothing raises. Most of
what is asserted here is therefore about *refusing*, not about succeeding.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import fitz
import pytest
from PIL import Image, ImageDraw

from opengrader.core.question_types import FREE_RESPONSE, MULTIPLE_CHOICE
from opengrader.core.region_map import (
    REGION_MAP_FORMAT,
    REGION_MAP_VERSION,
    MappedQuestion,
    RegionBox,
    RegionMap,
    RegionMapError,
    blank_region_map,
    default_region_map_path,
    grid_region_map,
    load_region_map,
    load_verified_region_map,
    region_map_errors,
    region_map_from_json,
    region_map_to_blank_template,
    region_map_to_json,
    render_region_map_page,
    save_region_map,
    seed_region_map_from_qa,
    validate_region_map,
    verify_region_map,
    write_region_map_preview,
)


# ── fixtures ───────────────────────────────────────────────────────────────


def _image_only_pdf(path: Path, pages: int = 2, marker: str = "ASSIGNMENT") -> Path:
    """A PDF with **no text layer** — the exact case this feature exists for.

    Every page is a rasterized image, so ``page.get_text()`` returns nothing and
    the blank-template path would refuse the file outright.
    """
    doc = fitz.open()
    for index in range(pages):
        canvas = Image.new("RGB", (850, 1100), "white")
        draw = ImageDraw.Draw(canvas)
        draw.text((60, 70), f"{marker} page {index + 1}", fill="black")
        draw.rectangle([60, 300, 780, 520], outline="black")
        raster = path.parent / f"{path.stem}-{index}.png"
        canvas.save(raster)
        page = doc.new_page(width=612, height=792)
        page.insert_image(page.rect, filename=str(raster))
    doc.save(str(path))
    doc.close()
    return path


def _map_with_two_questions(pdf: Path) -> RegionMap:
    return replace(
        blank_region_map(pdf, assignment_title="Hand-written Midterm"),
        name_field=RegionBox(1, (0.55, 0.03, 0.40, 0.06)),
        student_id_field=RegionBox(1, (0.08, 0.03, 0.40, 0.06)),
        questions=(
            MappedQuestion("q1", 1, 1, (0.08, 0.30, 0.84, 0.20), possible_points=10.0),
            MappedQuestion(
                "q2",
                2,
                1,
                (0.08, 0.60, 0.84, 0.20),
                possible_points=5.0,
                question_type=MULTIPLE_CHOICE,
                continuations=(RegionBox(2, (0.08, 0.10, 0.84, 0.25)),),
            ),
        ),
    )


@pytest.fixture()
def assignment(tmp_path: Path) -> Path:
    return _image_only_pdf(tmp_path / "midterm.pdf")


# ── the assignment has no text layer, which is the whole point ───────────────────


def test_the_assignment_a_region_map_is_for_has_no_text_layer(assignment: Path) -> None:
    with fitz.open(str(assignment)) as doc:
        assert all(not page.get_text("text").strip() for page in doc)
    # ...and building a map for it still works, unlike --blank-template.
    assert blank_region_map(assignment).source_pages == 2


# ── round trip ─────────────────────────────────────────────────────────────


def test_json_round_trip_is_lossless(assignment: Path, tmp_path: Path) -> None:
    region_map = _map_with_two_questions(assignment)
    path = save_region_map(region_map, default_region_map_path(assignment))

    assert path.name == "midterm.regions.json"
    assert load_region_map(path) == region_map


def test_round_trip_keeps_continuations_and_types(assignment: Path) -> None:
    region_map = _map_with_two_questions(assignment)
    restored = region_map_from_json(json.loads(json.dumps(region_map_to_json(region_map))))

    q2 = restored.questions[1]
    assert q2.question_type == MULTIPLE_CHOICE
    assert q2.continuations == (RegionBox(2, (0.08, 0.10, 0.84, 0.25)),)
    assert [segment.page for segment in q2.segments] == [1, 2]


def test_written_file_names_the_format_and_version(assignment: Path, tmp_path: Path) -> None:
    path = save_region_map(_map_with_two_questions(assignment), tmp_path / "m.json")
    data = json.loads(path.read_text())
    assert data["format"] == REGION_MAP_FORMAT
    assert data["version"] == REGION_MAP_VERSION
    assert data["source"]["sha256"]


# ── version handling ───────────────────────────────────────────────────────


def test_a_newer_format_version_is_refused_not_half_read(assignment: Path, tmp_path: Path) -> None:
    """Half-reading a format that learned a new field silently drops it."""
    path = save_region_map(_map_with_two_questions(assignment), tmp_path / "m.json")
    data = json.loads(path.read_text())
    data["version"] = REGION_MAP_VERSION + 5
    path.write_text(json.dumps(data))

    with pytest.raises(RegionMapError, match="version"):
        load_region_map(path)


def test_a_file_that_is_not_a_region_map_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "something.json"
    path.write_text(json.dumps({"hello": "world"}))
    with pytest.raises(RegionMapError, match="not an Opengrader region map"):
        load_region_map(path)


def test_unparseable_json_is_refused_with_the_path(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json")
    with pytest.raises(RegionMapError, match="not valid JSON"):
        load_region_map(path)


# ── fingerprint verification ───────────────────────────────────────────────


def test_verification_accepts_the_pdf_the_map_was_drawn_on(assignment: Path) -> None:
    assert verify_region_map(_map_with_two_questions(assignment), assignment).ok


def test_verification_refuses_a_different_pdf(assignment: Path, tmp_path: Path) -> None:
    other = _image_only_pdf(tmp_path / "final.pdf", pages=2, marker="OTHER")
    verification = verify_region_map(_map_with_two_questions(assignment), other)

    assert not verification
    assert verification.kind == "fingerprint"
    # The message has to be actionable, not "invalid region map".
    assert "final.pdf" in verification.message
    assert "region-map propose" in verification.message


def test_verification_reports_a_page_count_change_in_its_own_terms(
    assignment: Path, tmp_path: Path
) -> None:
    """The commonest real cause: the assignment was re-exported and grew a page."""
    longer = _image_only_pdf(tmp_path / "midterm-v2.pdf", pages=3)
    verification = verify_region_map(_map_with_two_questions(assignment), longer)

    assert verification.kind == "page_count"
    assert "2-page" in verification.message and "3 pages" in verification.message


def test_verification_reports_a_missing_pdf(assignment: Path, tmp_path: Path) -> None:
    verification = verify_region_map(_map_with_two_questions(assignment), tmp_path / "gone.pdf")
    assert verification.kind == "missing_pdf"


def test_load_verified_refuses_the_wrong_assignment(assignment: Path, tmp_path: Path) -> None:
    path = save_region_map(_map_with_two_questions(assignment), tmp_path / "m.json")
    other = _image_only_pdf(tmp_path / "other.pdf", pages=2, marker="OTHER")

    assert load_verified_region_map(path, assignment)  # the right one loads
    with pytest.raises(RegionMapError):
        load_verified_region_map(path, other)


# ── clamping ───────────────────────────────────────────────────────────────


def test_a_box_running_off_the_page_is_clamped_on_load(assignment: Path, tmp_path: Path) -> None:
    """A hand-edited file must never crash an overnight extraction."""
    path = save_region_map(_map_with_two_questions(assignment), tmp_path / "m.json")
    data = json.loads(path.read_text())
    data["questions"][0]["bbox"] = [0.9, 0.9, 5.0, 5.0]
    data["fields"]["name"]["bbox"] = [-3.0, -2.0, 0.5, 0.5]
    path.write_text(json.dumps(data))

    restored = load_region_map(path)
    x, y, w, h = restored.questions[0].bbox
    assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0
    assert x + w <= 1.0 + 1e-9 and y + h <= 1.0 + 1e-9
    assert restored.name_field is not None
    assert restored.name_field.bbox[0] == 0.0


def test_a_junk_bbox_drops_the_question_rather_than_raising(
    assignment: Path, tmp_path: Path
) -> None:
    path = save_region_map(_map_with_two_questions(assignment), tmp_path / "m.json")
    data = json.loads(path.read_text())
    data["questions"][0]["bbox"] = ["nonsense"]
    path.write_text(json.dumps(data))

    restored = load_region_map(path)
    assert [question.question_id for question in restored.questions] == ["q2"]


# ── validation ─────────────────────────────────────────────────────────────


def _kinds(region_map: RegionMap, **kwargs) -> set[str]:
    return {finding.kind for finding in validate_region_map(region_map, **kwargs)}


def test_a_good_map_has_no_error_findings(assignment: Path) -> None:
    findings = validate_region_map(_map_with_two_questions(assignment))
    assert region_map_errors(findings) == ()


def test_no_questions_is_an_error(assignment: Path) -> None:
    assert "no_questions" in _kinds(blank_region_map(assignment))


def test_duplicate_question_ids_are_an_error(assignment: Path) -> None:
    region_map = _map_with_two_questions(assignment)
    duplicated = replace(
        region_map,
        questions=(
            region_map.questions[0],
            replace(region_map.questions[1], question_id="q1"),
        ),
    )
    assert "duplicate_question_id" in _kinds(duplicated)


def test_a_missing_question_id_is_an_error(assignment: Path) -> None:
    region_map = _map_with_two_questions(assignment)
    blanked = replace(
        region_map,
        questions=(replace(region_map.questions[0], question_id="  "),),
    )
    assert "missing_question_id" in _kinds(blanked)


def test_a_gap_in_the_numbering_is_a_warning(assignment: Path) -> None:
    region_map = _map_with_two_questions(assignment)
    gapped = replace(
        region_map,
        questions=(
            region_map.questions[0],
            replace(region_map.questions[1], question_id="q4", number=4),
        ),
    )
    findings = validate_region_map(gapped)
    gap = next(f for f in findings if f.kind == "question_number_gap")
    assert gap.severity == "warning"
    assert "2, 3" in gap.message


def test_a_repeated_question_number_is_reported(assignment: Path) -> None:
    region_map = _map_with_two_questions(assignment)
    repeated = replace(
        region_map,
        questions=(
            region_map.questions[0],
            replace(region_map.questions[1], number=1),
        ),
    )
    assert "duplicate_question_number" in _kinds(repeated)


def test_a_zero_area_box_is_an_error(assignment: Path) -> None:
    region_map = _map_with_two_questions(assignment)
    collapsed = replace(
        region_map,
        questions=(replace(region_map.questions[0], bbox=(0.5, 0.5, 0.0, 0.2)),),
    )
    assert "empty_region" in _kinds(collapsed)


def test_an_inverted_box_collapses_to_the_same_error(assignment: Path) -> None:
    """Negative width is what a box dragged backwards produces."""
    region_map = _map_with_two_questions(assignment)
    inverted = replace(
        region_map,
        questions=(replace(region_map.questions[0], bbox=(0.5, 0.5, -0.3, -0.2)),),
    )
    assert "empty_region" in _kinds(inverted)


def test_a_box_off_the_page_is_a_warning(assignment: Path) -> None:
    region_map = _map_with_two_questions(assignment)
    spilling = replace(
        region_map,
        questions=(replace(region_map.questions[0], bbox=(0.5, 0.5, 0.9, 0.2)),),
    )
    findings = validate_region_map(spilling)
    assert any(
        f.kind == "region_off_page" and f.severity == "warning" for f in findings
    )


def test_a_page_the_assignment_does_not_have_is_an_error(assignment: Path) -> None:
    region_map = _map_with_two_questions(assignment)
    beyond = replace(
        region_map,
        questions=(replace(region_map.questions[0], page=9),),
    )
    assert "page_out_of_range" in _kinds(beyond)


def test_substantially_overlapping_regions_are_reported(assignment: Path) -> None:
    region_map = _map_with_two_questions(assignment)
    overlapping = replace(
        region_map,
        questions=(
            region_map.questions[0],
            replace(
                region_map.questions[1],
                page=1,
                bbox=(0.08, 0.32, 0.84, 0.20),
                continuations=(),
            ),
        ),
    )
    findings = validate_region_map(overlapping)
    overlap = next(f for f in findings if f.kind == "overlapping_regions")
    assert overlap.severity == "warning"
    assert "q1" in overlap.message and "q2" in overlap.message


def test_touching_regions_are_not_reported_as_overlapping(assignment: Path) -> None:
    """Answer boxes genuinely sit next to each other; sharing an edge is normal."""
    region_map = _map_with_two_questions(assignment)
    stacked = replace(
        region_map,
        questions=(
            region_map.questions[0],
            replace(
                region_map.questions[1],
                page=1,
                bbox=(0.08, 0.50, 0.84, 0.20),
                continuations=(),
            ),
        ),
    )
    assert "overlapping_regions" not in _kinds(stacked)


def test_missing_identity_fields_are_warnings(assignment: Path) -> None:
    region_map = replace(
        _map_with_two_questions(assignment), name_field=None, student_id_field=None
    )
    kinds = _kinds(region_map)
    assert {"missing_name_field", "missing_id_field"} <= kinds
    assert region_map_errors(validate_region_map(region_map)) == ()


def test_missing_points_is_a_warning_not_a_blocker(assignment: Path) -> None:
    region_map = _map_with_two_questions(assignment)
    unpriced = replace(
        region_map,
        questions=(replace(region_map.questions[0], possible_points=None),),
    )
    assert "missing_points" in _kinds(unpriced)
    assert region_map_errors(validate_region_map(unpriced)) == ()


def test_findings_are_ordered_worst_first(assignment: Path) -> None:
    region_map = replace(
        _map_with_two_questions(assignment),
        name_field=None,
        questions=(
            MappedQuestion("q1", 1, 1, (0.1, 0.1, 0.0, 0.0)),
        ),
    )
    findings = validate_region_map(region_map)
    assert findings[0].severity == "error"


# ── the seam into extraction ───────────────────────────────────────────────


def test_conversion_produces_a_template_the_processor_accepts(assignment: Path) -> None:
    from opengrader.core.submissions.processor import (
        _combined_question_positions,
        _question_number,
        _scan_offset_by_page,
    )
    from opengrader.core.submissions.processor import _ScanLayout

    template = region_map_to_blank_template(_map_with_two_questions(assignment))

    assert template.exact_regions is True
    assert template.total_pages == 2
    assert template.assignment_title == "Hand-written Midterm"
    assert [q.question_id for q in template.questions] == ["q1", "q2"]
    assert template.questions[0].possible_points == 10.0
    assert template.questions[1].question_type == MULTIPLE_CHOICE
    assert template.name_field is not None and template.name_field.page == 1

    # The bbox survives the conversion untouched — this is the promise.
    assert template.questions[0].source_segments[0].bbox_norm == (0.08, 0.30, 0.84, 0.20)
    assert len(template.questions[1].source_segments) == 2

    # And the existing processor helpers still take the structure happily.
    empty_layout = _ScanLayout({}, {}, None, None, {})
    assert _scan_offset_by_page(template, empty_layout) == {}
    positions = _combined_question_positions(template, empty_layout, {})
    assert positions[1] == (1, 0.30)
    assert _question_number(template.questions[0]) == 1


def test_questions_come_out_in_question_number_order(assignment: Path) -> None:
    region_map = _map_with_two_questions(assignment)
    shuffled = replace(
        region_map, questions=tuple(reversed(region_map.questions))
    )
    template = region_map_to_blank_template(shuffled)
    assert [q.question_id for q in template.questions] == ["q1", "q2"]


def test_the_template_falls_back_to_the_filename_for_a_title(assignment: Path) -> None:
    region_map = replace(_map_with_two_questions(assignment), assignment_title="")
    assert region_map_to_blank_template(region_map).assignment_title == "midterm"


# ── starting points ────────────────────────────────────────────────────────


def test_a_grid_map_covers_every_page(assignment: Path) -> None:
    region_map = grid_region_map(assignment, per_page=3)
    assert len(region_map.questions) == 6
    assert {question.page for question in region_map.questions} == {1, 2}
    assert [question.question_id for question in region_map.questions[:3]] == [
        "q1",
        "q2",
        "q3",
    ]


def test_grid_boxes_do_not_overlap_each_other(assignment: Path) -> None:
    """A starting point that immediately reports warnings is a bad starting point."""
    assert "overlapping_regions" not in _kinds(grid_region_map(assignment, per_page=4))


def test_a_two_column_grid_lays_out_side_by_side(assignment: Path) -> None:
    region_map = grid_region_map(assignment, per_page=4, columns=2)
    first, second = region_map.questions[0], region_map.questions[1]
    assert second.bbox[0] > first.bbox[0]
    assert second.bbox[1] == first.bbox[1]


def test_blank_region_map_binds_to_the_assignment_without_reading_text(assignment: Path) -> None:
    region_map = blank_region_map(assignment)
    assert region_map.questions == ()
    assert verify_region_map(region_map, assignment).ok


def test_a_missing_assignment_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(RegionMapError, match="not found"):
        blank_region_map(tmp_path / "nowhere.pdf")


# ── proposal ───────────────────────────────────────────────────────────────


def test_proposal_returns_a_map_bound_to_the_assignment(assignment: Path) -> None:
    """Detection may find nothing on a synthetic page; the binding still holds."""
    proposal = __import__(
        "opengrader.core.region_map", fromlist=["propose_region_map"]
    ).propose_region_map(assignment, dpi=100)

    assert verify_region_map(proposal.region_map, assignment).ok
    assert proposal.region_map.source_pages == 2
    for question in proposal.region_map.questions:
        # Whatever it found, the confidence is carried so the editor can lead
        # with the least trustworthy boxes.
        assert question.confidence is not None


# ── seeding from a QA file ─────────────────────────────────────────────────


def test_from_qa_fills_points_and_types(assignment: Path, tmp_path: Path) -> None:
    from opengrader.core.qa_file import parse_qa_markdown

    qa = parse_qa_markdown(
        "\n".join(
            [
                "- (12 points) What is a semaphore?",
                "  - **Correct**",
                "    - a counter",
                "",
                "- (4 points) Which bit is the MSB of 1010?",
                "  - **Choices**",
                "    - A) 0",
                "    - B) 1",
                "  - **Correct**",
                "    - B",
            ]
        )
    )
    region_map = replace(
        _map_with_two_questions(assignment),
        questions=(
            MappedQuestion("q1", 1, 1, (0.1, 0.3, 0.8, 0.2)),
            MappedQuestion("q2", 2, 1, (0.1, 0.6, 0.8, 0.2)),
        ),
    )

    seeded = seed_region_map_from_qa(region_map, qa)

    assert seeded.questions[0].possible_points == 12.0
    assert seeded.questions[0].question_type == FREE_RESPONSE
    assert seeded.questions[1].possible_points == 4.0
    assert seeded.questions[1].question_type == MULTIPLE_CHOICE
    # Geometry is never touched by seeding.
    assert seeded.questions[0].bbox == region_map.questions[0].bbox


# ── rendering ──────────────────────────────────────────────────────────────


def test_a_rendered_page_is_the_page_size_and_draws_something(assignment: Path) -> None:
    region_map = _map_with_two_questions(assignment)
    plain = render_region_map_page(assignment, replace(region_map, questions=()), 1, dpi=72)
    marked = render_region_map_page(assignment, region_map, 1, dpi=72)

    assert plain.size == marked.size
    assert list(plain.getdata()) != list(marked.getdata())


def test_preview_writes_one_pdf_for_the_whole_assignment(assignment: Path, tmp_path: Path) -> None:
    written = write_region_map_preview(
        assignment, _map_with_two_questions(assignment), tmp_path / "preview.pdf", dpi=72
    )
    assert written == (tmp_path / "preview.pdf",)
    assert written[0].stat().st_size > 0
    with fitz.open(str(written[0])) as doc:
        assert len(doc) == 2


def test_preview_writes_one_png_per_page_for_a_png_output(
    assignment: Path, tmp_path: Path
) -> None:
    written = write_region_map_preview(
        assignment, _map_with_two_questions(assignment), tmp_path / "shot.png", dpi=72
    )
    assert [path.name for path in written] == ["shot-page-1.png", "shot-page-2.png"]
    assert all(path.exists() for path in written)
