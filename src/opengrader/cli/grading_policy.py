"""Manage anonymity and traversal policies for a submissions folder."""

from __future__ import annotations

import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.anonymize import (
    read_name_map,
    redact_submission_names,
    restore_submission_names,
    write_name_map,
)
from opengrader.core.policy import GradingPolicy, read_policy, write_policy

EPILOG = """
examples:
  uv run grading-policy show --submissions out/student-submissions/
  uv run grading-policy lock --submissions out/student-submissions/
  uv run grading-policy unlock --submissions out/student-submissions/
  uv run grading-policy redact-names --submissions out/subs/ --name-map names.json
  uv run grading-policy restore-names --submissions out/subs/ --name-map names.json
"""


def _add_submissions(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--submissions",
        required=True,
        help="Submissions directory (or a single .submission.md file).",
    )


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Get or set a submissions folder's anonymity policy: the soft "
            "multi-grader lock (lock/unlock) and name redaction (redact-names/"
            "restore-names)."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_submissions(subparsers.add_parser("show", help="Print the current policy."))
    _add_submissions(
        subparsers.add_parser(
            "lock", help="Lock the folder to anonymous grading (multi-grader)."
        )
    )
    _add_submissions(
        subparsers.add_parser("unlock", help="Unlock (solo grading, toggle freely).")
    )

    redact = subparsers.add_parser(
        "redact-names",
        help="Strip student names from the files, writing a name map to keep.",
    )
    _add_submissions(redact)
    redact.add_argument(
        "--name-map",
        required=True,
        help="Where to write the id↔name map (keep this out of the graders' copy).",
    )

    restore = subparsers.add_parser(
        "restore-names",
        help="Write names back into the files from a name map.",
    )
    _add_submissions(restore)
    restore.add_argument(
        "--name-map",
        required=True,
        help="The name map written by redact-names.",
    )

    return parser


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


def _cmd_show(args: Namespace) -> None:
    path = _resolve_submissions(args.submissions)
    _print_policy(path, read_policy(path))


def _cmd_lock(args: Namespace) -> None:
    path = _resolve_submissions(args.submissions)
    write_policy(path, GradingPolicy(mode="multi", anonymity_locked=True))
    print(f"Locked {path} to anonymous grading (multi-grader).")


def _cmd_unlock(args: Namespace) -> None:
    path = _resolve_submissions(args.submissions)
    write_policy(path, GradingPolicy(mode="solo", anonymity_locked=False))
    print(f"Unlocked {path} — solo grading (teacher mode may be toggled).")


def _cmd_redact(args: Namespace) -> None:
    path = _resolve_submissions(args.submissions)
    name_map_path = Path(args.name_map).expanduser()
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


def _cmd_restore(args: Namespace) -> None:
    path = _resolve_submissions(args.submissions)
    name_map_path = Path(args.name_map).expanduser()
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


_COMMANDS = {
    "show": _cmd_show,
    "lock": _cmd_lock,
    "unlock": _cmd_unlock,
    "redact-names": _cmd_redact,
    "restore-names": _cmd_restore,
}


def run_cli(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    _COMMANDS[args.command](args)
