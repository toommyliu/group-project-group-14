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
        assert code.student_id is None
        assert code.version == "A"
        assert code.page == i
        assert code.total_pages == total


def test_plain_template_has_no_qr(tmp_path) -> None:
    out = tmp_path / "plain.pdf"
    generate_template_pdf(out, "A", QUESTIONS, "Quiz 1")
    assert all(code is None for code in _codes(out))


def test_personalized_batch_master_key_first_then_per_student(tmp_path) -> None:
    out = tmp_path / "batch.pdf"
    students = [
        RosterStudent(student_id="100001", name="Alice Zhang"),
        RosterStudent(student_id="", name="Carol Xavier"),
    ]
    assigned = generate_personalized_batch(out, students, "A", QUESTIONS, "Quiz 1")

    ids = {s.student_id for s in assigned}
    assert len(ids) == 2
    assert assigned[0].student_id == "100001"

    codes = _codes(out)
    assert codes[0] is None
    student_codes = [c for c in codes[1:] if c is not None]
    assert {c.student_id for c in student_codes} == ids
    assert all(c.version == "A" and c.page == 1 for c in student_codes)


def test_boundary_detection_splits_qr_bundle(tmp_path) -> None:
    out = tmp_path / "batch.pdf"
    students = [
        RosterStudent(student_id="100001", name="Alice Zhang"),
        RosterStudent(student_id="100002", name="Bob Young"),
        RosterStudent(student_id="100003", name="Carol Xavier"),
    ]
    generate_personalized_batch(out, students, "A", QUESTIONS, "Quiz 1")

    src = fitz.open(str(out))
    bundle_path = tmp_path / "bundle.pdf"
    bundle = fitz.open()
    bundle.insert_pdf(src, from_page=1)
    bundle.save(str(bundle_path))
    bundle.close()
    src.close()

    ranges, _warnings = plan_boundary_ranges(bundle_path)
    assert [(r.start_page, r.end_page) for r in ranges] == [(1, 1), (2, 2), (3, 3)]


def test_boundaries_from_qr_pages_needs_every_page() -> None:
    exact = boundaries_from_qr_pages([1, 1, 1])
    assert exact is not None and exact.ranges == [(0, 0), (1, 1), (2, 2)]
    grouped = boundaries_from_qr_pages([1, 2, 3, 1, 2])
    assert grouped is not None and grouped.ranges == [(0, 2), (3, 4)]
    assert boundaries_from_qr_pages([1, None, 1]) is None
    assert boundaries_from_qr_pages([]) is None


def test_reorder_uses_qr_page_numbers(tmp_path) -> None:
    many = [
        {"id": f"q{i}", "points": 5, "prompt": f"Question {i}? " + ("blah " * 40)}
        for i in range(1, 13)
    ]
    out = tmp_path / "multi.pdf"
    generate_template_pdf(out, "A", many, "Long Quiz", qr=True)

    original = _codes(out)
    assert len(original) >= 3, "need several pages to exercise reordering"

    doc = fitz.open(str(out))
    doc.move_page(len(doc) - 1, 0)
    shuffled_path = tmp_path / "shuffled.pdf"
    doc.save(str(shuffled_path))
    doc.close()

    result = reorder_pdf_pages(shuffled_path)
    assert result.changed is True
    assert bool(result) is True

    restored = [c.page for c in _codes(shuffled_path)]
    assert restored == sorted(restored) == list(range(1, len(restored) + 1))
