import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from opengrader.cli.calibrate import run_cli
from opengrader.core.handoff.packet import PACKET_SUBMISSIONS_DIRNAME
from opengrader.core.history.journal import GradeJournal
from opengrader.core.submissions.student_file import (
    StudentSubmissionGradeUpdate,
    load_student_submission_markdown,
    update_student_submission_file,
)

_START = datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc)


def _write_submission(
    path: Path, student_id: str, response_text: str = ""
) -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                f'student_name: "Student {student_id}"',
                f'student_id: "{student_id}"',
                'course_code: "CS146"',
                'assignment_title: "Quiz 1"',
                'assignment_version: "A"',
                f'source_pdf: "{student_id}.pdf"',
                'student_score: ""',
                "---",
                "",
                "- q1:",
                "  - Question type: Free Response",
                "  - Prompt: ",
                f"  - Response text: {response_text or f'answer number {student_id}'}",
                "  - Possible points: 10",
                "  - Points: ",
                "  - Rubric:",
                "  - Analysis: ",
                "",
            ]
        )
    )


@pytest.fixture()
def cohort(tmp_path: Path) -> Path:
    root = tmp_path / "subs"
    root.mkdir()
    for index in range(1, 9):
        _write_submission(root / f"s{index}.submission.md", f"00{index}")
    return root


def _grade_packet(packet_root: Path, points: float) -> None:
    for path in sorted((packet_root / PACKET_SUBMISSIONS_DIRNAME).glob("*/*.submission.md")):
        document = load_student_submission_markdown(path)
        for question in document.questions:
            update_student_submission_file(
                path,
                StudentSubmissionGradeUpdate(
                    question_id=question.question_id,
                    possible_points=question.possible_points,
                    points_awarded=points,
                    rubric_labels=("Partial",),
                ),
            )


def _pack(cohort: Path, tmp_path: Path, graders: str = "alice,bob") -> Path:
    out = tmp_path / "packets"
    run_cli(
        [
            "pack",
            "--submissions", str(cohort),
            "--graders", graders,
            "--sample", "4",
            "--name", "norming",
            "--output", str(out),
            "--seed", "42",
        ]
    )
    return out


def test_pack_cuts_one_packet_per_grader(cohort, tmp_path, capsys):
    out = _pack(cohort, tmp_path, "alice,bob,carol")

    printed = capsys.readouterr().out
    assert "4 student(s) to 3 grader(s), ungraded and anonymous" in printed
    assert sorted(p.name for p in out.iterdir()) == [
        "norming-alice",
        "norming-bob",
        "norming-carol",
    ]


def test_pack_refuses_a_single_grader(cohort, tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        run_cli(
            [
                "pack",
                "--submissions", str(cohort),
                "--graders", "alice",
                "--output", str(tmp_path / "packets"),
            ]
        )

    assert exit_info.value.code == 1
    assert "at least two graders" in capsys.readouterr().err


def test_list_shows_the_recorded_sessions(cohort, tmp_path, capsys):
    _pack(cohort, tmp_path)
    capsys.readouterr()

    run_cli(["list", "--submissions", str(cohort)])

    out = capsys.readouterr().out
    assert "norming" in out
    assert "alice, bob" in out


def test_list_is_quiet_when_there_are_no_sessions(cohort, capsys):
    run_cli(["list", "--submissions", str(cohort)])

    assert "No calibration sets recorded" in capsys.readouterr().out


def test_report_names_the_harsher_grader(cohort, tmp_path, capsys):
    out = _pack(cohort, tmp_path)
    _grade_packet(out / "norming-alice", 9.0)
    _grade_packet(out / "norming-bob", 5.0)
    capsys.readouterr()

    run_cli(
        [
            "report",
            "--submissions", str(cohort),
            "--set", "norming",
            "--min-samples", "4",
        ]
    )

    printed = capsys.readouterr().out
    assert "calibration set 'norming'" in printed
    assert "alice ↔ bob" in printed
    assert "alice marks 40.0 points per 100 more generously" in printed
    assert "+20.0" in printed and "-20.0" in printed
    assert "provisional" not in printed


def test_report_refuses_when_only_one_grader_returned_a_packet(
    cohort, tmp_path, capsys
):
    out = _pack(cohort, tmp_path)
    _grade_packet(out / "norming-alice", 9.0)
    capsys.readouterr()

    with pytest.raises(SystemExit) as exit_info:
        run_cli(["report", "--submissions", str(cohort), "--set", "norming"])

    captured = capsys.readouterr()
    assert exit_info.value.code == 1
    assert "agreement needs at least two" in captured.err
    assert "bob's packet is not usable" in captured.err


def test_report_on_an_unknown_set_is_an_error(cohort, capsys):
    with pytest.raises(SystemExit) as exit_info:
        run_cli(["report", "--submissions", str(cohort), "--set", "nope"])

    assert exit_info.value.code == 1
    assert "no calibration set named nope" in capsys.readouterr().err


def test_from_set_uses_the_most_recent_session(cohort, tmp_path, capsys):
    out = _pack(cohort, tmp_path)
    _grade_packet(out / "norming-alice", 9.0)
    _grade_packet(out / "norming-bob", 5.0)
    capsys.readouterr()

    run_cli(["report", "--submissions", str(cohort), "--from-set", "--min-samples", "4"])

    assert "calibration set 'norming'" in capsys.readouterr().out


def test_report_json_carries_the_pair_statistics(cohort, tmp_path, capsys):
    out = _pack(cohort, tmp_path)
    _grade_packet(out / "norming-alice", 9.0)
    _grade_packet(out / "norming-bob", 5.0)
    capsys.readouterr()

    run_cli(
        [
            "report",
            "--submissions", str(cohort),
            "--set", "norming",
            "--min-samples", "4",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["has_overlap"] is True
    pair = payload["same_work_pairs"][0]
    assert pair["basis"] == "same_work"
    assert pair["samples"] == 4
    assert pair["sufficient"] is True
    assert pair["more_generous"] == "alice"
    assert {grader["actor"] for grader in payload["graders"]} == {"alice", "bob"}


def _journal_grading(cohort: Path) -> None:
    history = cohort / ".opengrader" / "history"
    for index, path in enumerate(sorted(cohort.glob("*.submission.md"))):
        document = load_student_submission_markdown(path)
        actor = "alice" if index % 2 == 0 else "bob"
        points = (2.0 if index < 4 else 9.0) if actor == "alice" else 6.0
        update_student_submission_file(
            path,
            StudentSubmissionGradeUpdate(
                question_id="q1",
                possible_points=10.0,
                points_awarded=points,
                rubric_labels=("Partial",),
            ),
        )
        journal = GradeJournal(history, actor=actor, session_id=f"session-{actor}")
        journal.append(
            kind="grade",
            student_id=document.student_id,
            student_name=document.student_name,
            question_id="q1",
            summary="",
            new={"points": points, "rubric": ["Partial"]},
        )
        lines = journal.path.read_text().splitlines()
        record = json.loads(lines[-1])
        record["timestamp"] = (_START + timedelta(minutes=index * 3)).isoformat()
        lines[-1] = json.dumps(record)
        journal.path.write_text("\n".join(lines) + "\n")


def test_report_defaults_to_the_audit_journal(cohort, capsys):
    _journal_grading(cohort)

    run_cli(
        [
            "report",
            "--submissions", str(cohort),
            "--min-samples", "2",
            "--min-drift-samples", "3",
        ]
    )

    out = capsys.readouterr().out
    assert "the audit journal" in out
    assert "+70.0" in out


def test_a_divided_cohort_with_no_shared_answer_is_told_so(cohort, capsys):
    _journal_grading(cohort)

    run_cli(["report", "--submissions", str(cohort), "--min-drift-samples", "3"])

    out = capsys.readouterr().out
    assert "No two graders marked comparable work" in out
    assert "calibrate pack" in out


def test_a_divided_cohort_is_still_comparable_through_a_shared_answer(
    tmp_path, capsys
):
    root = tmp_path / "same-answers"
    root.mkdir()
    for index in range(1, 9):
        _write_submission(
            root / f"s{index}.submission.md",
            f"00{index}",
            response_text="a block that repeats until a condition fails",
        )
    _journal_grading(root)

    run_cli(
        [
            "report",
            "--submissions", str(root),
            "--min-samples", "2",
            "--min-drift-samples", "3",
        ]
    )

    out = capsys.readouterr().out
    assert "the same answers from different students" in out
    assert "No two graders marked comparable work" not in out


def test_report_without_any_history_is_an_error(cohort, capsys):
    with pytest.raises(SystemExit) as exit_info:
        run_cli(["report", "--submissions", str(cohort)])

    assert exit_info.value.code == 1
    assert "no grading history" in capsys.readouterr().err


def test_a_missing_submissions_path_is_an_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        run_cli(["report", "--submissions", str(tmp_path / "nope")])

    assert exit_info.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_reporting_never_changes_a_grade(cohort, tmp_path, capsys):
    out = _pack(cohort, tmp_path)
    _grade_packet(out / "norming-alice", 9.0)
    _grade_packet(out / "norming-bob", 5.0)
    before = {
        path: path.read_bytes() for path in sorted(cohort.glob("*.submission.md"))
    }
    capsys.readouterr()

    run_cli(
        ["report", "--submissions", str(cohort), "--set", "norming", "--min-samples", "4"]
    )

    after = {path: path.read_bytes() for path in sorted(cohort.glob("*.submission.md"))}
    assert after == before
