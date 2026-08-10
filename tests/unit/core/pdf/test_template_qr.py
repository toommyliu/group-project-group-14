"""QR template generation + the reader integrations that consume the codes."""

from __future__ import annotations

import fitz

from opengrader.core.ocr.qr_reader import read_code_from_image
from opengrader.core.pdf.templates import (
    generate_personalized_batch,
    generate_template_pdf,
)
from opengrader.core.pdf_utils import render_page_to_image
from opengrader.core.roster import RosterStudent
from opengrader.core.submissions.boundary import (
    boundaries_from_qr_pages,
    plan_boundary_ranges,
)
from opengrader.core.submissions.reorder import reorder_pdf_pages

QUESTIONS = [
    {"id": "q1", "points": 5, "prompt": "What is 2 + 2?"},
    {"id": "q2", "points": 5, "prompt": "Name a primary color."},
]


def _codes(pdf_path) -> list:
    doc = fitz.open(str(pdf_path))
    out = []
    try:
        for i in range(len(doc)):
            image, _, _ = render_page_to_image(doc, i, dpi=150)
            out.append(read_code_from_image(image))
    finally:
        doc.close()
    return out


def test_generic_template_stamps_version_and_page(tmp_path) -> None:
    out = tmp_path / "tmpl.pdf"
    generate_template_pdf(out, "A", QUESTIONS, "Quiz 1", qr=True)
    codes = _codes(out)
    assert codes, "expected at least one page"
    total = len(codes)
    for i, code in enumerate(codes, start=1):
        assert code is not None
        assert code.student_id is None  # generic — no identity
        assert code.version == "A"
        assert code.page == i
        assert code.total_pages == total


def test_plain_template_has_no_qr(tmp_path) -> None:
    out = tmp_path / "plain.pdf"
    generate_template_pdf(out, "A", QUESTIONS, "Quiz 1")  # qr defaults False
    assert all(code is None for code in _codes(out))


def test_personalized_batch_master_key_first_then_per_student(tmp_path) -> None:
    out = tmp_path / "batch.pdf"
    students = [
        RosterStudent(student_id="100001", name="Alice Zhang"),
        RosterStudent(student_id="", name="Carol Xavier"),
    ]
    assigned = generate_personalized_batch(out, students, "A", QUESTIONS, "Quiz 1")

    # Every student got a (unique, 6-digit for the minted one) id back.
    ids = {s.student_id for s in assigned}
    assert len(ids) == 2
    assert assigned[0].student_id == "100001"

    codes = _codes(out)
    # Page 1 is the teacher master key — deliberately carries no QR.
    assert codes[0] is None
    student_codes = [c for c in codes[1:] if c is not None]
    assert {c.student_id for c in student_codes} == ids
    assert all(c.version == "A" and c.page == 1 for c in student_codes)


def test_boundary_detection_splits_qr_bundle(tmp_path) -> None:
    """A batch (minus master key) reads as one submission per QR page-1 sheet."""
    out = tmp_path / "batch.pdf"
    students = [
        RosterStudent(student_id="100001", name="Alice Zhang"),
        RosterStudent(student_id="100002", name="Bob Young"),
        RosterStudent(student_id="100003", name="Carol Xavier"),
    ]
    generate_personalized_batch(out, students, "A", QUESTIONS, "Quiz 1")

    # Simulate the scanned bundle: drop the teacher master-key page.
    src = fitz.open(str(out))
    bundle_path = tmp_path / "bundle.pdf"
    bundle = fitz.open()
    bundle.insert_pdf(src, from_page=1)
    bundle.save(str(bundle_path))
    bundle.close()
    src.close()

    # Exact QR page numbers → three single-page submissions, no coalescing.
    ranges, _warnings = plan_boundary_ranges(bundle_path)
    assert [(r.start_page, r.end_page) for r in ranges] == [(1, 1), (2, 2), (3, 3)]


def test_boundaries_from_qr_pages_needs_every_page() -> None:
    # Exact path splits adjacent single-page submissions the heuristic would merge.
    exact = boundaries_from_qr_pages([1, 1, 1])
    assert exact is not None and exact.ranges == [(0, 0), (1, 1), (2, 2)]
    # A multi-page submission groups correctly.
    grouped = boundaries_from_qr_pages([1, 2, 3, 1, 2])
    assert grouped is not None and grouped.ranges == [(0, 2), (3, 4)]
    # Any missing QR page number → fall back (returns None).
    assert boundaries_from_qr_pages([1, None, 1]) is None
    assert boundaries_from_qr_pages([]) is None


def test_reorder_uses_qr_page_numbers(tmp_path) -> None:
    """Shuffled pages of a QR template are restored to order from the QR codes."""
    many = [
        {"id": f"q{i}", "points": 5, "prompt": f"Question {i}? " + ("blah " * 40)}
        for i in range(1, 13)
    ]
    out = tmp_path / "multi.pdf"
    generate_template_pdf(out, "A", many, "Long Quiz", qr=True)

    original = _codes(out)
    assert len(original) >= 3, "need several pages to exercise reordering"

    # Shuffle: move the last page to the front (definitely out of order).
    doc = fitz.open(str(out))
    doc.move_page(len(doc) - 1, 0)
    shuffled_path = tmp_path / "shuffled.pdf"
    doc.save(str(shuffled_path))
    doc.close()

    result = reorder_pdf_pages(shuffled_path)
    # ReorderResult is truthy exactly when the file was rewritten.
    assert result.changed is True
    assert bool(result) is True

    restored = [c.page for c in _codes(shuffled_path)]
    assert restored == sorted(restored) == list(range(1, len(restored) + 1))
