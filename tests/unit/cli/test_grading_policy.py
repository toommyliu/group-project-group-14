from pathlib import Path

import pytest

from opengrader.cli.grading_policy import run_cli
from opengrader.core.policy import read_policy
from opengrader.core.submissions.student_file import load_student_submission_markdown


def _write_submission(path: Path, *, student_name: str = "Tommy Liu") -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                f'student_name: "{student_name}"',
                'student_id: "123456789"',
                'course_code: "CS146"',
                'assignment_title: "Quiz 1"',
                'assignment_version: "A"',
                'source_pdf: "s.pdf"',
                'student_score: ""',
                "---",
                "",
                "- q1:",
                "  - Question type: Free Response",
                "  - Prompt: ",
                '  - Source segments: [{"page":1,"bbox":[0.2,0.3,0.5,0.2]}]',
                "  - Response text: an answer",
                "  - Possible points: 10",
                "  - Points: ",
                "  - Rubric:",
                "  - Analysis: ",
                "",
            ]
        )
    )


def test_lock_then_unlock_updates_policy(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()

    run_cli(["lock", "--submissions", str(subs)])
    assert read_policy(subs).anonymity_locked is True
    assert read_policy(subs).mode == "multi"

    run_cli(["unlock", "--submissions", str(subs)])
    assert read_policy(subs).anonymity_locked is False
    assert read_policy(subs).mode == "solo"


def test_show_reports_current_policy(tmp_path: Path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    run_cli(["lock", "--submissions", str(subs)])
    capsys.readouterr()  # drop the lock output

    run_cli(["show", "--submissions", str(subs)])

    out = capsys.readouterr().out
    assert "anonymity_locked: True" in out
    assert "LOCKED" in out


def test_redact_writes_map_and_strips_names(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_submission(subs / "a.submission.md", student_name="Ann Lee")
    name_map = tmp_path / "names.json"

    run_cli(["redact-names", "--submissions", str(subs), "--name-map", str(name_map)])

    assert name_map.exists()
    assert load_student_submission_markdown(subs / "a.submission.md").student_name == ""


def test_redact_refuses_to_overwrite_existing_map(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_submission(subs / "a.submission.md")
    name_map = tmp_path / "names.json"
    name_map.write_text("{}")  # pretend a map is already there

    with pytest.raises(SystemExit):
        run_cli(
            ["redact-names", "--submissions", str(subs), "--name-map", str(name_map)]
        )
    # The submission must be untouched since we bailed before redacting.
    assert load_student_submission_markdown(subs / "a.submission.md").student_name != ""


def test_restore_round_trips_from_the_written_map(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_submission(subs / "a.submission.md", student_name="Ann Lee")
    name_map = tmp_path / "names.json"

    run_cli(["redact-names", "--submissions", str(subs), "--name-map", str(name_map)])
    run_cli(["restore-names", "--submissions", str(subs), "--name-map", str(name_map)])

    assert load_student_submission_markdown(subs / "a.submission.md").student_name == "Ann Lee"


def test_missing_submissions_path_exits(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        run_cli(["show", "--submissions", str(tmp_path / "nope")])
