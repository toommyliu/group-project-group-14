"""Tests for content-aware submission boundary detection."""

import re

from opengrader.core.submissions.boundary import (
    _IDENTITY_HEADER_RE,
    detect_submission_boundaries,
    plan_boundary_ranges,
)


def _ranges(flags):
    return detect_submission_boundaries(flags).ranges


# --------------------------------------------------------------------------- #
# The pure walk-forward decision
# --------------------------------------------------------------------------- #


def test_page_zero_always_anchors_submission_one() -> None:
    assert _ranges([False, False, False]) == [(0, 2)]  # no header ever → one submission


def test_simple_two_students() -> None:
    # first, content, content | first, content
    assert _ranges([True, False, False, True, False]) == [(0, 2), (3, 4)]


def test_cover_page_coalesces_with_first_content_page() -> None:
    # cover(header) + content1(header) + content2 + content3, twice.
    flags = [True, True, False, False, True, True, False, False]
    assert _ranges(flags) == [(0, 3), (4, 7)]


def test_two_adjacent_covers_stay_together() -> None:
    # Two first pages back-to-back with nothing between are left as one
    # submission rather than mis-split into two 1-page files.
    assert _ranges([True, True]) == [(0, 1)]


def test_non_header_page_between_headers_splits() -> None:
    # A blank/scratch page carries no header, so it reads as a continuation and
    # correctly lets the following header page start a new submission.
    assert _ranges([True, False, True]) == [(0, 1), (2, 2)]


def test_variable_length_submissions() -> None:
    flags = [True, False, False, False, True, False, True, False, False]
    assert _ranges(flags) == [(0, 3), (4, 5), (6, 8)]


def test_empty_input() -> None:
    assert _ranges([]) == []


def test_unusual_length_is_flagged() -> None:
    # Three submissions of length 2, 2, 10 — the long one is > 2x the median,
    # so it's flagged for review (a likely missed boundary).
    flags = [True, False, True, False, True] + [False] * 9
    result = detect_submission_boundaries(flags)
    assert result.ranges == [(0, 1), (2, 3), (4, 13)]
    assert any("unusual" in w for w in result.warnings)


def test_typical_uniform_lengths_produce_no_warnings() -> None:
    flags = [True, False, False] * 4  # four 3-page submissions
    result = detect_submission_boundaries(flags)
    assert len(result.ranges) == 4
    assert result.warnings == []


# --------------------------------------------------------------------------- #
# Identity-header regex
# --------------------------------------------------------------------------- #


def test_identity_header_regex_matches_common_labels() -> None:
    for text in ["Name:", "Student ID", "Student Number", "SID:", "ID Number"]:
        assert _IDENTITY_HEADER_RE.search(text), text


def test_identity_header_regex_ignores_unrelated_text() -> None:
    for text in ["Question 1", "Explain the algorithm", "Total points"]:
        assert not _IDENTITY_HEADER_RE.search(text), text


# --------------------------------------------------------------------------- #
# End-to-end over a synthetic born-digital bundle (real Tesseract)
# --------------------------------------------------------------------------- #


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
