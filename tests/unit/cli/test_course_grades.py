"""Tests for the `course-grades` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opengrader.cli import course_grades

QA = """---
title: A
---

- (10 points) Explain.
  - **Correct**
    - x
  - **Outcome** CLO1
- (10 points) Apply.
  - **Correct**
    - y
  - **Outcome** CLO2
"""


def _submission(student_id: str, name: str, marks: dict[str, float | None]) -> str:
    blocks = ""
    for question_id, awarded in marks.items():
        blocks += (
            f"- {question_id}:\n"
            "  - Question type: Free Response\n"
            "  - Prompt: p\n"
            "  - Response text: answer\n"
            "  - Possible points: 10\n"
            f"  - Points: {'' if awarded is None else f'{awarded:g}'}\n"
            "  - Rubric:\n  - Analysis:\n\n"
        )
    return (
        "---\n"
        f'student_name: "{name}"\n'
        f'student_id: "{student_id}"\n'
        "course_code: CS146\n"
        'assignment_title: "A"\n'
        "assignment_version: null\n"
        f'source_pdf: "{student_id}.pdf"\n'
        'score: ""\n'
        "---\n\n\n" + blocks
    )


@pytest.fixture
def course(tmp_path: Path) -> Path:
    for folder, marks in (
        ("quiz", {"101": ("Ana", {"q1": 10, "q2": 8}), "102": ("Ben", {"q1": 5, "q2": 4})}),
        ("final", {"101": ("Ana", {"q1": 9, "q2": 9}), "102": ("Ben", {"q1": 5, "q2": 5})}),
    ):
        directory = tmp_path / folder / "student-submissions"
        directory.mkdir(parents=True)
        (tmp_path / folder / "solutions.md").write_text(QA)
        for student_id, (name, values) in marks.items():
            (directory / f"{student_id}.submission.md").write_text(
                _submission(student_id, name, values)
            )
    path = tmp_path / "course.toml"
    path.write_text(
        'name = "CS 146"\n'
        "at_risk_percent = 70\n"
        '[[assignment]]\nname = "Quiz"\npath = "quiz/student-submissions"\n'
        'weight = 25\nsolutions = "quiz/solutions.md"\n'
        '[[assignment]]\nname = "Final"\npath = "final/student-submissions"\n'
        'weight = 75\nsolutions = "final/solutions.md"\n'
    )
    return path


def test_it_prints_weighted_grades_and_letters(course: Path, capsys) -> None:  # noqa: ANN001
    course_grades.run_cli(["--course", str(course)])
    out = capsys.readouterr().out
    assert "CS 146" in out
    assert "Ana" in out and "Ben" in out
    # Ana: 0.25*90 + 0.75*90 = 90
    assert "90.0%" in out


def test_the_at_risk_list_appears_without_being_asked(course: Path, capsys) -> None:  # noqa: ANN001
    """A list of struggling students behind an extra flag goes unread."""
    course_grades.run_cli(["--course", str(course)])
    out = capsys.readouterr().out
    assert "At risk" in out
    assert "Ben" in out.split("At risk")[1]


def test_outcomes_are_rolled_up_across_assignments(course: Path, capsys) -> None:  # noqa: ANN001
    course_grades.run_cli(["--course", str(course), "--outcomes"])
    out = capsys.readouterr().out
    assert "Learning outcomes" in out
    assert "CLO1" in out and "CLO2" in out
    assert "Quiz, Final" in out


def test_a_directory_argument_finds_the_course_file(course: Path, capsys) -> None:  # noqa: ANN001
    course_grades.run_cli(["--course", str(course.parent)])
    assert "CS 146" in capsys.readouterr().out


def test_summary_hides_the_per_student_list(course: Path, capsys) -> None:  # noqa: ANN001
    course_grades.run_cli(["--course", str(course), "--summary"])
    out = capsys.readouterr().out
    assert "Grades —" in out
    assert "Students\n" not in out


def test_csv_has_one_column_per_assignment(course: Path, tmp_path: Path) -> None:
    output = tmp_path / "out" / "grades.csv"
    course_grades.run_cli(["--course", str(course), "--csv", str(output)])
    rows = output.read_text().splitlines()
    assert rows[0] == "student_id,student_name,Quiz %,Final %,course %,letter,missing"
    assert len(rows) == 3  # header + two students


def test_json_carries_students_outcomes_and_at_risk(course: Path, capsys) -> None:  # noqa: ANN001
    course_grades.run_cli(["--course", str(course), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["course"] == "CS 146"
    assert len(payload["students"]) == 2
    assert [o["outcome"] for o in payload["outcomes"]] == ["CLO1", "CLO2"]
    assert payload["at_risk"] == ["102"]
    ana = next(s for s in payload["students"] if s["student_id"] == "101")
    assert len(ana["trend"]) == 2


def test_a_single_student_view_shows_every_assignment(course: Path, capsys) -> None:  # noqa: ANN001
    with pytest.raises(SystemExit) as exit_info:
        course_grades.run_cli(["--course", str(course), "--student", "101"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "Ana" in out and "Quiz" in out and "Final" in out


def test_an_unknown_student_exits_nonzero(course: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        course_grades.run_cli(["--course", str(course), "--student", "zzz"])
    assert exit_info.value.code == 1


def test_a_bad_course_file_fails_with_a_readable_error(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    path = tmp_path / "course.toml"
    path.write_text('name = "X"\n')
    with pytest.raises(SystemExit) as exit_info:
        course_grades.run_cli(["--course", str(path)])
    assert exit_info.value.code == 1
    assert "at least one" in capsys.readouterr().err


def test_a_broken_assignment_is_warned_about_not_fatal(course: Path, capsys) -> None:  # noqa: ANN001
    course.write_text(
        course.read_text()
        + '[[assignment]]\nname = "Missing"\npath = "nowhere"\nweight = 10\n'
    )
    course_grades.run_cli(["--course", str(course)])
    captured = capsys.readouterr()
    assert "Missing" in captured.err
    assert "CS 146" in captured.out  # the report still printed
