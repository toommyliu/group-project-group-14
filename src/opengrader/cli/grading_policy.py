"""Manage anonymity and traversal policies for a submissions folder."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS, Group
from opengrader.core.anonymize import (
    read_name_map,
    redact_submission_names,
    restore_submission_names,
    write_name_map,
)
from opengrader.core.policy import GradingPolicy, read_policy, write_policy

EPILOG = """\
\b
Examples:
  opengrader grading-policy show --submissions out/student-submissions/
  opengrader grading-policy lock --submissions out/student-submissions/
  opengrader grading-policy unlock --submissions out/student-submissions/
  opengrader grading-policy redact-names --submissions out/subs/ --name-map names.json
  opengrader grading-policy restore-names --submissions out/subs/ --name-map names.json
"""

_submissions_option = click.option(
    "--submissions",
    required=True,
    help="Submissions directory (or a single .submission.md file).",
)


def _resolve_submissions(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.exists():
        print(f"Error: submissions path not found: {path}", file=sys.stderr)
        sys.exit(1)
    return path


def _print_policy(path: Path, policy: GradingPolicy) -> None:
    state = "LOCKED — anonymous grading" if policy.anonymity_locked else "unlocked"
    print(f"{path}")
    print(f"  mode:             {policy.mode}")
    print(f"  anonymity_locked: {policy.anonymity_locked}  ({state})")


@click.group(
    "grading-policy",
    cls=Group,
    context_settings=CONTEXT_SETTINGS,
    epilog=EPILOG,
)
def command() -> None:
    """Get or set a submissions folder's anonymity policy: the soft
    multi-grader lock (lock/unlock) and name redaction
    (redact-names/restore-names).
    """


@command.command("show")
@_submissions_option
def show_command(submissions: str) -> None:
    """Print the current policy."""
    path = _resolve_submissions(submissions)
    _print_policy(path, read_policy(path))


@command.command("lock")
@_submissions_option
def lock_command(submissions: str) -> None:
    """Lock the folder to anonymous grading (multi-grader)."""
    path = _resolve_submissions(submissions)
    write_policy(path, GradingPolicy(mode="multi", anonymity_locked=True))
    print(f"Locked {path} to anonymous grading (multi-grader).")


@command.command("unlock")
@_submissions_option
def unlock_command(submissions: str) -> None:
    """Unlock (solo grading, toggle freely)."""
    path = _resolve_submissions(submissions)
    write_policy(path, GradingPolicy(mode="solo", anonymity_locked=False))
    print(f"Unlocked {path} — solo grading (teacher mode may be toggled).")


@command.command("redact-names")
@_submissions_option
@click.option(
    "--name-map",
    "name_map_file",
    required=True,
    help="Where to write the id↔name map (keep this out of the graders' copy).",
)
def redact_command(submissions: str, name_map_file: str) -> None:
    """Strip student names from the files, writing a name map to keep."""
    path = _resolve_submissions(submissions)
    name_map_path = Path(name_map_file).expanduser()
    if name_map_path.exists():
        print(
            f"Error: {name_map_path} already exists; refusing to overwrite a "
            "name map (it may be the only copy of the names).",
            file=sys.stderr,
        )
        sys.exit(1)
    name_map = redact_submission_names(path)
    if not name_map:
        print(f"Error: no .submission.md files found under {path}", file=sys.stderr)
        sys.exit(1)
    write_name_map(name_map_path, name_map)
    stripped = sum(1 for entry in name_map.values() if entry.get("student_name"))
    print(
        f"Redacted names from {len(name_map)} submission(s) "
        f"({stripped} had a name); name map written to {name_map_path}."
    )


@command.command("restore-names")
@_submissions_option
@click.option(
    "--name-map",
    "name_map_file",
    required=True,
    help="The name map written by redact-names.",
)
def restore_command(submissions: str, name_map_file: str) -> None:
    """Write names back into the files from a name map."""
    path = _resolve_submissions(submissions)
    name_map_path = Path(name_map_file).expanduser()
    if not name_map_path.exists():
        print(f"Error: name map not found: {name_map_path}", file=sys.stderr)
        sys.exit(1)
    try:
        name_map = read_name_map(name_map_path)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    restored = restore_submission_names(path, name_map)
    print(f"Restored names into {restored} submission(s) under {path}.")


if __name__ == "__main__":
    command()
