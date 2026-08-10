"""Tests for the return-bundle export engine (core/export/bundle.py)."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import fitz
import pytest

from opengrader.core.export import (
    BUNDLE_PRESETS,
    BundleContent,
    build_return_bundle,
    default_bundle_name,
)
from opengrader.core.source_location import QuestionSourceSegment
from opengrader.core.submissions import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    render_student_submission_markdown,
)


def _make_source_pdf(path: Path) -> None:
    """Write a minimal one-page PDF to stand in for a scanned submission."""
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((72, 72), "Exam page")
    document.save(path)
    document.close()


def _question(
    question_id: str,
    possible: float | None,
    awarded: float | None,
    *,
    segments: tuple[QuestionSourceSegment, ...] = (),
    response_text: str = "",
) -> StudentSubmissionQuestion:
    return StudentSubmissionQuestion(
        question_id=question_id,
        prompt="",
        possible_points=possible,
        response_image_path=None,
        response_text=response_text,
        points_awarded=awarded,
        rubric_labels=("Correct (+0)",) if awarded else (),
        analysis="Nice work" if awarded else "",
        source_segments=segments,
    )


def _write_cohort(directory: Path) -> None:
    """Two graded students, each with a real source PDF beside the .submission.md."""
    for index, (name, sid) in enumerate(
        [("Tommy Liu", "111"), ("Andy Cortes", "222")], start=1
    ):
        source_pdf = directory / f"submission-{index}.pdf"
        _make_source_pdf(source_pdf)
        document = StudentSubmissionDocument(
            student_name=name,
            student_id=sid,
            course_code="CS146",
            assignment_title="Quiz 1",
            assignment_version="A",
            source_pdf=source_pdf.name,
            questions=[
                _question(
                    "q1",
                    10,
                    10 if index == 1 else 6,
                    segments=(QuestionSourceSegment(1, (0.1, 0.3, 0.5, 0.1)),),
                    response_text="answer one",
                ),
                _question("q2", 10, 8 if index == 1 else 4, response_text="answer two"),
            ],
        )
        (directory / f"student-{index}.submission.md").write_text(
            render_student_submission_markdown(document), encoding="utf-8"
        )


def test_default_bundle_name_uses_assignment_and_date() -> None:
    doc = StudentSubmissionDocument(
        student_name="A",
        student_id="1",
        course_code="CS146",
        assignment_title="Midterm Exam 2",
        assignment_version=None,
        source_pdf="a.pdf",
        questions=[],
    )
    name = default_bundle_name([doc], today=date(2026, 7, 28))
    assert name == "midterm-exam-2-return-2026-07-28"


def test_default_bundle_name_falls_back_when_untitled() -> None:
    doc = StudentSubmissionDocument(
        student_name="A",
        student_id="1",
        course_code=None,
        assignment_title="",
        assignment_version=None,
        source_pdf="a.pdf",
        questions=[],
    )
    assert default_bundle_name([doc], today=date(2026, 7, 28)) == (
        "assignment-return-2026-07-28"
    )


def test_build_all_produces_every_section(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_cohort(subs)
    out = tmp_path / "out"

    result = build_return_bundle(
        subs, out, content=BUNDLE_PRESETS["all"], name="quiz1-return"
    )

    assert result.bundle_dir == out / "quiz1-return"
    assert result.bundle_dir.is_dir()
    assert result.warnings == ()

    # Gradebook CSV.
    assert result.gradebook_path == result.bundle_dir / "gradebook" / "gradebook.csv"
    rows = list(csv.reader(result.gradebook_path.read_text().splitlines()))
    assert rows[0] == ["student_id", "student_name", "Q1", "Q2", "total", "possible"]
    assert {row[0] for row in rows[1:]} == {"111", "222"}

    # Student PDFs — one per student, each a valid multi-page PDF (scan + summary).
    assert result.student_count == 2
    for pdf_path in result.student_pdf_paths:
        assert pdf_path.parent == result.bundle_dir / "student-pdfs"
        with fitz.open(pdf_path) as doc:
            assert doc.page_count >= 2  # original page + appended summary

    # Analysis report + manifest.
    assert result.analysis_path == result.bundle_dir / "analysis" / "analysis-report.pdf"
    assert result.analysis_path.exists()
    assert (result.bundle_dir / "README.txt").exists()


def test_gradebook_only_skips_pdfs_and_analysis(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_cohort(subs)

    result = build_return_bundle(
        subs, tmp_path / "out", content=BUNDLE_PRESETS["gradebook"]
    )

    assert result.gradebook_path is not None and result.gradebook_path.exists()
    assert result.student_pdf_paths == ()
    assert result.analysis_path is None
    assert not (result.bundle_dir / "student-pdfs").exists()
    assert not (result.bundle_dir / "analysis").exists()


def test_anonymous_names_pdfs_by_id(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_cohort(subs)

    result = build_return_bundle(
        subs,
        tmp_path / "out",
        content=BUNDLE_PRESETS["student-pdfs"],
        anonymous=True,
    )

    stems = sorted(p.stem for p in result.student_pdf_paths)
    assert stems == ["111", "222"]  # ids only, no names in filenames


def test_missing_source_pdf_becomes_a_warning(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    document = StudentSubmissionDocument(
        student_name="No Scan",
        student_id="999",
        course_code="CS146",
        assignment_title="Quiz 1",
        assignment_version=None,
        source_pdf="does-not-exist.pdf",
        questions=[_question("q1", 10, 5)],
    )
    (subs / "x.submission.md").write_text(
        render_student_submission_markdown(document), encoding="utf-8"
    )

    result = build_return_bundle(
        subs, tmp_path / "out", content=BUNDLE_PRESETS["student-pdfs"]
    )

    assert result.student_pdf_paths == ()
    assert len(result.warnings) == 1
    assert "source PDF not found" in result.warnings[0]


def test_empty_content_is_rejected(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_cohort(subs)
    with pytest.raises(ValueError, match="no bundle content"):
        build_return_bundle(subs, tmp_path / "out", content=BundleContent())


def test_no_submissions_is_rejected(tmp_path: Path) -> None:
    subs = tmp_path / "empty"
    subs.mkdir()
    with pytest.raises(ValueError, match="no .* files found"):
        build_return_bundle(subs, tmp_path / "out", content=BUNDLE_PRESETS["all"])


def test_the_analysis_pdf_gains_an_outcomes_section(tmp_path) -> None:  # noqa: ANN001
    """The accreditation page: present when the answer key tags outcomes."""
    import fitz

    from opengrader.core.analytics import build_outcome_report
    from opengrader.core.analytics.item_analysis import build_item_analysis
    from opengrader.core.pdf.analysis_report import (
        render_analysis_report_pdf,
        score_distribution_from_totals,
    )
    from opengrader.core.qa_file import (
        outcomes_by_question,
        parse_qa_markdown,
        possible_points_by_question,
    )
    from opengrader.core.submissions import parse_student_submission_markdown

    qa = parse_qa_markdown(
        "---\ntitle: Quiz\n---\n\n"
        "- (10 points) Explain.\n  - **Correct**\n    - x\n  - **Outcome** CLO1\n"
        "- (10 points) Apply.\n  - **Correct**\n    - y\n"
    )
    docs = [
        parse_student_submission_markdown(
            "---\n"
            f'student_name: "S{i}"\nstudent_id: "{i}"\ncourse_code: C\n'
            'assignment_title: "Quiz"\nassignment_version: null\n'
            f'source_pdf: "{i}.pdf"\nscore: ""\n---\n\n\n'
            "- q1:\n  - Question type: Free Response\n  - Prompt: p\n"
            "  - Response text: a\n  - Possible points: 10\n"
            f"  - Points: {8 + i}\n  - Rubric:\n  - Analysis:\n\n"
            "- q2:\n  - Question type: Free Response\n  - Prompt: p\n"
            "  - Response text: a\n  - Possible points: 10\n"
            "  - Points: 5\n  - Rubric:\n  - Analysis:\n"
        )
        for i in range(2)
    ]
    outcomes = build_outcome_report(
        docs,
        outcomes_by_question(qa),
        possible_points_by_question=possible_points_by_question(qa),
    )
    output = render_analysis_report_pdf(
        tmp_path / "analysis.pdf",
        report=build_item_analysis(docs),
        distribution=score_distribution_from_totals([(13, 20), (14, 20)]),
        title="Quiz",
        outcomes=outcomes,
    )
    with fitz.open(output) as document:
        text = "".join(page.get_text() for page in document)
    assert "Learning outcomes" in text
    assert "CLO1" in text
    # Coverage is printed beside mastery so the number can't be over-read.
    assert "outcome tag" in text


def test_the_analysis_pdf_omits_outcomes_when_nothing_is_tagged(tmp_path) -> None:  # noqa: ANN001
    import fitz

    from opengrader.core.analytics.item_analysis import build_item_analysis
    from opengrader.core.pdf.analysis_report import (
        render_analysis_report_pdf,
        score_distribution_from_totals,
    )

    output = render_analysis_report_pdf(
        tmp_path / "analysis.pdf",
        report=build_item_analysis([]),
        distribution=score_distribution_from_totals([]),
        title="Quiz",
        outcomes=None,
    )
    with fitz.open(output) as document:
        text = "".join(page.get_text() for page in document)
    assert "Learning outcomes" not in text
