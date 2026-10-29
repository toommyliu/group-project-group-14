"""File and track student regrade requests."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS, Group
from opengrader.core.submissions.regrade import (
    RegradeRequest,
    add_regrade_request,
    deny_regrade_request,
    read_regrade_requests,
    resolve_regrade_request,
)

EPILOG = """\
\b
Examples:
  opengrader regrade add --submissions out/subs/ --student 012345 --question q3 \\
      --reason "Valid alternative method deserves partial credit."
  opengrader regrade add --submissions out/subs/ --student 012345 \\
      --reason "Whole-assignment total looks off."          # whole-assignment dispute
  opengrader regrade list --submissions out/subs/                     # open only
  opengrader regrade list --submissions out/subs/ --all              # every request
  opengrader regrade resolve rg-1a2b3c4d --submissions out/subs/ --note "Regraded: +5"
  opengrader regrade deny rg-9f8e7d6c --submissions out/subs/ --note "Answer is wrong."
"""

_STATUS_GLYPH = {"open": "●", "resolved": "✓", "denied": "✗"}


def _submissions_option(function):
    return click.option(
        "--submissions",
        required=True,
        help="Submissions directory (or a single .submission.md file).",
    )(function)


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


@click.group("regrade", cls=Group, context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
def command() -> None:
    """File and track student regrade requests: add a dispute, list open (or
    all) requests, resolve or deny one.
    """


@command.command("add")
@_submissions_option
@click.option("--student", required=True, help="The disputing student's id.")
@click.option(
    "--question",
    default="",
    help="The disputed question id (e.g. q3). Omit for a whole-assignment dispute.",
)
@click.option(
    "--reason",
    default="",
    help="The student's stated reason for the dispute.",
)
def add_command(*, submissions: str, student: str, question: str, reason: str) -> None:
    """File a new regrade request."""
    path = _resolve_submissions(submissions)
    try:
        request = add_regrade_request(
            path,
            student_id=student,
            reason=reason,
            question_id=question,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    where = request.question_id or "the whole assignment"
    print(
        f"Filed regrade request {request.request_id} for student "
        f"{request.student_id} on {where}."
    )


@command.command("list")
@_submissions_option
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="Show every request (default: only still-open ones).",
)
def list_command(*, submissions: str, show_all: bool) -> None:
    """List regrade requests."""
    path = _resolve_submissions(submissions)
    requests = read_regrade_requests(path)
    if not show_all:
        requests = [r for r in requests if r.is_open]
    if not requests:
        scope = "requests" if show_all else "open requests"
        print(f"No {scope} for {path}.")
        return
    open_count = sum(1 for r in requests if r.is_open)
    label = "request" if len(requests) == 1 else "requests"
    print(f"{len(requests)} {label} ({open_count} open) for {path}:\n")
    for request in requests:
        print(_format_request(request))
        print()


@command.command("resolve")
@click.argument("request_id")
@_submissions_option
@click.option("--note", default="", help="Resolution note.")
def resolve_command(*, request_id: str, submissions: str, note: str) -> None:
    """Mark a request resolved (grade reviewed/adjusted).

    REQUEST_ID is the request id (e.g. rg-1a2b3c4d).
    """
    path = _resolve_submissions(submissions)
    updated = resolve_regrade_request(path, request_id, note=note)
    if updated is None:
        print(f"Error: no regrade request {request_id!r}", file=sys.stderr)
        sys.exit(1)
    print(f"Resolved {updated.request_id}.")


@command.command("deny")
@click.argument("request_id")
@_submissions_option
@click.option("--note", default="", help="Reason the grade stands.")
def deny_command(*, request_id: str, submissions: str, note: str) -> None:
    """Mark a request denied (the grade stands).

    REQUEST_ID is the request id (e.g. rg-1a2b3c4d).
    """
    path = _resolve_submissions(submissions)
    updated = deny_regrade_request(path, request_id, note=note)
    if updated is None:
        print(f"Error: no regrade request {request_id!r}", file=sys.stderr)
        sys.exit(1)
    print(f"Denied {updated.request_id}.")
