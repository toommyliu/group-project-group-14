"""Record grade releases and report changes since the last release."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.cli.grade_check import print_readiness
from opengrader.core.curve import describe_curve, read_curve
from opengrader.core.release import (
    GradeRelease,
    ReleaseComparison,
    ReleaseError,
    build_release,
    compare_release,
    latest_release,
    load_release_context,
    read_release,
    read_releases,
    write_release,
)

EPILOG = """
examples:
  uv run grade-release create --submissions out/subs/
  uv run grade-release create --submissions out/subs/ --label "returned in class" --yes
  uv run grade-release create --submissions out/subs/ --yes --force
  uv run grade-release list   --submissions out/subs/
  uv run grade-release show   --submissions out/subs/ --release rel-2026-08-05-1a2b3c
  uv run grade-release diff   --submissions out/subs/
  uv run grade-release diff   --submissions out/subs/ --release rel-2026-08-05-1a2b3c --json

note:
  A release never blocks a later grade change. 'diff' is how you find the ones
  that happened after the students saw their grades.
"""

_CHANGE_LABELS = {
    "raised": "raised",
    "lowered": "lowered",
    "newly_graded": "graded since",
    "ungraded": "un-graded since",
    "added_question": "new question",
    "removed_question": "question gone",
}


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description="Record when grades were handed back, and what moved since.",
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser(
        "create", help="Check readiness and record a release (previews without --yes)."
    )
    create.add_argument("--submissions", required=True)
    create.add_argument(
        "--label",
        default="",
        help='Human name for this hand-back, e.g. "returned in class".',
    )
    create.add_argument(
        "--roster",
        default="",
        help="Roster TSV; adds the missing-submission check to the readiness gate.",
    )
    create.add_argument(
        "--yes", action="store_true", help="Actually write it (previews otherwise)."
    )
    create.add_argument(
        "--force",
        action="store_true",
        help="Record the release even though something blocks it.",
    )

    listing = subparsers.add_parser("list", help="Every recorded release, oldest first.")
    listing.add_argument("--submissions", required=True)
    listing.add_argument("--json", action="store_true")

    show = subparsers.add_parser("show", help="One release's recorded grades.")
    show.add_argument("--submissions", required=True)
    show.add_argument(
        "--release", default="", help="Release id (default: the most recent)."
    )
    show.add_argument("--json", action="store_true")

    diff = subparsers.add_parser(
        "diff", help="What has changed since a release handed grades out."
    )
    diff.add_argument("--submissions", required=True)
    diff.add_argument(
        "--release", default="", help="Release id (default: the most recent)."
    )
    diff.add_argument(
        "--raw",
        action="store_true",
        help=(
            "Compare against the release's own curve instead of today's, i.e. "
            "ask 'did any grade change?' rather than 'is their total still what "
            "we told them?'"
        ),
    )
    diff.add_argument("--json", action="store_true")

    return parser


def _resolve_submissions(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.exists():
        print(f"Error: submissions path not found: {path}", file=sys.stderr)
        sys.exit(1)
    return path


def _resolve_release(submissions: Path, release_id: str) -> GradeRelease:
    if release_id.strip():
        release = read_release(submissions, release_id)
        if release is None:
            print(
                f"Error: no release {release_id!r} in {submissions}. "
                "Run 'grade-release list' to see what is recorded.",
                file=sys.stderr,
            )
            sys.exit(1)
        return release
    release = latest_release(submissions)
    if release is None:
        print(
            f"Error: nothing has been released from {submissions} yet — "
            "run 'grade-release create' first.",
            file=sys.stderr,
        )
        sys.exit(1)
    return release


def _number(value: float | None) -> str:
    return f"{value:g}" if value is not None else "—"


def _cmd_create(args: Namespace) -> None:
    submissions = _resolve_submissions(args.submissions)
    context = load_release_context(
        submissions, roster_path=args.roster.strip() or None
    )
    print_readiness(context)
    print()

    if not context.readiness.ready and not args.force:
        print(
            "Refusing to release: fix the blocking issues above, or pass --force "
            "to record the release anyway.",
            file=sys.stderr,
        )
        sys.exit(2)

    if not args.yes:
        print(
            f"Would record a release of {len(context.documents)} student(s). "
            "Re-run with --yes to write it."
        )
        return

    release = build_release(
        context.documents, curve=context.curve, label=args.label
    )
    try:
        path = write_release(submissions, release)
    except ReleaseError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Released {release.student_count} student(s) as {release.release_id}")
    if release.label:
        print(f"  label: {release.label}")
    print(f"  curve: {describe_curve(release.curve)}")
    print(f"  → {path}")
    print()
    print(
        "This changes no grade and blocks nothing. When a grade moves later, "
        "'grade-release diff' will name it."
    )
    if not context.readiness.ready:
        print(
            f"(Recorded with --force over {len(context.readiness.blocking)} "
            "blocking issue(s).)"
        )


def _cmd_list(args: Namespace) -> None:
    submissions = _resolve_submissions(args.submissions)
    releases = read_releases(submissions)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "release_id": release.release_id,
                        "created_at": release.created_at,
                        "created_by": release.created_by,
                        "label": release.label,
                        "students": release.student_count,
                        "curve": release.curve.to_dict(),
                    }
                    for release in releases
                ],
                indent=2,
            )
        )
        return
    if not releases:
        print(f"No releases recorded in {submissions}.")
        return
    print(f"{len(releases)} release(s) in {submissions}:\n")
    for release in releases:
        label = f"  {release.label}" if release.label else ""
        print(
            f"  {release.release_id}  {release.created_at}  "
            f"{release.student_count} student(s)  by {release.created_by or '?'}{label}"
        )


def _cmd_show(args: Namespace) -> None:
    submissions = _resolve_submissions(args.submissions)
    release = _resolve_release(submissions, args.release)
    if args.json:
        print(
            json.dumps(
                {
                    "release_id": release.release_id,
                    "created_at": release.created_at,
                    "created_by": release.created_by,
                    "label": release.label,
                    "curve": release.curve.to_dict(),
                    "students": [
                        {
                            "student_id": student.student_id,
                            "student_name": student.student_name,
                            "total": student.total,
                            "curved_total": student.curved_total,
                            "possible": student.possible,
                            "questions": {
                                question.question_id: question.points
                                for question in student.questions
                            },
                        }
                        for student in release.students
                    ],
                },
                indent=2,
            )
        )
        return

    print(f"{release.release_id}  {release.created_at}")
    if release.label:
        print(f"label: {release.label}")
    print(f"curve: {describe_curve(release.curve)}")
    print(f"\n{release.student_count} student(s) as handed back:\n")
    for student in release.students:
        shown = (
            f"{_number(student.curved_total)}/{_number(student.possible)}"
            if release.curve.is_active
            else f"{_number(student.total)}/{_number(student.possible)}"
        )
        raw_note = (
            f"  (raw {_number(student.total)})" if release.curve.is_active else ""
        )
        print(
            f"  {student.student_id or '(no id)':<12} "
            f"{student.student_name or '':<24} {shown}{raw_note}"
        )


def _print_comparison(comparison: ReleaseComparison, *, compared_curve: str) -> None:
    print(
        f"Since {comparison.release_id} ({comparison.released_at}), "
        f"compared with curve: {compared_curve}\n"
    )

    if comparison.missing_students:
        print(
            f"{len(comparison.missing_students)} released student(s) have no "
            "submission in the folder now:"
        )
        for student in comparison.missing_students:
            print(f"  {student}")
        print()
    if comparison.new_students:
        print(
            f"{len(comparison.new_students)} student(s) in the folder were not in "
            "the release:"
        )
        for student in comparison.new_students:
            print(f"  {student}")
        print()
    if comparison.curve_changed:
        print("The curve has changed since the release.\n")

    if comparison.unchanged:
        print("No grade has changed since it was handed back.")
        return

    if comparison.changes:
        print(f"{len(comparison.changes)} question grade(s) changed:")
        for change in comparison.changes:
            label = _CHANGE_LABELS.get(change.kind, change.kind)
            who = change.student_id or change.student_key
            print(
                f"  {who:<12} {change.question_id:<6} {label:<15} "
                f"{_number(change.before)} → {_number(change.after)}"
            )
        print()

    if comparison.totals:
        print(
            f"{len(comparison.totals)} student(s) now have a different total than "
            "they were shown:"
        )
        for total in comparison.totals:
            who = total.student_id or total.student_key
            print(
                f"  {who:<12} {_number(total.before)} → {_number(total.after)} "
                f"({total.delta:+g})"
            )


def _cmd_diff(args: Namespace) -> None:
    submissions = _resolve_submissions(args.submissions)
    release = _resolve_release(submissions, args.release)
    context = load_release_context(submissions)
    current_curve = None if args.raw else read_curve(submissions)
    comparison = compare_release(release, context.documents, curve=current_curve)

    if args.json:
        print(
            json.dumps(
                {
                    "release_id": comparison.release_id,
                    "released_at": comparison.released_at,
                    "curve_changed": comparison.curve_changed,
                    "missing_students": list(comparison.missing_students),
                    "new_students": list(comparison.new_students),
                    "changes": [
                        {
                            "student_id": change.student_id,
                            "student_name": change.student_name,
                            "question_id": change.question_id,
                            "kind": change.kind,
                            "before": change.before,
                            "after": change.after,
                        }
                        for change in comparison.changes
                    ],
                    "totals": [
                        {
                            "student_id": total.student_id,
                            "student_name": total.student_name,
                            "before": total.before,
                            "after": total.after,
                            "possible": total.possible,
                        }
                        for total in comparison.totals
                    ],
                },
                indent=2,
            )
        )
        return

    if context.unreadable_files:
        print(
            f"WARNING: {len(context.unreadable_files)} submission file(s) could "
            f"not be read and are not compared: {', '.join(context.unreadable_files)}\n",
            file=sys.stderr,
        )
    _print_comparison(
        comparison,
        compared_curve=describe_curve(
            release.curve if args.raw else read_curve(submissions)
        ),
    )


_COMMANDS = {
    "create": _cmd_create,
    "list": _cmd_list,
    "show": _cmd_show,
    "diff": _cmd_diff,
}


def run_cli(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    _COMMANDS[args.command](args)
