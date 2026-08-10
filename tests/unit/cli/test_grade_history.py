import json
from pathlib import Path

import pytest

from opengrader.cli.grade_history import run_cli
from opengrader.core.history import GradeJournal, resolve_history_dir


def _seed_history(submissions: Path) -> None:
    history = resolve_history_dir(submissions)
    a = GradeJournal(history, actor="prof_kim", session_id="a")
    b = GradeJournal(history, actor="ta_bob", session_id="b")
    a.append(
        kind="grade",
        student_id="111",
        student_name="Amy",
        question_id="q1",
        summary="8/10 pts",
        old={"points": None},
        new={"points": 8},
    )
    b.append(
        kind="comment",
        student_id="222",
        student_name="Bob",
        question_id="q2",
        summary="“see rubric”",
    )


def test_grade_history_lists_all_graders(tmp_path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _seed_history(subs)

    run_cli(["--input", str(subs)])

    out = capsys.readouterr().out
    assert "2 change(s) by 2 grader(s)" in out
    assert "prof_kim" in out and "ta_bob" in out
    assert "8/10 pts" in out


def test_grade_history_filters_by_actor(tmp_path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _seed_history(subs)

    run_cli(["--input", str(subs), "--actor", "ta_bob", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 1
    assert payload[0]["actor"] == "ta_bob"
    assert payload[0]["kind"] == "comment"


def test_grade_history_filters_by_student(tmp_path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _seed_history(subs)

    run_cli(["--input", str(subs), "--student", "111", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert [e["student_id"] for e in payload] == ["111"]


def test_grade_history_empty_reports_nothing(tmp_path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()

    run_cli(["--input", str(subs)])

    assert "No grade history recorded yet." in capsys.readouterr().out


def test_grade_history_errors_on_missing_input(tmp_path) -> None:
    with pytest.raises(SystemExit) as exc:
        run_cli(["--input", str(tmp_path / "nope")])
    assert exc.value.code == 1
