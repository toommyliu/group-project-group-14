"""Show changes to grades, comments, and transcriptions."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.history import (
    GradeJournalEntry,
    read_journal,
    resolve_history_dir,
)

EPILOG = """
examples:
  uv run grade-history --input out/student-submissions/
  uv run grade-history --input out/student-submissions/ --actor prof_kim
  uv run grade-history --input out/student-submissions/ --student 123456789
  uv run grade-history --input out/student-submissions/ --json
"""

_KIND_GLYPH = {"grade": "✓", "comment": "✎", "rubric": "±"}


def _short_time(timestamp: str) -> str:
    if "T" in timestamp and len(timestamp) >= 16:
        return timestamp[:16].replace("T", " ")
    return timestamp


def _who(entry: GradeJournalEntry) -> str:
    return entry.student_name or entry.student_id or "—"


def _print_human(entries: list[GradeJournalEntry]) -> None:
    if not entries:
        print("No grade history recorded yet.")
        return
    actors = sorted({entry.actor for entry in entries if entry.actor})
    print(f"{len(entries)} change(s) by {len(actors)} grader(s): {', '.join(actors)}\n")
    for entry in entries:
        glyph = _KIND_GLYPH.get(entry.kind, "•")
        where = f" · {entry.question_id}" if entry.question_id else ""
        print(
            f"{_short_time(entry.timestamp)}  {glyph} {entry.actor:<12} "
            f"{_who(entry)}{where}  {entry.summary}"
        )


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Show the grade-change history (who changed what, when) recorded "
            "for a set of submissions."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        required=True,
        help="The submissions directory (or file) whose history to read.",
    )
    parser.add_argument("--actor", default=None, help="Only show changes by this grader.")
    parser.add_argument(
        "--student",
        default=None,
        help="Only show changes for this student id.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the history as a JSON array instead of a readable list.",
    )
    return parser


def run_cli(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input path not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    entries = read_journal(resolve_history_dir(input_path))
    if args.actor is not None:
        entries = [e for e in entries if e.actor == args.actor]
    if args.student is not None:
        entries = [e for e in entries if e.student_id == args.student]

    if args.as_json:
        print(
            json.dumps(
                [
                    {
                        "timestamp": e.timestamp,
                        "actor": e.actor,
                        "kind": e.kind,
                        "student_id": e.student_id,
                        "student_name": e.student_name,
                        "question_id": e.question_id,
                        "summary": e.summary,
                        "old": e.old,
                        "new": e.new,
                    }
                    for e in entries
                ],
                indent=2,
            )
        )
    else:
        _print_human(entries)
