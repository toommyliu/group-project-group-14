"""File and track student regrade requests."""

from __future__ import annotations

import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.submissions.regrade import (
    RegradeRequest,
    add_regrade_request,
    deny_regrade_request,
    read_regrade_requests,
    resolve_regrade_request,
)

EPILOG = """
examples:
  uv run regrade add --submissions out/subs/ --student 012345 --question q3 \\
      --reason "Valid alternative method deserves partial credit."
  uv run regrade add --submissions out/subs/ --student 012345 \\
      --reason "Whole-exam total looks off."          # whole-assignment dispute
  uv run regrade list --submissions out/subs/                     # open only
  uv run regrade list --submissions out/subs/ --all              # every request
  uv run regrade resolve rg-1a2b3c4d --submissions out/subs/ --note "Regraded: +5"
  uv run regrade deny rg-9f8e7d6c --submissions out/subs/ --note "Answer is wrong."
"""

_STATUS_GLYPH = {"open": "●", "resolved": "✓", "denied": "✗"}


def _add_submissions(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--submissions",
        required=True,
        help="Submissions directory (or a single .submission.md file).",
    )


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "File and track student regrade requests: add a dispute, list "
            "open (or all) requests, resolve or deny one."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="File a new regrade request.")
    _add_submissions(add)
    add.add_argument(
        "--student", required=True, help="The disputing student's id."
    )
    add.add_argument(
        "--question",
        default="",
        help="The disputed question id (e.g. q3). Omit for a whole-assignment dispute.",
    )
    add.add_argument(
        "--reason",
        default="",
        help="The student's stated reason for the dispute.",
    )

    listing = subparsers.add_parser("list", help="List regrade requests.")
    _add_submissions(listing)
    listing.add_argument(
        "--all",
        action="store_true",
        help="Show every request (default: only still-open ones).",
    )

    resolve = subparsers.add_parser(
        "resolve", help="Mark a request resolved (grade reviewed/adjusted)."
    )
    resolve.add_argument("request_id", help="The request id (e.g. rg-1a2b3c4d).")
    _add_submissions(resolve)
    resolve.add_argument("--note", default="", help="Resolution note.")

    deny = subparsers.add_parser(
        "deny", help="Mark a request denied (the grade stands)."
    )
    deny.add_argument("request_id", help="The request id (e.g. rg-1a2b3c4d).")
    _add_submissions(deny)
    deny.add_argument("--note", default="", help="Reason the grade stands.")

    return parser


def _resolve_submissions(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.exists():
        print(f"Error: submissions path not found: {path}", file=sys.stderr)
        sys.exit(1)
    return path


def _format_request(request: RegradeRequest) -> str:
    glyph = _STATUS_GLYPH.get(request.status, "?")
    where = request.question_id or "whole assignment"
    line = (
        f"{glyph} {request.request_id}  [{request.status}]  "
        f"student {request.student_id}  ({where})"
    )
    if request.reason:
        line += f"\n    reason: {request.reason}"
    if request.status != "open" and request.resolution_note:
        line += f"\n    note:   {request.resolution_note}"
    return line


def _cmd_add(args: Namespace) -> None:
    path = _resolve_submissions(args.submissions)
    try:
        request = add_regrade_request(
            path,
            student_id=args.student,
            reason=args.reason,
            question_id=args.question,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    where = request.question_id or "the whole assignment"
    print(
        f"Filed regrade request {request.request_id} for student "
        f"{request.student_id} on {where}."
    )


def _cmd_list(args: Namespace) -> None:
    path = _resolve_submissions(args.submissions)
    requests = read_regrade_requests(path)
    if not args.all:
        requests = [r for r in requests if r.is_open]
    if not requests:
        scope = "requests" if args.all else "open requests"
        print(f"No {scope} for {path}.")
        return
    open_count = sum(1 for r in requests if r.is_open)
    label = "request" if len(requests) == 1 else "requests"
    print(f"{len(requests)} {label} ({open_count} open) for {path}:\n")
    for request in requests:
        print(_format_request(request))
        print()


def _cmd_resolve(args: Namespace) -> None:
    path = _resolve_submissions(args.submissions)
    updated = resolve_regrade_request(path, args.request_id, note=args.note)
    if updated is None:
        print(f"Error: no regrade request {args.request_id!r}", file=sys.stderr)
        sys.exit(1)
    print(f"Resolved {updated.request_id}.")


def _cmd_deny(args: Namespace) -> None:
    path = _resolve_submissions(args.submissions)
    updated = deny_regrade_request(path, args.request_id, note=args.note)
    if updated is None:
        print(f"Error: no regrade request {args.request_id!r}", file=sys.stderr)
        sys.exit(1)
    print(f"Denied {updated.request_id}.")


_COMMANDS = {
    "add": _cmd_add,
    "list": _cmd_list,
    "resolve": _cmd_resolve,
    "deny": _cmd_deny,
}


def run_cli(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    _COMMANDS[args.command](args)
