import re

from opengrader.core.submissions.boundary import (
    _IDENTITY_HEADER_RE,
    detect_submission_boundaries,
    plan_boundary_ranges,
)


def _ranges(flags):
    return detect_submission_boundaries(flags).ranges


def test_page_zero_always_anchors_submission_one() -> None:
    assert _ranges([False, False, False]) == [(0, 2)]


def test_simple_two_students() -> None:
    assert _ranges([True, False, False, True, False]) == [(0, 2), (3, 4)]


def test_cover_page_coalesces_with_first_content_page() -> None:
    flags = [True, True, False, False, True, True, False, False]
    assert _ranges(flags) == [(0, 3), (4, 7)]


def test_two_adjacent_covers_stay_together() -> None:
    assert _ranges([True, True]) == [(0, 1)]


def test_non_header_page_between_headers_splits() -> None:
    assert _ranges([True, False, True]) == [(0, 1), (2, 2)]


def test_variable_length_submissions() -> None:
    flags = [True, False, False, False, True, False, True, False, False]
    assert _ranges(flags) == [(0, 3), (4, 5), (6, 8)]


def test_empty_input() -> None:
    assert _ranges([]) == []


def test_unusual_length_is_flagged() -> None:
    flags = [True, False, True, False, True] + [False] * 9
    result = detect_submission_boundaries(flags)
    assert result.ranges == [(0, 1), (2, 3), (4, 13)]
    assert any("unusual" in w for w in result.warnings)


def test_typical_uniform_lengths_produce_no_warnings() -> None:
    flags = [True, False, False] * 4
    result = detect_submission_boundaries(flags)
    assert len(result.ranges) == 4
    assert result.warnings == []


def test_identity_header_regex_matches_common_labels() -> None:
    for text in ["Name:", "Student ID", "Student Number", "SID:", "ID Number"]:
        assert _IDENTITY_HEADER_RE.search(text), text


def test_identity_header_regex_ignores_unrelated_text() -> None:
    for text in ["Question 1", "Explain the algorithm", "Total points"]:
        assert not _IDENTITY_HEADER_RE.search(text), text


def test_plan_boundary_ranges_detects_two_students(tmp_path) -> None:
    import fitz

    doc = fitz.open()

    def add(header: bool, body: str) -> None:
        page = doc.new_page(width=612, height=792)
        if header:
            page.insert_text((60, 70), "Name: ________   Student ID: ________", fontsize=16)
        page.insert_text((60, 300), body, fontsize=14)

    add(True, "Q1. Explain recursion.")
    add(False, "Q2. Big-O of mergesort?")
    add(False, "Q3. Define a stack.")
    add(True, "Q1. Explain recursion.")
    add(False, "Q2. Big-O of mergesort?")
    src = tmp_path / "bundle.pdf"
    doc.save(src)
    doc.close()

    ranges, warnings = plan_boundary_ranges(src)
    assert [(r.start_page, r.end_page) for r in ranges] == [(1, 3), (4, 5)]
    assert [r.name for r in ranges] == ["submission-0001", "submission-0002"]


def test_missing_page_keeps_next_students_scan_intact() -> None:
    result = detect_submission_boundaries(
        [True, False, True, False, False], page_numbers=[1, 3, 1, 2, 3], expected_pages=3,
    )
    assert result.ranges == [(0, 1), (2, 4)]
    assert result.warnings == [
        "Submission at pages 1-2: incomplete scan, 2 of 3 expected pages; 1 page(s) missing. Check the original scan.",
        "Submission at pages 1-2: missing printed page(s) 2.",
    ]


def test_page_numbers_override_repeated_identity_headers() -> None:
    result = detect_submission_boundaries(
        [True] * 5, page_numbers=[1, 2, 1, 2, 3], expected_pages=3,
    )
    assert result.ranges == [(0, 1), (2, 4)]
    assert "2 of 3" in result.warnings[0]


def test_partial_page_number_evidence_still_finds_restarts() -> None:
    result = detect_submission_boundaries(
        [True, False, True, False, False], page_numbers=[1, None, 1, 2, 3], expected_pages=3,
    )
    assert result.ranges == [(0, 1), (2, 4)]
    assert len(result.warnings) == 1
    assert "incomplete scan" in result.warnings[0]


def test_two_unnumbered_students_of_different_lengths_are_flagged() -> None:
    result = detect_submission_boundaries([True, False, True, False, False])
    assert result.ranges == [(0, 1), (2, 4)]
    assert "2 of 3" in result.warnings[0]


def test_scan_warning_survives_split_extraction_and_markdown(tmp_path) -> None:
    import fitz
    from opengrader.core.submissions.splitter import split_scanned_submission_pdf
    from opengrader.core.submissions.processor import build_student_submission_document_unified
    from opengrader.core.submissions.student_file import load_student_submission_markdown
    from opengrader.core.submissions.review import flag_submission, ReviewReason

    source = tmp_path / "scan.pdf"
    with fitz.open() as doc:
        for number in [1, 3, 1, 2, 3]:
            page = doc.new_page()
            page.insert_text((60, 70), "Name: Student  Student ID: 123456789")
            page.insert_text((60, 250), "1. (10 points) Explain recursion.")
            page.insert_text((290, 780), str(number))
        doc.save(source)
    ranges, warnings = plan_boundary_ranges(source, expected_pages=3)
    assert [(r.start_page, r.end_page) for r in ranges] == [(1, 2), (3, 5)]
    paths = split_scanned_submission_pdf(source, tmp_path / "split", ranges)
    _, markdown = build_student_submission_document_unified(
        paths[0], tmp_path / "extracted", None, 72, True, None, True, False, None, lambda message: None,
    )
    document = load_student_submission_markdown(markdown)
    flags = [flag for flag in flag_submission(document) if flag.reason == ReviewReason.SCAN_INCOMPLETE]
    assert [flag.detail for flag in flags] == list(ranges[0].warnings)
    assert "2 of 3" in flags[0].detail


def test_numbered_continuation_separates_adjacent_unnumbered_first_pages() -> None:
    detected = detect_submission_boundaries(
        [True, True, False], page_numbers=[None, None, 2], expected_pages=2,
    )
    assert detected.ranges == [(0, 0), (1, 2)]
    assert any("1 of 2 expected pages" in warning for warning in detected.warnings)
