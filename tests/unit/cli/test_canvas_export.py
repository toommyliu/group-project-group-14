from pathlib import Path

import pytest

from opengrader.cli import run_command
from opengrader.cli import canvas_export
from opengrader.core.submissions import load_student_submission_markdown
from opengrader.core.workspace.intake import publish_submission
from opengrader.core.workspace.store import Workspace


def test_canvas_export_from_workspace_uses_saved_grades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = Workspace.create(tmp_path / "course")
    assignment = workspace.create_assignment(
        "- (10 points) First question\n  - **Answer**\n    - Yes\n"
    )
    source = tmp_path / "one.submission.md"
    source.write_text(
        "---\nstudent_id: '123'\nstudent_name: Alice\n---\n"
        "- q1:\n  - Response text: Yes\n  - Possible points: 10\n  - Points: 7\n"
    )
    publish_submission(
        workspace,
        assignment.id,
        load_student_submission_markdown(source),
        asset_root=tmp_path,
        source_key="one",
    )
    source.write_text("invalid extraction after grading")
    monkeypatch.chdir(workspace.root)
    monkeypatch.setattr(canvas_export, "_check_canvas_sak_installed", lambda: True)
    posted: list[float] = []

    def record(**kwargs):
        posted.append(kwargs["grade"])
        return True, ""

    monkeypatch.setattr(canvas_export, "_call_canvas_sak", record)

    run_command(
        canvas_export.command,
        ["--course", "CS146", "--assignment", "Midterm", "--dry-run"]
    )

    assert posted == [7.0]


def test_canvas_export_posts_one_section_and_reads_nested_submissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    # A pipeline's submissions/ holds one folder per student.
    for sid, section in (("1", "Section 01"), ("2", "Section 02")):
        folder = tmp_path / "submissions" / f"s{sid}"
        folder.mkdir(parents=True)
        (folder / f"s{sid}.submission.md").write_text(
            f"---\nstudent_id: '{sid}'\nstudent_name: S{sid}\nsection: {section}\n---\n"
            "- q1:\n  - Response text: Yes\n  - Possible points: 10\n  - Points: 7\n"
        )
    monkeypatch.setattr(canvas_export, "_check_canvas_sak_installed", lambda: True)
    posted: list[str] = []

    def record(**kwargs):
        posted.append(kwargs["sisid"])
        return True, ""

    monkeypatch.setattr(canvas_export, "_call_canvas_sak", record)

    run_command(
        canvas_export.command,
        [
            "--submissions", str(tmp_path / "submissions"),
            "--course", "CS146-01", "--assignment", "Quiz 1",
            "--section", "section 1", "--dry-run",
        ],
    )

    assert posted == ["1"]
    assert "Section 'section 1' only: 1 submission(s)." in capsys.readouterr().out


def test_canvas_export_names_a_section_nobody_is_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.submission.md").write_text(
        "---\nstudent_id: '1'\nstudent_name: A\nsection: Section 01\n---\n"
        "- q1:\n  - Possible points: 10\n  - Points: 7\n"
    )
    monkeypatch.setattr(canvas_export, "_check_canvas_sak_installed", lambda: True)

    with pytest.raises(SystemExit) as raised:
        run_command(
            canvas_export.command,
            ["--submissions", str(tmp_path), "--course", "C", "--assignment", "A",
             "--section", "Section 9", "--dry-run"],
        )
    assert raised.value.code == 1
