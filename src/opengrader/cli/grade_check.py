"""Check whether an assignment is ready for release."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.release import (
    ReadinessIssue,
    ReadinessReport,
    ReleaseContext,
    load_release_context,
)

EPILOG = """
examples:
  uv run grade-check --submissions out/student-submissions/
  uv run grade-check --submissions out/student-submissions/ --roster roster.txt
  uv run grade-check --submissions out/student-submissions/ --json
  uv run grade-check --submissions out/student-submissions/ --fail-on-blocking

what the severities mean:
  BLOCKING  releasing now would hand back something known to be wrong
  WARNING   defensible, but a person should decide it rather than miss it
  NOTE      context that changes how the numbers should be read
"""


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Check whether a graded assignment is ready to hand back. Read-only."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--submissions",
        required=True,
        help="Submissions directory (or a single .submission.md file).",
    )
    parser.add_argument(
        "--roster",
        default="",
        help=(
            "Roster TSV (ID<TAB>NAME), the same file submission-extractor takes "
            "as --id-file. Adds a check for roster students with no submission."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    parser.add_argument(
        "--fail-on-blocking",
        action="store_true",
        help="Exit 2 when anything blocks the release — for scripting.",
    )
    return parser


def _issue_payload(issue: ReadinessIssue) -> dict:
    return {
        "kind": issue.kind.value,
        "severity": issue.severity,
        "summary": issue.summary,
        "detail": issue.detail,
        "count": issue.count,
        "examples": list(issue.examples),
    }


def _report_payload(context: ReleaseContext) -> dict:
    report = context.readiness
    return {
        "submissions": str(context.submissions_path),
        "ready": report.ready,
        "student_count": report.student_count,
        "graded_answers": report.graded_answers,
        "total_answers": report.total_answers,
        "graded_fraction": report.graded_fraction,
        "unreadable_files": list(context.unreadable_files),
        "previous_release": (
            context.previous_release.release_id if context.previous_release else None
        ),
        "issues": [_issue_payload(issue) for issue in report.issues],
    }


def _print_group(title: str, issues: tuple[ReadinessIssue, ...]) -> None:
    if not issues:
        return
    print(f"{title}")
    for issue in issues:
        print(f"  - {issue.describe()}")
    print()


def print_readiness(context: ReleaseContext) -> None:
    report: ReadinessReport = context.readiness

    if context.unreadable_files:
        print(
            f"WARNING: {len(context.unreadable_files)} submission file(s) could "
            "not be read and were left out of every count below:"
        )
        for name in context.unreadable_files:
            print(f"  {name}")
        print()

    fraction = report.graded_fraction
    graded = (
        f"{report.graded_answers}/{report.total_answers} answers graded"
        + (f" ({fraction * 100:.0f}%)" if fraction is not None else "")
    )
    print(f"{report.student_count} student(s), {graded}")
    if context.previous_release is not None:
        print(
            f"Last released {context.previous_release.created_at} "
            f"({context.previous_release.release_id})"
        )
    print()

    _print_group("Blocking — fix before releasing:", report.blocking)
    _print_group("Worth a decision:", report.warnings)
    _print_group("For context:", report.notes)

    if report.ready:
        print("Ready to release.")
        if report.warnings:
            print("(Nothing blocking; the warnings above are yours to accept.)")
    else:
        print(
            f"Not ready: {len(report.blocking)} blocking issue(s). "
            "Fix them, or release anyway with 'grade-release create --force'."
        )


def run_cli(argv: list[str] | None = None) -> ReleaseContext:
    args = _build_parser().parse_args(argv)
    submissions = Path(args.submissions).expanduser()
    if not submissions.exists():
        print(f"Error: submissions path not found: {submissions}", file=sys.stderr)
        sys.exit(1)

    context = load_release_context(
        submissions, roster_path=args.roster.strip() or None
    )

    if args.json:
        print(json.dumps(_report_payload(context), indent=2))
    else:
        print_readiness(context)

    if args.fail_on_blocking and not context.readiness.ready:
        sys.exit(2)
    return context


def main(argv: list[str] | None = None) -> None:
    run_cli(argv)
