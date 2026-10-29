"""Export graded submissions to a class gradebook CSV."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS
from opengrader.core.curve import read_curve
from opengrader.core.qa_file import (
    load_qa_markdown,
    outcomes_by_question,
    parse_qa_markdown,
    possible_points_by_question,
)
from opengrader.core.export.gradebook import (
    build_gradebook_rows,
    discover_submission_paths,
    ordered_question_ids,
    write_gradebook_csv,
    write_gradebook_rows,
)
from opengrader.core.submissions import load_student_submission_markdown

_submission_paths = discover_submission_paths


EPILOG = """\
\b
Examples:
  opengrader grades-export --submissions samples/cs146-quiz1/student-submissions/
  opengrader grades-export --submissions samples/cs146-quiz1/student-submissions/ --output grades.csv
"""


@click.command("grades-export", context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.option(
    "--submissions",
    metavar="PATH",
    help="Read extraction files instead of the current workspace assignment.",
)
@click.option("--workspace", type=click.Path(path_type=Path), help="Workspace directory.")
@click.option("--assignment", help="Assignment ID within the workspace.")
@click.option(
    "--output",
    metavar="PATH",
    help="Write the CSV here. Omit to print it to stdout.",
)
@click.option(
    "--solutions",
    help=(
        "QA answer key. Optional, and only needed to add per-learning-"
        "outcome mastery columns: the outcome tags live in the answer key, "
        "not in the submissions. Without it the gradebook is unchanged."
    ),
)
def command(
    submissions: str | None,
    workspace: Path | None,
    assignment: str | None,
    output: str | None,
    solutions: str | None,
) -> None:
    """Export graded submission files to a class gradebook CSV."""
    solutions_path = Path(solutions) if solutions else None
    if solutions_path is not None and not solutions_path.is_file():
        print(f"error: solutions file not found: {solutions_path}", file=sys.stderr)
        sys.exit(1)

    if submissions:
        submissions_path = Path(submissions)
        try:
            paths = discover_submission_paths(submissions_path)
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        if not paths:
            print(
                f"error: no *.submission.md files found in {submissions_path}",
                file=sys.stderr,
            )
            sys.exit(1)
        docs = [load_student_submission_markdown(path) for path in paths]
        curve = read_curve(submissions_path)
        qa_doc = load_qa_markdown(solutions_path) if solutions_path is not None else None
    else:
        from opengrader.core.workspace.context import (
            current_assignment,
            submissions_path,
        )

        try:
            opened, assignment_id = current_assignment(
                workspace_root=workspace, assignment_id=assignment
            )
            docs = [
                opened.document(assignment_id, item.id)
                for item in opened.submissions(assignment_id)
            ]
            if not docs:
                raise ValueError("This assignment has no submissions to export.")
            curve = read_curve(submissions_path(opened, assignment_id))
            qa_doc = (
                load_qa_markdown(solutions_path)
                if solutions_path is not None
                else parse_qa_markdown(opened.assignment_source(assignment_id))
            )
        except (OSError, ValueError) as exc:
            raise click.UsageError(str(exc)) from None

    outcomes: dict[str, tuple[str, ...]] | None = None
    outcome_points: dict[str, float] | None = None
    if qa_doc is not None:
        outcomes = outcomes_by_question(qa_doc)
        outcome_points = possible_points_by_question(qa_doc)

    rows = build_gradebook_rows(
        docs,
        curve,
        outcomes_by_question=outcomes,
        possible_points_by_question=outcome_points,
    )

    if output:
        output_path = Path(output)
        write_gradebook_csv(rows, output_path)
        print(
            f"Wrote {len(docs)} student(s) × {len(ordered_question_ids(docs))} question(s) "
            f"to {output_path}"
        )
    else:
        write_gradebook_rows(rows, sys.stdout)
