from pathlib import Path

import pytest

from opengrader.cli import run_command
from opengrader.cli.regrade import command
from opengrader.core.submissions.regrade import (
    open_regrade_requests,
    read_regrade_requests,
)


def _add(subs: Path, *args: str) -> None:
    run_command(command, ["add", "--submissions", str(subs), *args])


def test_add_files_a_request(tmp_path: Path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _add(subs, "--student", "012345", "--question", "q3", "--reason", "wrong")

    out = capsys.readouterr().out
    assert "Filed regrade request" in out
    requests = read_regrade_requests(subs)
    assert len(requests) == 1
    assert requests[0].student_id == "012345"
    assert requests[0].question_id == "q3"
    assert requests[0].reason == "wrong"


def test_add_without_question_is_whole_assignment(tmp_path: Path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _add(subs, "--student", "012345", "--reason", "total off")
    out = capsys.readouterr().out
    assert "the whole assignment" in out
    assert read_regrade_requests(subs)[0].question_id == ""


def test_list_shows_open_by_default(tmp_path: Path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _add(subs, "--student", "012345", "--question", "q3", "--reason", "wrong")
    capsys.readouterr()

    run_command(command, ["list", "--submissions", str(subs)])
    out = capsys.readouterr().out
    assert "1 request (1 open)" in out
    assert "q3" in out


def test_resolve_then_list_hides_it_unless_all(tmp_path: Path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _add(subs, "--student", "012345", "--reason", "wrong")
    capsys.readouterr()

    request_id = read_regrade_requests(subs)[0].request_id
    run_command(command, ["resolve", request_id, "--submissions", str(subs), "--note", "regraded +5"])
    assert capsys.readouterr().out.strip() == f"Resolved {request_id}."
    assert open_regrade_requests(subs) == []

    run_command(command, ["list", "--submissions", str(subs)])
    assert "No open requests" in capsys.readouterr().out

    run_command(command, ["list", "--submissions", str(subs), "--all"])
    all_out = capsys.readouterr().out
    assert "resolved" in all_out
    assert "regraded +5" in all_out


def test_deny_marks_denied(tmp_path: Path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _add(subs, "--student", "012345", "--reason", "wrong")
    request_id = read_regrade_requests(subs)[0].request_id
    capsys.readouterr()

    run_command(command, ["deny", request_id, "--submissions", str(subs), "--note", "answer is wrong"])
    assert f"Denied {request_id}." in capsys.readouterr().out
    assert read_regrade_requests(subs)[0].status == "denied"


def test_resolve_unknown_id_exits_nonzero(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    with pytest.raises(SystemExit) as exc:
        run_command(command, ["resolve", "rg-nope", "--submissions", str(subs)])
    assert exc.value.code == 1


def test_missing_submissions_path_exits(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        run_command(command, ["list", "--submissions", str(tmp_path / "nope")])
    assert exc.value.code == 1
