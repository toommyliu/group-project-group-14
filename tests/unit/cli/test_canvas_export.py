from pathlib import Path

import pytest

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

    canvas_export.run_cli(
        ["--course", "CS146", "--assignment", "Midterm", "--dry-run"]
    )

    assert posted == [7.0]
