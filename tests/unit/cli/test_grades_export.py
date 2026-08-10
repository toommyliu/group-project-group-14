import csv
from pathlib import Path

import pytest

from opengrader.cli import grades_export
from opengrader.cli.grades_export import run_cli
from opengrader.core.submissions import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    render_student_submission_markdown,
)


def _question(
    question_id: str,
    possible: float | None,
    awarded: float | None,
    rubric: tuple[str, ...] = (),
) -> StudentSubmissionQuestion:
    return StudentSubmissionQuestion(
        question_id=question_id,
        prompt="",
        possible_points=possible,
        response_image_path=None,
        response_text="",
        points_awarded=awarded,
        rubric_labels=rubric,
    )


def _write_submission(
    path: Path,
    *,
    name: str,
    student_id: str,
    questions: list[StudentSubmissionQuestion],
) -> None:
    document = StudentSubmissionDocument(
        student_name=name,
        student_id=student_id,
        course_code="CS146",
        assignment_title="Quiz 1",
        assignment_version="A",
        source_pdf=f"{path.stem}.pdf",
        questions=questions,
    )
    path.write_text(render_student_submission_markdown(document), encoding="utf-8")


def _write_submissions(directory: Path) -> None:
    _write_submission(
        directory / "tommy.submission.md",
        name="Tommy Liu",
        student_id="s1",
        questions=[
            _question("q1", 10, 10, ("Correct (+0)",)),
            _question("q2", 10, 4, ("Partial (+4)",)),
        ],
    )
    _write_submission(
        directory / "andy.submission.md",
        name="Andy Cortes",
        student_id="s2",
        questions=[
            _question("q1", 10, 0, ("Incorrect (+0)",)),
            _question("q2", 10, None),  # ungraded
        ],
    )


def test_grades_export_writes_gradebook_csv(tmp_path: Path) -> None:
    submissions = tmp_path / "subs"
    submissions.mkdir()
    _write_submissions(submissions)
    output = tmp_path / "gradebook.csv"

    run_cli(["--submissions", str(submissions), "--output", str(output)])

    rows = list(csv.reader(output.open(encoding="utf-8")))
    assert rows[0] == ["student_id", "student_name", "Q1", "Q2", "total", "possible"]
    # Rows follow filename order: andy.submission.md before tommy.submission.md.
    assert rows[1] == ["s2", "Andy Cortes", "0", "", "0", "20"]
    assert rows[2] == ["s1", "Tommy Liu", "10", "4", "14", "20"]


def test_grades_export_ungraded_cell_is_blank(tmp_path: Path) -> None:
    submissions = tmp_path / "subs"
    submissions.mkdir()
    _write_submissions(submissions)
    output = tmp_path / "gradebook.csv"

    run_cli(["--submissions", str(submissions), "--output", str(output)])

    rows = list(csv.reader(output.open(encoding="utf-8")))
    andy = rows[1]
    # Andy's q2 is ungraded → blank cell, and it doesn't inflate the total.
    assert andy[3] == ""
    assert andy[4] == "0"


def test_grades_export_adds_curved_column_when_curve_active(tmp_path: Path) -> None:
    from opengrader.core.curve import Curve, write_curve

    submissions = tmp_path / "subs"
    submissions.mkdir()
    _write_submissions(submissions)
    write_curve(submissions, Curve("bonus", 5.0))
    output = tmp_path / "gradebook.csv"

    run_cli(["--submissions", str(submissions), "--output", str(output)])

    rows = list(csv.reader(output.open(encoding="utf-8")))
    assert rows[0] == [
        "student_id", "student_name", "Q1", "Q2", "total", "curved", "possible"
    ]
    # Tommy: raw 14 → curved 19 (bonus +5), capped at 20 possible.
    tommy = rows[2]
    assert tommy[4] == "14"   # raw total
    assert tommy[5] == "19"   # curved


def test_grades_export_neutralizes_csv_formula_injection(tmp_path: Path) -> None:
    """An OCR'd name that looks like a spreadsheet formula is escaped on export."""
    submissions = tmp_path / "subs"
    submissions.mkdir()
    _write_submission(
        submissions / "evil.submission.md",
        name='=HYPERLINK("http://evil","click")',
        student_id="@SUM(A1:A9)",
        questions=[_question("q1", 10, 10, ("Correct (+0)",))],
    )
    output = tmp_path / "gradebook.csv"

    run_cli(["--submissions", str(submissions), "--output", str(output)])

    rows = list(csv.reader(output.open(encoding="utf-8")))
    # The dangerous cells are prefixed with a single quote so a spreadsheet
    # renders them as literal text instead of evaluating a formula.
    assert rows[1][0] == "'@SUM(A1:A9)"
    assert rows[1][1] == '\'=HYPERLINK("http://evil","click")'
    # Numeric columns are untouched.
    assert rows[1][2] == "10"


def test_grades_export_stdout_also_escapes_formulas(tmp_path: Path, capsys) -> None:
    submissions = tmp_path / "subs"
    submissions.mkdir()
    _write_submission(
        submissions / "evil.submission.md",
        name="=1+1",
        student_id="s1",
        questions=[_question("q1", 10, 10)],
    )

    run_cli(["--submissions", str(submissions)])

    out = capsys.readouterr().out
    rows = list(csv.reader(out.splitlines()))
    assert rows[1][1] == "'=1+1"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("=cmd()", "'=cmd()"),
        ("+SUM()", "'+SUM()"),
        ("@import", "'@import"),
        ("-cmd|'/c calc'", "'-cmd|'/c calc'"),
        ("\ttab", "'\ttab"),
        ("\rcr", "'\rcr"),
        # Genuine signed numbers stay numeric (not text).
        ("-5", "-5"),
        ("+3.0", "+3.0"),
        # Ordinary values pass through untouched.
        ("Tommy Liu", "Tommy Liu"),
        ("s1", "s1"),
        ("", ""),
    ],
)
def test_escape_csv_field(value: str, expected: str) -> None:
    from opengrader.core.export.gradebook import escape_csv_field

    assert escape_csv_field(value) == expected


def test_grades_export_errors_on_empty_directory(tmp_path: Path, capsys) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(SystemExit) as exc:
        run_cli(["--submissions", str(empty)])
    assert exc.value.code == 1
    assert "no *.submission.md" in capsys.readouterr().err


# ── learning-outcome columns (--solutions) ──────────────────────────────────


_OUTCOME_QA = """---
title: Quiz
---

- (10 points) Explain.
  - **Correct**
    - x
  - **Outcome** CLO1
- (10 points) Apply.
  - **Correct**
    - y
  - **Outcome** CLO1
  - **Outcome** CLO2
"""


def _outcome_submission(student_id: str, marks: dict[str, float | None]) -> str:
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
        f'student_name: "Student {student_id}"\n'
        f'student_id: "{student_id}"\n'
        "course_code: CS146\n"
        'assignment_title: "Quiz"\n'
        "assignment_version: null\n"
        f'source_pdf: "{student_id}.pdf"\n'
        'score: ""\n'
        "---\n\n\n" + blocks
    )


def _outcome_cohort(tmp_path, marks_by_student):  # noqa: ANN001, ANN202
    from pathlib import Path

    subs = Path(tmp_path) / "subs"
    subs.mkdir()
    for student_id, marks in marks_by_student.items():
        (subs / f"{student_id}.submission.md").write_text(
            _outcome_submission(student_id, marks)
        )
    qa = Path(tmp_path) / "solutions.md"
    qa.write_text(_OUTCOME_QA)
    return qa, subs


def test_outcome_columns_appear_only_with_solutions(tmp_path, capsys) -> None:  # noqa: ANN001
    qa, subs = _outcome_cohort(tmp_path, {"101": {"q1": 9, "q2": 5}})

    grades_export.run_cli(["--submissions", str(subs)])
    assert "outcome:" not in capsys.readouterr().out

    grades_export.run_cli(["--submissions", str(subs), "--solutions", str(qa)])
    header = capsys.readouterr().out.splitlines()[0]
    assert "outcome:CLO1 %" in header and "outcome:CLO2 %" in header


def test_outcome_columns_come_after_the_totals(tmp_path, capsys) -> None:  # noqa: ANN001
    """Appended rather than interleaved, so an existing LMS import keeps working."""
    qa, subs = _outcome_cohort(tmp_path, {"101": {"q1": 9, "q2": 5}})
    grades_export.run_cli(["--submissions", str(subs), "--solutions", str(qa)])
    header = capsys.readouterr().out.splitlines()[0].split(",")
    assert header.index("possible") < header.index("outcome:CLO1 %")


def test_outcome_cells_are_percentages_of_the_tagged_points(tmp_path, capsys) -> None:  # noqa: ANN001
    qa, subs = _outcome_cohort(tmp_path, {"101": {"q1": 9, "q2": 5}})
    grades_export.run_cli(["--submissions", str(subs), "--solutions", str(qa)])
    rows = capsys.readouterr().out.splitlines()
    header, row = rows[0].split(","), rows[1].split(",")
    # CLO1 spans q1+q2 = 14/20; CLO2 is q2 alone = 5/10.
    assert row[header.index("outcome:CLO1 %")] == "70.0"
    assert row[header.index("outcome:CLO2 %")] == "50.0"


def test_an_ungraded_outcome_cell_is_blank_not_zero(tmp_path, capsys) -> None:  # noqa: ANN001
    """Same rule as an ungraded question — a half-marked assignment must not
    report a student as having scored nothing."""
    qa, subs = _outcome_cohort(tmp_path, {"101": {"q1": 9, "q2": None}})
    grades_export.run_cli(["--submissions", str(subs), "--solutions", str(qa)])
    rows = capsys.readouterr().out.splitlines()
    header, row = rows[0].split(","), rows[1].split(",")
    assert row[header.index("outcome:CLO1 %")] == "90.0"  # q1 only
    assert row[header.index("outcome:CLO2 %")] == ""  # nothing graded


def test_an_untagged_answer_key_adds_no_columns(tmp_path, capsys) -> None:  # noqa: ANN001
    """Passing --solutions for an untagged assignment is a no-op, not a pile of
    empty columns."""
    from pathlib import Path

    qa, subs = _outcome_cohort(tmp_path, {"101": {"q1": 9, "q2": 5}})
    Path(qa).write_text(
        "---\ntitle: Quiz\n---\n\n"
        "- (10 points) Explain.\n  - **Correct**\n    - x\n"
        "- (10 points) Apply.\n  - **Correct**\n    - y\n"
    )
    grades_export.run_cli(["--submissions", str(subs), "--solutions", str(qa)])
    assert "outcome:" not in capsys.readouterr().out


def test_a_missing_solutions_file_fails_fast(tmp_path) -> None:  # noqa: ANN001
    import pytest

    _, subs = _outcome_cohort(tmp_path, {"101": {"q1": 9}})
    with pytest.raises(SystemExit) as exit_info:
        grades_export.run_cli(
            ["--submissions", str(subs), "--solutions", str(tmp_path / "nope.md")]
        )
    assert exit_info.value.code == 1
