"""Export graded submissions to a class gradebook CSV."""

from __future__ import annotations

import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

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


EPILOG = """
examples:
  uv run grades-export --submissions samples/cs146-quiz1/student-submissions/
  uv run grades-export --submissions samples/cs146-quiz1/student-submissions/ --output grades.csv
"""


def setup_args() -> ArgumentParser:
    parser = ArgumentParser(
        description="Export graded submission files to a class gradebook CSV.",
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--submissions",
        metavar="PATH",
        help="Read extraction files instead of the current workspace assignment.",
    )
    parser.add_argument("--workspace", type=Path, help="Workspace directory.")
    parser.add_argument("--assignment", help="Assignment ID within the workspace.")
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="Write the CSV here. Omit to print it to stdout.",
    )
    parser.add_argument(
        "--solutions",
        default=None,
        help=(
            "QA answer key. Optional, and only needed to add per-learning-"
            "outcome mastery columns: the outcome tags live in the answer key, "
            "not in the submissions. Without it the gradebook is unchanged."
        ),
    )
    return parser


def run_cli(argv: list[str] | None = None) -> None:
    parser = setup_args()
    args = parser.parse_args(argv)
    solutions = Path(args.solutions) if args.solutions else None
    if solutions is not None and not solutions.is_file():
        print(f"error: solutions file not found: {solutions}", file=sys.stderr)
        sys.exit(1)

    if args.submissions:
        submissions_path = Path(args.submissions)
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
        qa_doc = load_qa_markdown(solutions) if solutions is not None else None
    else:
        from opengrader.core.workspace.context import (
            current_assignment,
            submissions_path,
        )

        try:
            workspace, assignment_id = current_assignment(
                workspace_root=args.workspace, assignment_id=args.assignment
            )
            docs = [
                workspace.document(assignment_id, item.id)
                for item in workspace.submissions(assignment_id)
            ]
            if not docs:
                raise ValueError("This assignment has no submissions to export.")
            curve = read_curve(submissions_path(workspace, assignment_id))
            qa_doc = (
                load_qa_markdown(solutions)
                if solutions is not None
                else parse_qa_markdown(workspace.assignment_source(assignment_id))
            )
        except (OSError, ValueError) as exc:
            parser.error(str(exc))

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

    if args.output:
        output_path = Path(args.output)
        write_gradebook_csv(rows, output_path)
        print(
            f"Wrote {len(docs)} student(s) × {len(ordered_question_ids(docs))} question(s) "
            f"to {output_path}"
        )
    else:
        write_gradebook_rows(rows, sys.stdout)
