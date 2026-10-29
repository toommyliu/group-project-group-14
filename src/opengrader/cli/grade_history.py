"""Show changes to grades, comments, and transcriptions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS
from opengrader.core.history import (
    GradeJournalEntry,
    read_journal,
    resolve_history_dir,
)

EPILOG = """\
\b
Examples:
  opengrader grade-history --input out/student-submissions/
  opengrader grade-history --input out/student-submissions/ --actor prof_kim
  opengrader grade-history --input out/student-submissions/ --student 123456789
  opengrader grade-history --input out/student-submissions/ --json
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


@click.command("grade-history", context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(path_type=Path),
    help="The submissions directory (or file) whose history to read.",
)
@click.option("--actor", help="Only show changes by this grader.")
@click.option("--student", help="Only show changes for this student id.")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the history as a JSON array instead of a readable list.",
)
def command(
    input_path: Path, actor: str | None, student: str | None, as_json: bool
) -> None:
    """Show the grade-change history (who changed what, when) recorded for a
    set of submissions.
    """
    if not input_path.exists():
        print(f"Error: input path not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    entries = read_journal(resolve_history_dir(input_path))
    if actor is not None:
        entries = [e for e in entries if e.actor == actor]
    if student is not None:
        entries = [e for e in entries if e.student_id == student]

    if as_json:
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


if __name__ == "__main__":
    command()
