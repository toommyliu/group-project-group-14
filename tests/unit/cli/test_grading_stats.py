import json
from pathlib import Path

import pytest

from opengrader.cli.grading_stats import run_cli
from opengrader.core.history import GradeJournal, resolve_history_dir


def _seed_journal(submissions: Path) -> None:
    history_dir = resolve_history_dir(submissions)
    journal = GradeJournal(history_dir, actor="prof_kim")
    journal.append(
        kind="grade", student_id="s1", student_name="", question_id="q1", summary="8/10"
    )
    journal.append(
        kind="grade", student_id="s2", student_name="", question_id="q1", summary="6/10"
    )
    journal.append(
        kind="comment", student_id="s1", student_name="", question_id="q2", summary="note"
    )


def test_grading_stats_human_output(tmp_path: Path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _seed_journal(subs)

    run_cli(["--input", str(subs)])

    out = capsys.readouterr().out
    assert "3 action(s) by 1 grader(s)" in out
    assert "prof_kim" in out
    assert "Grades: 2" in out


def test_grading_stats_json_output(tmp_path: Path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _seed_journal(subs)

    run_cli(["--input", str(subs), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["total_events"] == 3
    assert payload["grades"] == 2
    assert payload["comments"] == 1
    assert payload["distinct_actors"] == 1
    assert payload["actors"][0]["actor"] == "prof_kim"


def test_grading_stats_empty_history(tmp_path: Path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()

    run_cli(["--input", str(subs)])

    assert "No grading activity recorded" in capsys.readouterr().out


def test_grading_stats_errors_on_missing_input(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        run_cli(["--input", str(tmp_path / "nope")])
    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err
