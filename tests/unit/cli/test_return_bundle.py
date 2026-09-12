from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from opengrader.cli.return_bundle import _resolve_content, run_cli
from opengrader.core.submissions import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    render_student_submission_markdown,
)


def _write_cohort(directory: Path) -> None:
    for index, (name, sid) in enumerate([("Tommy Liu", "111"), ("Andy Cortes", "222")], 1):
        source_pdf = directory / f"submission-{index}.pdf"
        doc = fitz.open()
        doc.new_page(width=612, height=792)
        doc.save(source_pdf)
        doc.close()
        document = StudentSubmissionDocument(
            student_name=name,
            student_id=sid,
            course_code="CS146",
            assignment_title="Quiz 1",
            assignment_version=None,
            source_pdf=source_pdf.name,
            questions=[
                StudentSubmissionQuestion(
                    question_id="q1",
                    prompt="",
                    possible_points=10,
                    response_image_path=None,
                    response_text="answer",
                    points_awarded=8,
                )
            ],
        )
        (directory / f"s{index}.submission.md").write_text(
            render_student_submission_markdown(document), encoding="utf-8"
        )


class _Args:
    def __init__(self, **kwargs) -> None:
        self.content = kwargs.get("content")
        self.gradebook = kwargs.get("gradebook", False)
        self.student_pdfs = kwargs.get("student_pdfs", False)
        self.analysis = kwargs.get("analysis", False)
        self.all = kwargs.get("all", False)


def test_resolve_content_defaults_to_all_when_nothing_selected() -> None:
    content = _resolve_content(_Args())
    assert (content.gradebook, content.student_pdfs, content.analysis) == (True, True, True)


def test_resolve_content_unions_individual_flags() -> None:
    content = _resolve_content(_Args(gradebook=True, analysis=True))
    assert (content.gradebook, content.student_pdfs, content.analysis) == (True, False, True)


def test_resolve_content_preset() -> None:
    content = _resolve_content(_Args(content="student-pdfs"))
    assert (content.gradebook, content.student_pdfs, content.analysis) == (False, True, False)


def test_cli_all_writes_bundle(tmp_path: Path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_cohort(subs)
    out = tmp_path / "out"

    run_cli(["--submissions", str(subs), "--output", str(out), "--name", "b1", "--all"])

    bundle = out / "b1"
    assert (bundle / "gradebook" / "gradebook.csv").exists()
    assert len(list((bundle / "student-pdfs").glob("*.pdf"))) == 2
    assert (bundle / "analysis" / "analysis-report.pdf").exists()
    printed = capsys.readouterr().out
    assert "Bundle written to" in printed


def test_cli_gradebook_flag_only(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_cohort(subs)
    out = tmp_path / "out"

    run_cli(["--submissions", str(subs), "--output", str(out), "--name", "b2", "--gradebook"])

    bundle = out / "b2"
    assert (bundle / "gradebook" / "gradebook.csv").exists()
    assert not (bundle / "student-pdfs").exists()
    assert not (bundle / "analysis").exists()


def test_cli_output_defaults_to_submissions_folder(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_cohort(subs)

    run_cli(["--submissions", str(subs), "--name", "b3", "--gradebook"])

    assert (subs / "b3" / "gradebook" / "gradebook.csv").exists()


def test_cli_errors_on_missing_path(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        run_cli(["--submissions", str(tmp_path / "nope"), "--gradebook"])
    assert excinfo.value.code == 1
    assert "error:" in capsys.readouterr().err
