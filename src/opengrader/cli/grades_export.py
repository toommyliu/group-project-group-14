"""Export graded submissions to a class gradebook CSV.

The opposite granularity from the annotated feedback PDF: one row per student,
one column per question, plus totals — the spreadsheet an instructor drops into
their own gradebook when they don't use Canvas. Read-only; never re-runs OCR or
changes a grade.

The gradebook layout itself lives in :mod:`opengrader.core.export.gradebook` so
this CLI and the return-bundle exporter emit an identical table; this module is
the thin CLI wrapper (argument parsing + stdout/file output).
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.curve import read_curve
from opengrader.core.qa_file import (
    load_qa_markdown,
    outcomes_by_question,
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

# Re-exported for backwards compatibility with earlier importers/tests.
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
        required=True,
        metavar="PATH",
        help="Path to a .submission.md file or directory containing submission files.",
    )
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

    # Outcome columns appear only when an answer key is given *and* it carries
    # **Outcome** tags — so passing --solutions for an untagged assignment is a
    # no-op rather than a pile of empty columns.
    outcomes: dict[str, tuple[str, ...]] | None = None
    outcome_points: dict[str, float] | None = None
    if args.solutions:
        qa_path = Path(args.solutions)
        if not qa_path.exists():
            print(f"error: solutions file not found: {qa_path}", file=sys.stderr)
            sys.exit(1)
        qa_doc = load_qa_markdown(qa_path)
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
