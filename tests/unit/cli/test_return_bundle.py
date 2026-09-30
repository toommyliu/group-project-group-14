from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from opengrader.cli import run_command
from opengrader.cli.return_bundle import _resolve_content, command
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


def _flags(**selected) -> dict:
    return {
        "content_preset": None,
        "gradebook": False,
        "student_pdfs": False,
        "analysis": False,
        "include_all": False,
        **selected,
    }


def test_resolve_content_defaults_to_all_when_nothing_selected() -> None:
    content = _resolve_content(**_flags())
    assert (content.gradebook, content.student_pdfs, content.analysis) == (True, True, True)


def test_resolve_content_unions_individual_flags() -> None:
    content = _resolve_content(**_flags(gradebook=True, analysis=True))
    assert (content.gradebook, content.student_pdfs, content.analysis) == (True, False, True)


def test_resolve_content_preset() -> None:
    content = _resolve_content(**_flags(content_preset="student-pdfs"))
    assert (content.gradebook, content.student_pdfs, content.analysis) == (False, True, False)


def test_cli_all_writes_bundle(tmp_path: Path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_cohort(subs)
    out = tmp_path / "out"

    run_command(command, ["--submissions", str(subs), "--output", str(out), "--name", "b1", "--all"])

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

    run_command(command, ["--submissions", str(subs), "--output", str(out), "--name", "b2", "--gradebook"])

    bundle = out / "b2"
    assert (bundle / "gradebook" / "gradebook.csv").exists()
    assert not (bundle / "student-pdfs").exists()
    assert not (bundle / "analysis").exists()


def test_cli_output_defaults_to_submissions_folder(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_cohort(subs)

    run_command(command, ["--submissions", str(subs), "--name", "b3", "--gradebook"])

    assert (subs / "b3" / "gradebook" / "gradebook.csv").exists()


def test_cli_errors_on_missing_path(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        run_command(command, ["--submissions", str(tmp_path / "nope"), "--gradebook"])
    assert excinfo.value.code == 1
    assert "error:" in capsys.readouterr().err


def test_cli_workspace_bundle_uses_saved_grades_and_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opengrader.core.submissions import load_student_submission_markdown
    from opengrader.core.workspace.intake import publish_submission
    from opengrader.core.workspace.models import AssignmentInputs
    from opengrader.core.workspace.store import Workspace

    course = tmp_path / "course"
    folder = course / "quiz"
    folder.mkdir(parents=True)
    _write_cohort(folder)
    workspace = Workspace.create(course)
    assignment_id = workspace.assignment_id_for_folder(folder)
    workspace.save_assignment_inputs(
        AssignmentInputs(id=assignment_id, folder="quiz", scan="scans.pdf", output=".")
    )
    workspace.create_assignment(
        "- (10 points) First question\n  - **Answer**\n    - Yes\n",
        assignment_id=assignment_id,
    )
    for index in (1, 2):
        source = folder / f"s{index}.submission.md"
        publish_submission(
            workspace,
            assignment_id,
            load_student_submission_markdown(source),
            asset_root=folder,
            source_key=f"s{index}",
        )
        source.write_text("stale extraction", encoding="utf-8")

    monkeypatch.chdir(folder)
    run_command(command, ["--name", "return", "--content", "gradebook-student"])

    bundle = folder / "return"
    assert (bundle / "gradebook" / "gradebook.csv").exists()
    assert len(list((bundle / "student-pdfs").glob("*.pdf"))) == 2
    assert "Tommy Liu" in (bundle / "gradebook" / "gradebook.csv").read_text()
