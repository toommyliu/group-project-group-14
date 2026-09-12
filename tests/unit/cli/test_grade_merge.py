from __future__ import annotations

from pathlib import Path

import pytest

from opengrader.cli.grade_merge import run_cli
from opengrader.core.handoff.packet import (
    default_packet_output_dir,
    resolve_handoff_dir,
    seal_packet,
    unseal_packet,
)
from opengrader.core.history import GradeJournal, read_journal, resolve_history_dir
from opengrader.core.history.restore import AspectValue, journal_payload
from opengrader.core.submissions.student_file import (
    StudentSubmissionGradeUpdate,
    load_student_submission_markdown,
    update_student_submission_file,
)

SUBMISSION = """---
student_name: "{name}"
student_id: "{sid}"
course_code: "CS146"
assignment_title: "Quiz 1"
assignment_version: "A"
source_pdf: ""
student_score: ""
---


- q1:
  - Question type: Free Response
  - Prompt: What is a loop?
  - Response text: a repeat
  - Possible points: 5
  - Points:
  - Rubric:
  - Analysis:

- q2:
  - Question type: Free Response
  - Prompt: What is a stack?
  - Response text: LIFO
  - Possible points: 5
  - Points:
  - Rubric:
  - Analysis:
"""


@pytest.fixture()
def cohort(tmp_path: Path) -> Path:
    root = tmp_path / "master"
    for index, (name, sid) in enumerate(
        [("Ada Lovelace", "1001"), ("Alan Turing", "1002")], start=1
    ):
        student = root / f"sub-{index:04d}"
        student.mkdir(parents=True)
        (student / f"sub-{index:04d}.submission.md").write_text(
            SUBMISSION.format(name=name, sid=sid)
        )
    return root


def _grade(folder: Path, actor: str, question_id: str, points: float, *, index: int = 0):
    paths = sorted(folder.glob("*/*.submission.md"))
    path = paths[index]
    document = load_student_submission_markdown(path)
    before = next(q for q in document.questions if q.question_id == question_id)
    update_student_submission_file(
        path,
        StudentSubmissionGradeUpdate(
            question_id=question_id,
            points_awarded=points,
            rubric_labels=("Correct",),
        ),
    )
    GradeJournal(resolve_history_dir(folder), actor=actor).append(
        kind="grade",
        student_id=document.student_id,
        student_name=document.student_name,
        question_id=question_id,
        summary=f"graded {points}",
        old=journal_payload(
            "grade",
            AspectValue(points=before.points_awarded, rubric=tuple(before.rubric_labels)),
        ),
        new=journal_payload("grade", AspectValue(points=points, rubric=("Correct",))),
    )


def _points(folder: Path, question_id: str, *, index: int = 0) -> float | None:
    path = sorted(folder.glob("*/*.submission.md"))[index]
    document = load_student_submission_markdown(path)
    return next(q for q in document.questions if q.question_id == question_id).points_awarded


def _pack(cohort: Path, out: Path, name: str = "p1") -> Path:
    run_cli(["pack", "--submissions", str(cohort), "--output", str(out), "--name", name])
    return out / name


def test_pack_reports_the_folder_and_the_key_separately(
    cohort: Path, tmp_path: Path, capsys
) -> None:
    packet = _pack(cohort, tmp_path / "out")
    output = capsys.readouterr().out
    assert str(packet) in output
    assert "Do NOT send the key" in output
    assert (resolve_handoff_dir(cohort) / "p1.json").is_file()


def test_pack_refuses_scans_in_a_redacted_packet(cohort: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run_cli(
            [
                "pack",
                "--submissions",
                str(cohort),
                "--output",
                str(tmp_path / "out"),
                "--with-pdfs",
            ]
        )
    assert exit_info.value.code == 2


def test_list_shows_the_packets_handed_out(cohort: Path, tmp_path: Path, capsys) -> None:
    _pack(cohort, tmp_path / "out", "for-alice")
    capsys.readouterr()
    run_cli(["list", "--submissions", str(cohort)])
    output = capsys.readouterr().out
    assert "for-alice" in output and "students: 2" in output


def test_preview_writes_nothing(cohort: Path, tmp_path: Path, capsys) -> None:
    packet = _pack(cohort, tmp_path / "out")
    _grade(packet / "submissions", "alice", "q1", 5.0)
    before = sorted(p.read_bytes() for p in cohort.glob("*/*.submission.md"))

    capsys.readouterr()
    run_cli(["preview", "--submissions", str(cohort), "--incoming", str(packet)])
    output = capsys.readouterr().out

    assert "1 change(s) to take" in output
    assert "Ada Lovelace" in output
    assert sorted(p.read_bytes() for p in cohort.glob("*/*.submission.md")) == before


def test_apply_takes_their_work_and_keeps_ours(cohort: Path, tmp_path: Path) -> None:
    packet = _pack(cohort, tmp_path / "out")
    _grade(packet / "submissions", "alice", "q1", 5.0)
    _grade(cohort, "prof", "q2", 4.0)

    run_cli(["apply", "--submissions", str(cohort), "--incoming", str(packet), "--yes"])

    assert _points(cohort, "q1") == 5.0
    assert _points(cohort, "q2") == 4.0


def test_apply_without_yes_is_a_preview(cohort: Path, tmp_path: Path, capsys) -> None:
    packet = _pack(cohort, tmp_path / "out")
    _grade(packet / "submissions", "alice", "q1", 5.0)
    capsys.readouterr()
    run_cli(["apply", "--submissions", str(cohort), "--incoming", str(packet)])
    assert "Re-run with --yes" in capsys.readouterr().out
    assert _points(cohort, "q1") is None


def test_a_conflict_is_left_alone_by_default(cohort: Path, tmp_path: Path, capsys) -> None:
    packet = _pack(cohort, tmp_path / "out")
    _grade(packet / "submissions", "alice", "q1", 2.0)
    _grade(cohort, "prof", "q1", 5.0)

    capsys.readouterr()
    run_cli(["apply", "--submissions", str(cohort), "--incoming", str(packet), "--yes"])
    assert "1 conflict(s)" in capsys.readouterr().out
    assert _points(cohort, "q1") == 5.0


def test_conflicts_theirs_takes_the_incoming_answer(cohort: Path, tmp_path: Path) -> None:
    packet = _pack(cohort, tmp_path / "out")
    _grade(packet / "submissions", "alice", "q1", 2.0)
    _grade(cohort, "prof", "q1", 5.0)

    run_cli(
        [
            "apply",
            "--submissions",
            str(cohort),
            "--incoming",
            str(packet),
            "--conflicts",
            "theirs",
            "--yes",
        ]
    )
    assert _points(cohort, "q1") == 2.0


def test_the_merge_is_journalled_under_an_actor_naming_the_packet(
    cohort: Path, tmp_path: Path
) -> None:
    packet = _pack(cohort, tmp_path / "out")
    _grade(packet / "submissions", "alice", "q1", 5.0)
    run_cli(["apply", "--submissions", str(cohort), "--incoming", str(packet), "--yes"])

    actors = {entry.actor for entry in read_journal(resolve_history_dir(cohort))}
    assert "merge:p1" in actors


def test_a_merge_can_be_undone_with_one_grade_restore_command(
    cohort: Path, tmp_path: Path
) -> None:
    from opengrader.cli.grade_restore import run_cli as run_restore_cli

    packet = _pack(cohort, tmp_path / "out")
    _grade(packet / "submissions", "alice", "q1", 5.0)
    run_cli(["apply", "--submissions", str(cohort), "--incoming", str(packet), "--yes"])
    assert _points(cohort, "q1") == 5.0

    run_restore_cli(
        [
            "apply",
            "--submissions",
            str(cohort),
            "--without-actor",
            "merge:p1",
            "--yes",
        ]
    )
    assert _points(cohort, "q1") is None


def test_history_import_is_off_by_default(cohort: Path, tmp_path: Path) -> None:
    packet = _pack(cohort, tmp_path / "out")
    _grade(packet / "submissions", "alice", "q1", 5.0)
    run_cli(["apply", "--submissions", str(cohort), "--incoming", str(packet), "--yes"])
    assert "alice" not in {e.actor for e in read_journal(resolve_history_dir(cohort))}


def test_history_import_de_aliases_and_names_the_extra_undo_actor(
    cohort: Path, tmp_path: Path, capsys
) -> None:
    packet = _pack(cohort, tmp_path / "out")
    _grade(packet / "submissions", "alice", "q1", 5.0)
    capsys.readouterr()
    run_cli(
        [
            "apply",
            "--submissions",
            str(cohort),
            "--incoming",
            str(packet),
            "--yes",
            "--import-history",
        ]
    )
    output = capsys.readouterr().out

    imported = [e for e in read_journal(resolve_history_dir(cohort)) if e.actor == "alice"]
    assert imported, "the other grader's session should be readable here"
    assert imported[0].student_id == "1001"
    assert imported[0].student_name == "Ada Lovelace"
    assert "--without-actor alice" in output


def test_importing_twice_does_not_double_the_entries(cohort: Path, tmp_path: Path) -> None:
    packet = _pack(cohort, tmp_path / "out")
    _grade(packet / "submissions", "alice", "q1", 5.0)
    argv = [
        "apply",
        "--submissions",
        str(cohort),
        "--incoming",
        str(packet),
        "--yes",
        "--import-history",
    ]
    run_cli(argv)
    first = len([e for e in read_journal(resolve_history_dir(cohort)) if e.actor == "alice"])
    run_cli(argv)
    second = len([e for e in read_journal(resolve_history_dir(cohort)) if e.actor == "alice"])
    assert first == second


def test_a_redacted_packet_without_its_key_is_refused(
    cohort: Path, tmp_path: Path, capsys
) -> None:
    packet = _pack(cohort, tmp_path / "out")
    _grade(packet / "submissions", "alice", "q1", 5.0)
    (resolve_handoff_dir(cohort) / "p1.json").unlink()

    with pytest.raises(SystemExit) as exit_info:
        run_cli(["preview", "--submissions", str(cohort), "--incoming", str(packet)])
    assert exit_info.value.code == 2
    assert "handoff key was not found" in capsys.readouterr().err


def test_a_moved_key_can_be_named_explicitly(cohort: Path, tmp_path: Path, capsys) -> None:
    packet = _pack(cohort, tmp_path / "out")
    _grade(packet / "submissions", "alice", "q1", 5.0)
    moved = tmp_path / "elsewhere.json"
    original = resolve_handoff_dir(cohort) / "p1.json"
    moved.write_text(original.read_text())
    original.unlink()

    capsys.readouterr()
    run_cli(
        [
            "preview",
            "--submissions",
            str(cohort),
            "--incoming",
            str(packet),
            "--key",
            str(moved),
        ]
    )
    assert "1 change(s) to take" in capsys.readouterr().out


def test_a_plain_unredacted_copy_merges_with_no_key_at_all(
    cohort: Path, tmp_path: Path, capsys
) -> None:
    copy = tmp_path / "copy"
    copy.mkdir()
    for source in sorted(cohort.glob("*/")):
        target = copy / source.name
        target.mkdir()
        for child in source.glob("*.submission.md"):
            (target / child.name).write_text(child.read_text())
    _grade(copy, "alice", "q1", 5.0)

    capsys.readouterr()
    run_cli(["apply", "--submissions", str(cohort), "--incoming", str(copy), "--yes"])
    assert _points(cohort, "q1") == 5.0


def test_a_submission_only_they_have_is_reported_not_invented(
    cohort: Path, tmp_path: Path, capsys
) -> None:
    packet = _pack(cohort, tmp_path / "out")
    stray = packet / "submissions" / "og-stranger"
    stray.mkdir()
    (stray / "og-stranger.submission.md").write_text(
        SUBMISSION.format(name="", sid="og-stranger")
    )
    _grade(packet / "submissions", "alice", "q1", 5.0, index=2)

    capsys.readouterr()
    run_cli(["preview", "--submissions", str(cohort), "--incoming", str(packet)])
    captured = capsys.readouterr()
    assert "could not be matched" in captured.err
    assert not list(cohort.glob("*/og-stranger*"))


def test_preview_json_is_machine_readable(cohort: Path, tmp_path: Path, capsys) -> None:
    import json

    packet = _pack(cohort, tmp_path / "out")
    _grade(packet / "submissions", "alice", "q1", 5.0)
    capsys.readouterr()
    run_cli(
        ["preview", "--submissions", str(cohort), "--incoming", str(packet), "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["changes"][0]["question"] == "q1"
    assert payload["conflicts"] == []


def test_pack_without_output_uses_the_default_beside_the_cohort(
    cohort: Path, capsys
) -> None:
    run_cli(["pack", "--submissions", str(cohort), "--name", "p1"])
    expected = default_packet_output_dir(cohort) / "p1"
    assert expected.is_dir()
    assert str(expected) in capsys.readouterr().out
    assert cohort not in expected.parents


def test_pack_encrypt_writes_a_sealed_file_instead_of_a_folder(
    cohort: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("OPENGRADER_PASSPHRASE", "correct horse")
    run_cli(
        [
            "pack",
            "--submissions",
            str(cohort),
            "--output",
            str(tmp_path / "out"),
            "--name",
            "p1",
            "--encrypt",
        ]
    )
    out = capsys.readouterr().out
    sealed = tmp_path / "out" / "p1.zip.enc"
    assert sealed.is_file()
    assert not (tmp_path / "out" / "p1").exists()
    assert str(sealed) in out
    assert "some other way" in out


def test_a_sealed_packet_merges_back(
    cohort: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENGRADER_PASSPHRASE", "correct horse")
    run_cli(
        [
            "pack",
            "--submissions",
            str(cohort),
            "--output",
            str(tmp_path / "out"),
            "--name",
            "p1",
            "--encrypt",
        ]
    )
    sealed = tmp_path / "out" / "p1.zip.enc"

    opened = unseal_packet(sealed, tmp_path / "alice", "correct horse")
    _grade(opened / "submissions", "alice", "q1", 5.0)
    returned = seal_packet(opened, "correct horse", destination=tmp_path / "back.zip.enc")

    run_cli(
        ["apply", "--submissions", str(cohort), "--incoming", str(returned), "--yes"]
    )
    assert _points(cohort, "q1") == 5.0


def test_previewing_a_sealed_packet_leaves_nothing_decrypted(
    cohort: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("OPENGRADER_PASSPHRASE", "p")
    run_cli(
        [
            "pack",
            "--submissions",
            str(cohort),
            "--output",
            str(tmp_path / "out"),
            "--name",
            "p1",
            "--encrypt",
        ]
    )
    sealed = tmp_path / "out" / "p1.zip.enc"

    capsys.readouterr()
    run_cli(["preview", "--submissions", str(cohort), "--incoming", str(sealed)])
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["p1.zip.enc"]


def test_a_wrong_passphrase_refuses_the_merge(
    cohort: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENGRADER_PASSPHRASE", "right")
    run_cli(
        [
            "pack",
            "--submissions",
            str(cohort),
            "--output",
            str(tmp_path / "out"),
            "--name",
            "p1",
            "--encrypt",
        ]
    )
    monkeypatch.setenv("OPENGRADER_PASSPHRASE", "wrong")
    with pytest.raises(SystemExit) as exit_info:
        run_cli(
            [
                "apply",
                "--submissions",
                str(cohort),
                "--incoming",
                str(tmp_path / "out" / "p1.zip.enc"),
                "--yes",
            ]
        )
    assert exit_info.value.code == 2
    assert _points(cohort, "q1") is None
