"""Combine assignment grades into a course gradebook."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS
from opengrader.core.course import (
    COURSE_FILENAME,
    CourseConfigError,
    CourseReport,
    build_course_report,
    load_course_config,
)
from opengrader.core.export.gradebook import escape_csv_field

EPILOG = f"""\
\b
Examples:
  uv run course-grades --course course.toml
  uv run course-grades --course .                      # finds ./{COURSE_FILENAME}
  uv run course-grades --course course.toml --csv course-grades.csv
  uv run course-grades --course course.toml --at-risk
  uv run course-grades --course course.toml --outcomes
  uv run course-grades --course course.toml --student 012345
  uv run course-grades --course course.toml --json

\b
A course.toml looks like:
  name = "CS 146 - Spring 2026"

\b
  [[assignment]]
  name = "Quiz 1"
  path = "quizzes/quiz-1/student-submissions"
  weight = 10
  solutions = "quizzes/quiz-1/solutions.md"   # optional, unlocks outcomes

\b
  [grades]
  A = 90
  B = 80

Weights are relative — 10/10/30/50 and 1/1/3/5 mean the same thing. An
assignment a student has no graded work for is left out of their average by
default (mid-semester that means "not marked yet"); add missing = "zero" to an
assignment, or at the top level, when you want it counted as a zero.
"""


def _percent(value: float | None) -> str:
    return f"{value:.1f}%" if value is not None else "—"


def _signed(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.1f}"


def _print_report(report: CourseReport, *, show_all: bool) -> None:
    print(f"{report.course}")
    print(
        f"  {len(report.students)} student(s) across "
        f"{len(report.assignments)} assignment(s)"
    )
    weights = ", ".join(f"{a.name} ({a.weight:g})" for a in report.assignments)
    print(f"  Weights: {weights}\n")

    for problem in report.problems:
        print(f"  ! {problem}", file=sys.stderr)
    if report.problems:
        print(file=sys.stderr)

    counts = report.grade_counts()
    if counts:
        spread = "  ".join(
            f"{letter}: {counts[letter]}"
            for letter, _ in report.grade_scale
            if letter in counts
        )
        extra = "  ".join(
            f"{letter}: {count}"
            for letter, count in sorted(counts.items())
            if letter not in {name for name, _ in report.grade_scale}
        )
        print(f"  Grades — {spread}{('  ' + extra) if extra else ''}\n")

    if show_all:
        print("Students")
        for student in report.students:
            label = student.student_name or student.student_id or "?"
            trend = "  ".join(
                f"{name} {percent:.0f}%" for name, percent in student.trend()
            )
            note = ""
            if student.missing:
                note = f"   (no grade yet: {', '.join(student.missing)})"
            print(
                f"  {label:<24} {_percent(student.weighted_percent):>7}  "
                f"{student.letter}{note}"
            )
            if trend:
                direction = student.direction()
                suffix = (
                    f"   trend {_signed(direction)} pts" if direction is not None else ""
                )
                print(f"      {trend}{suffix}")


def _print_at_risk(report: CourseReport) -> None:
    print(f"\nAt risk (at or below {report.at_risk_percent:g}%)")
    at_risk = report.at_risk
    if not at_risk:
        print("  Nobody — every student with graded work is above the line.")
        return
    for student in at_risk:
        label = student.student_name or student.student_id or "?"
        direction = student.direction()
        arrow = ""
        if direction is not None:
            arrow = "  ↓ falling" if direction < -2 else ("  ↑ improving" if direction > 2 else "")
        missing = (
            f"   missing: {', '.join(student.missing)}" if student.missing else ""
        )
        print(
            f"  {label:<24} {_percent(student.weighted_percent):>7}  "
            f"{student.letter}{arrow}{missing}"
        )


def _print_outcomes(report: CourseReport) -> None:
    print("\nLearning outcomes (across the whole course)")
    if not report.outcomes:
        print(
            "  No outcomes to report. Add **Outcome** bullets to your answer "
            "keys and point each [[assignment]] at its solutions file."
        )
        return
    print(f"  Mastery bar: {report.mastery_threshold:.0%}\n")
    for outcome in report.outcomes:
        rate = outcome.mastery_rate
        print(f"  {outcome.outcome}")
        print(
            f"    assessed in: {', '.join(outcome.assignments)}"
            f"   ({outcome.possible_points:g} pts)"
        )
        print(
            f"    mastered:    {outcome.students_mastered}/{outcome.students_measured}"
            + (f"  ({rate:.0%})" if rate is not None else "")
            + (
                f"   mean {outcome.mean_mastery:.0%}"
                if outcome.mean_mastery is not None
                else ""
            )
        )


def _print_student(report: CourseReport, needle: str) -> int:
    wanted = needle.strip().casefold()
    matches = [
        student
        for student in report.students
        if student.student_id.casefold() == wanted
        or wanted in student.student_name.casefold()
    ]
    if not matches:
        print(f"error: no student matching {needle!r}", file=sys.stderr)
        return 1
    for student in matches:
        label = student.student_name or student.student_id or "?"
        print(f"\n{label}  ({student.student_id or 'no id'})")
        print(f"  Course grade: {_percent(student.weighted_percent)}  {student.letter}")
        for assignment in report.assignments:
            score = student.scores.get(assignment.name)
            if score is None:
                print(f"    {assignment.name:<22} —  (no graded work)")
                continue
            print(
                f"    {assignment.name:<22} {_percent(score.percent):>7}"
                f"   {score.earned:g}/{score.possible:g}"
                f"   weight {assignment.weight:g}"
            )
        direction = student.direction()
        if direction is not None:
            print(f"  Trend: {_signed(direction)} points, first half → second half")
    return 0


def build_csv_rows(report: CourseReport) -> list[list[str]]:
    header = ["student_id", "student_name"]
    header += [f"{a.name} %" for a in report.assignments]
    header += ["course %", "letter", "missing"]
    rows = [header]
    for student in report.students:
        cells = [student.student_id, student.student_name]
        for assignment in report.assignments:
            score = student.scores.get(assignment.name)
            percent = score.percent if score is not None else None
            cells.append(f"{percent:.1f}" if percent is not None else "")
        cells.append(
            f"{student.weighted_percent:.1f}"
            if student.weighted_percent is not None
            else ""
        )
        cells.append(student.letter)
        cells.append("; ".join(student.missing))
        rows.append(cells)
    return rows


def report_to_dict(report: CourseReport) -> dict[str, object]:
    return {
        "course": report.course,
        "at_risk_percent": report.at_risk_percent,
        "mastery_threshold": report.mastery_threshold,
        "grade_scale": [
            {"letter": letter, "minimum": minimum}
            for letter, minimum in report.grade_scale
        ],
        "problems": list(report.problems),
        "assignments": [
            {"name": a.name, "weight": a.weight, "path": str(a.path), "missing": a.missing}
            for a in report.assignments
        ],
        "students": [
            {
                "student_id": s.student_id,
                "student_name": s.student_name,
                "course_percent": s.weighted_percent,
                "letter": s.letter,
                "missing": list(s.missing),
                "trend": [
                    {"assignment": name, "percent": percent} for name, percent in s.trend()
                ],
                "direction": s.direction(),
                "assignments": {
                    name: {
                        "earned": score.earned,
                        "possible": score.possible,
                        "percent": score.percent,
                    }
                    for name, score in s.scores.items()
                },
            }
            for s in report.students
        ],
        "at_risk": [s.student_id or s.student_name for s in report.at_risk],
        "outcomes": [
            {
                "outcome": o.outcome,
                "assignments": list(o.assignments),
                "possible_points": o.possible_points,
                "students_measured": o.students_measured,
                "students_mastered": o.students_mastered,
                "mastery_rate": o.mastery_rate,
                "mean_mastery": o.mean_mastery,
            }
            for o in report.outcomes
        ],
    }


@click.command("course-grades", context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.option(
    "--course",
    required=True,
    help=f"Path to a {COURSE_FILENAME} (or a directory containing one).",
)
@click.option(
    "--csv",
    "csv_path",
    metavar="PATH",
    help="Also write the course gradebook as a CSV here.",
)
@click.option(
    "--at-risk",
    is_flag=True,
    help="Show the at-risk list (also shown by default when it is non-empty).",
)
@click.option(
    "--outcomes",
    is_flag=True,
    help="Show learning-outcome mastery across the course.",
)
@click.option(
    "--student",
    metavar="ID_OR_NAME",
    help="Show one student's full picture instead of the class summary.",
)
@click.option(
    "--summary",
    is_flag=True,
    help="Print only the headline figures, not the per-student list.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the whole report as JSON instead of a readable summary.",
)
def command(
    *,
    course: str,
    csv_path: str | None,
    at_risk: bool,
    outcomes: bool,
    student: str | None,
    summary: bool,
    as_json: bool,
) -> None:
    """Roll a semester of assignment folders into one course gradebook:
    weighted grades, letter grades, an at-risk list, per-student trend, and
    learning-outcome mastery across the whole course. Read-only — it never
    changes an assignment.
    """
    try:
        config = load_course_config(course)
    except CourseConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    report = build_course_report(config)

    if csv_path:
        output = Path(csv_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(
                [escape_csv_field(cell) for cell in row]
                for row in build_csv_rows(report)
            )

    if as_json:
        print(json.dumps(report_to_dict(report), indent=2))
        return

    if student:
        sys.exit(_print_student(report, student))

    _print_report(report, show_all=not summary)

    if at_risk or report.at_risk:
        _print_at_risk(report)
    if outcomes or report.outcomes:
        _print_outcomes(report)

    if csv_path:
        print(f"\nWrote {Path(csv_path)}")


if __name__ == "__main__":
    command()
