from __future__ import annotations

from pathlib import Path

import pytest

from opengrader.cli import run_command
from opengrader.cli.grade_backup import command
from opengrader.core.history import GradeJournal, resolve_history_dir
from opengrader.core.integrity import resolve_snapshot_path


@pytest.fixture()
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "subs"
    student = root / "sub-0001"
    (student / "responses").mkdir(parents=True)
    (student / "sub-0001.submission.md").write_text(
        """---
student_name: "Ada Lovelace"
student_id: "1001"
course_code: "CS146"
assignment_title: "Quiz 1"
source_pdf: ""
student_score: ""
---


- q1:
  - Question type: Free Response
  - Prompt: What is a loop?
  - Response text: a repeat
  - Possible points: 5
  - Points: 3
  - Rubric: Correct
  - Analysis:
"""
    )
    (student / "responses" / "q1.png").write_bytes(b"crop")
    return root


def test_snapshot_records_the_folder(folder: Path, capsys) -> None:
    run_command(command, ["snapshot", "--submissions", str(folder)])
    assert resolve_snapshot_path(folder).is_file()
    assert "Recorded" in capsys.readouterr().out


def test_re_snapshotting_says_it_replaced_the_old_one(folder: Path, capsys) -> None:
    run_command(command, ["snapshot", "--submissions", str(folder)])
    capsys.readouterr()
    run_command(command, ["snapshot", "--submissions", str(folder)])
    assert "Replaced the snapshot" in capsys.readouterr().out


def test_verify_without_a_snapshot_exits_with_advice(folder: Path, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run_command(command, ["verify", "--submissions", str(folder)])
    assert exit_info.value.code == 1
    assert "run 'snapshot' first" in capsys.readouterr().err


def test_verify_reports_a_clean_folder(folder: Path, capsys) -> None:
    run_command(command, ["snapshot", "--submissions", str(folder)])
    capsys.readouterr()
    run_command(command, ["verify", "--submissions", str(folder)])
    assert "Nothing needs attention" in capsys.readouterr().out


def test_verify_names_a_corrupted_file(folder: Path, capsys) -> None:
    run_command(command, ["snapshot", "--submissions", str(folder)])
    (folder / "sub-0001" / "responses" / "q1.png").write_bytes(b"bad")
    capsys.readouterr()
    run_command(command, ["verify", "--submissions", str(folder)])
    output = capsys.readouterr().out
    assert "need attention" in output
    assert "responses/q1.png" in output


def _journal_a_grade(folder: Path) -> None:
    GradeJournal(resolve_history_dir(folder), actor="prof").append(
        kind="grade",
        student_id="1001",
        student_name="Ada Lovelace",
        question_id="q1",
        summary="graded 5",
        old={"points": 3, "rubric": []},
        new={"points": 5, "rubric": []},
    )


def test_verify_flags_a_grade_change_with_nothing_in_the_journal(
    folder: Path, capsys
) -> None:
    _journal_a_grade(folder)
    run_command(command, ["snapshot", "--submissions", str(folder)])
    path = folder / "sub-0001" / "sub-0001.submission.md"
    path.write_text(path.read_text().replace("Points: 3", "Points: 5"))
    capsys.readouterr()
    run_command(command, ["verify", "--submissions", str(folder)])
    output = capsys.readouterr().out
    assert "no grading activity recorded since" in output
    assert "check these first" in output


def test_verify_explains_a_change_that_grading_accounts_for(
    folder: Path, capsys
) -> None:
    run_command(command, ["snapshot", "--submissions", str(folder)])
    path = folder / "sub-0001" / "sub-0001.submission.md"
    path.write_text(path.read_text().replace("Points: 3", "Points: 5"))
    _journal_a_grade(folder)
    capsys.readouterr()
    run_command(command, ["verify", "--submissions", str(folder)])
    output = capsys.readouterr().out
    assert "1 grading action(s) recorded since" in output
    assert "check these first" not in output


def test_verify_fail_on_change_exits_two_for_scripts(folder: Path) -> None:
    run_command(command, ["snapshot", "--submissions", str(folder)])
    (folder / "sub-0001" / "responses" / "q1.png").write_bytes(b"bad")
    with pytest.raises(SystemExit) as exit_info:
        run_command(command, ["verify", "--submissions", str(folder), "--fail-on-change"])
    assert exit_info.value.code == 2


def test_verify_all_lists_the_untouched_files_too(folder: Path, capsys) -> None:
    run_command(command, ["snapshot", "--submissions", str(folder)])
    capsys.readouterr()
    run_command(command, ["verify", "--submissions", str(folder), "--all"])
    assert "unchanged: sub-0001/responses/q1.png" in capsys.readouterr().out


def test_backup_writes_and_verifies_an_archive(folder: Path, tmp_path: Path, capsys) -> None:
    archive = tmp_path / "b.zip"
    run_command(command, ["backup", "--submissions", str(folder), "--output", str(archive)])
    assert archive.is_file()
    assert "read back and verified" in capsys.readouterr().out.lower()


def test_backup_can_record_the_snapshot_at_the_same_time(
    folder: Path, tmp_path: Path
) -> None:
    run_command(
        command,
        [
            "backup",
            "--submissions",
            str(folder),
            "--output",
            str(tmp_path / "b.zip"),
            "--snapshot",
        ]
    )
    assert resolve_snapshot_path(folder).is_file()


def test_backup_refuses_to_overwrite(folder: Path, tmp_path: Path, capsys) -> None:
    archive = tmp_path / "b.zip"
    run_command(command, ["backup", "--submissions", str(folder), "--output", str(archive)])
    with pytest.raises(SystemExit) as exit_info:
        run_command(command, ["backup", "--submissions", str(folder), "--output", str(archive)])
    assert exit_info.value.code == 1
    assert "never overwritten" in capsys.readouterr().err


def test_check_reports_an_intact_archive(folder: Path, tmp_path: Path, capsys) -> None:
    archive = tmp_path / "b.zip"
    run_command(command, ["backup", "--submissions", str(folder), "--output", str(archive)])
    capsys.readouterr()
    run_command(command, ["check", "--archive", str(archive)])
    assert "intact" in capsys.readouterr().out


def test_restore_unpacks_into_a_new_folder(folder: Path, tmp_path: Path, capsys) -> None:
    archive = tmp_path / "b.zip"
    run_command(command, ["backup", "--submissions", str(folder), "--output", str(archive)])
    capsys.readouterr()
    run_command(command, ["restore", "--archive", str(archive), "--output", str(tmp_path / "new")])
    assert (tmp_path / "new" / "sub-0001" / "sub-0001.submission.md").is_file()
    assert "matches the archive's manifest" in capsys.readouterr().out


def test_restore_refuses_an_existing_destination(folder: Path, tmp_path: Path, capsys) -> None:
    archive = tmp_path / "b.zip"
    run_command(command, ["backup", "--submissions", str(folder), "--output", str(archive)])
    with pytest.raises(SystemExit) as exit_info:
        run_command(command, ["restore", "--archive", str(archive), "--output", str(folder)])
    assert exit_info.value.code == 1
    assert "already exists" in capsys.readouterr().err


def test_backup_without_output_writes_beside_the_folder(folder: Path, capsys) -> None:
    run_command(command, ["backup", "--submissions", str(folder)])
    written = list(folder.parent.glob("*.zip"))
    assert len(written) == 1
    assert written[0].name.startswith(f"{folder.name}-backup-")
    assert not list(folder.rglob("*.zip"))
    assert str(written[0]) in capsys.readouterr().out


def test_backup_into_the_folder_is_refused_with_the_reason(folder: Path, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run_command(
            command,
            ["backup", "--submissions", str(folder), "--output", str(folder / "b.zip")]
        )
    assert exit_info.value.code == 1
    assert "inside the folder being backed up" in capsys.readouterr().err


def test_backup_encrypt_writes_an_enc_archive(
    folder: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("OPENGRADER_PASSPHRASE", "correct horse")
    archive = tmp_path / "quiz1.zip.enc"
    run_command(
        command,
        [
            "backup",
            "--submissions",
            str(folder),
            "--output",
            str(archive),
            "--encrypt",
        ]
    )
    out = capsys.readouterr().out
    assert archive.is_file()
    assert "Encrypted" in out
    assert "store it somewhere" in out


def test_check_and_restore_ask_for_the_passphrase_themselves(
    folder: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("OPENGRADER_PASSPHRASE", "p")
    archive = tmp_path / "quiz1.zip.enc"
    run_command(command, ["backup", "--submissions", str(folder), "--output", str(archive), "--encrypt"])

    capsys.readouterr()
    run_command(command, ["check", "--archive", str(archive)])
    assert "intact" in capsys.readouterr().out

    run_command(command, ["restore", "--archive", str(archive), "--output", str(tmp_path / "back")])
    assert (tmp_path / "back").is_dir()


def test_an_unencrypted_backup_still_needs_no_passphrase(
    folder: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("OPENGRADER_PASSPHRASE", "p")
    archive = tmp_path / "plain.zip"
    run_command(command, ["backup", "--submissions", str(folder), "--output", str(archive)])
    capsys.readouterr()
    run_command(command, ["check", "--archive", str(archive)])
    assert "intact" in capsys.readouterr().out
