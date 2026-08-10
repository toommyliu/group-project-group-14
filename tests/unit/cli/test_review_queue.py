import json
from pathlib import Path

import pytest

from opengrader.cli.review_queue import run_cli


def _write_submission(
    path: Path,
    *,
    student_id: str = "123456789",
    student_name: str = "Tommy Liu",
    response_text: str = "an answer",
    analysis: str = "",
) -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                f'student_name: "{student_name}"',
                f'student_id: "{student_id}"',
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
                f"  - Response text: {response_text}",
                "  - Possible points: 10",
                "  - Points: ",
                "  - Rubric:",
                f"  - Analysis: {analysis}",
                "",
            ]
        )
    )


def test_review_queue_lists_flags_human_readable(tmp_path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_submission(subs / "a.submission.md", student_id="")  # missing id -> high
    _write_submission(subs / "b.submission.md", response_text="")  # empty resp -> low

    run_cli(["--input", str(subs)])

    out = capsys.readouterr().out
    assert "need review" in out
    assert "missing_student_id" in out
    assert "empty_response" in out
    # High severity is listed before low.
    assert out.index("HIGH") < out.index("LOW")


def test_review_queue_json_output(tmp_path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_submission(subs / "a.submission.md", student_id="")

    run_cli(["--input", str(subs), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["reason"] == "missing_student_id"
    assert payload[0]["severity"] == "high"


def test_review_queue_clean_set_reports_nothing(tmp_path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_submission(subs / "a.submission.md")

    run_cli(["--input", str(subs)])

    assert "No submissions need review" in capsys.readouterr().out


def test_review_queue_fail_on_high_exits_nonzero(tmp_path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_submission(subs / "a.submission.md", student_id="")

    with pytest.raises(SystemExit) as exc:
        run_cli(["--input", str(subs), "--fail-on-high"])
    assert exc.value.code == 2


def test_review_queue_errors_on_empty_directory(tmp_path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    with pytest.raises(SystemExit) as exc:
        run_cli(["--input", str(subs)])
    assert exc.value.code == 1


def _rubric_drift_journal(root: Path, *, student_id: str = "123456789") -> None:
    """One grade, then a rubric edit that leaves it behind."""
    from opengrader.core.history import GradeJournal, resolve_history_dir

    journal = GradeJournal(resolve_history_dir(root), actor="prof_kim")
    journal.append(
        kind="grade",
        student_id=student_id,
        student_name="Tommy Liu",
        question_id="q1",
        summary="8/10",
        old={"points": None, "rubric": []},
        new={"points": 8.0, "rubric": ["Correct"]},
    )
    journal.append(
        kind="rubric",
        student_id="",
        student_name="",
        question_id="q1",
        summary="Rubric edited",
        old={"options": ["Correct", "Incorrect"]},
        new={"options": ["Correct", "Partial - method", "Incorrect"]},
    )


def test_grades_awarded_before_a_rubric_edit_are_flagged(tmp_path, capsys) -> None:
    _write_submission(tmp_path / "a.submission.md")
    _rubric_drift_journal(tmp_path)

    run_cli(["--input", str(tmp_path), "--json"])
    flags = json.loads(capsys.readouterr().out)

    stale = [f for f in flags if f["reason"] == "graded_before_rubric_change"]
    assert len(stale) == 1
    assert stale[0]["question_id"] == "q1"
    assert "Partial - method" in stale[0]["detail"]


def test_the_rubric_drift_check_can_be_switched_off(tmp_path, capsys) -> None:
    _write_submission(tmp_path / "a.submission.md")
    _rubric_drift_journal(tmp_path)

    run_cli(["--input", str(tmp_path), "--json", "--no-rubric-drift"])
    flags = json.loads(capsys.readouterr().out)

    assert not [f for f in flags if f["reason"] == "graded_before_rubric_change"]


def test_a_folder_with_no_journal_reports_no_drift(tmp_path, capsys) -> None:
    """Grading that predates journalling leaves no trace, and none is asserted."""
    _write_submission(tmp_path / "a.submission.md")

    run_cli(["--input", str(tmp_path), "--json"])
    flags = json.loads(capsys.readouterr().out)

    assert not [f for f in flags if f["reason"] == "graded_before_rubric_change"]
