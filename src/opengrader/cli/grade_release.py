"""Record grade releases and report changes since the last release."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS, Group
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
    load_workspace_release_context,
    read_release,
    read_releases,
    write_release,
)
from opengrader.core.workspace.store import Workspace

EPILOG = """\
\b
Examples:
  uv run grade-release create --submissions out/subs/
  uv run grade-release create --submissions out/subs/ --label "returned in class" --yes
  uv run grade-release create --submissions out/subs/ --yes --force
  uv run grade-release list   --submissions out/subs/
  uv run grade-release show   --submissions out/subs/ --release rel-2026-08-05-1a2b3c
  uv run grade-release diff   --submissions out/subs/
  uv run grade-release diff   --submissions out/subs/ --release rel-2026-08-05-1a2b3c --json

\b
Note:
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


def _workspace_options(function):
    function = click.option(
        "--assignment", help="Assignment ID within the workspace."
    )(function)
    return click.option(
        "--workspace", type=click.Path(path_type=Path), help="Workspace directory."
    )(function)


def _resolve_submissions(
    *, submissions: str | None, workspace: Path | None, assignment: str | None
) -> tuple[Path, Workspace | None, str | None]:
    if submissions:
        path = Path(submissions).expanduser()
        if not path.exists():
            print(f"Error: submissions path not found: {path}", file=sys.stderr)
            sys.exit(1)
        return path, None, None
    from opengrader.core.workspace.context import current_assignment, submissions_path

    try:
        opened, assignment_id = current_assignment(
            workspace_root=workspace, assignment_id=assignment
        )
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    return submissions_path(opened, assignment_id), opened, assignment_id


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


@click.group(
    "grade-release",
    cls=Group,
    context_settings=CONTEXT_SETTINGS,
    epilog=EPILOG,
)
def command() -> None:
    """Record when grades were handed back, and what moved since."""


@command.command("create")
@click.option("--submissions")
@click.option(
    "--label",
    default="",
    help='Human name for this hand-back, e.g. "returned in class".',
)
@click.option(
    "--roster",
    default="",
    help="Roster TSV; adds the missing-submission check to the readiness gate.",
)
@click.option("--yes", is_flag=True, help="Actually write it (previews otherwise).")
@click.option(
    "--force",
    is_flag=True,
    help="Record the release even though something blocks it.",
)
@_workspace_options
def create_command(
    *,
    submissions: str | None,
    label: str,
    roster: str,
    yes: bool,
    force: bool,
    workspace: Path | None,
    assignment: str | None,
) -> None:
    """Check readiness and record a release (previews without --yes)."""
    folder, opened, assignment_id = _resolve_submissions(
        submissions=submissions, workspace=workspace, assignment=assignment
    )
    context = (
        load_workspace_release_context(
            opened, assignment_id, roster_path=roster.strip() or None
        )
        if opened is not None and assignment_id is not None
        else load_release_context(folder, roster_path=roster.strip() or None)
    )
    print_readiness(context)
    print()

    if not context.readiness.ready and not force:
        print(
            "Refusing to release: fix the blocking issues above, or pass --force "
            "to record the release anyway.",
            file=sys.stderr,
        )
        sys.exit(2)

    if not yes:
        print(
            f"Would record a release of {len(context.documents)} student(s). "
            "Re-run with --yes to write it."
        )
        return

    release = build_release(context.documents, curve=context.curve, label=label)
    try:
        path = write_release(folder, release)
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


@command.command("list")
@click.option("--submissions")
@click.option("--json", "as_json", is_flag=True)
@_workspace_options
def list_command(
    *,
    submissions: str | None,
    as_json: bool,
    workspace: Path | None,
    assignment: str | None,
) -> None:
    """Every recorded release, oldest first."""
    folder, _, _ = _resolve_submissions(
        submissions=submissions, workspace=workspace, assignment=assignment
    )
    releases = read_releases(folder)
    if as_json:
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
        print(f"No releases recorded in {folder}.")
        return
    print(f"{len(releases)} release(s) in {folder}:\n")
    for release in releases:
        label = f"  {release.label}" if release.label else ""
        print(
            f"  {release.release_id}  {release.created_at}  "
            f"{release.student_count} student(s)  by {release.created_by or '?'}{label}"
        )


@command.command("show")
@click.option("--submissions")
@click.option(
    "--release", "release_id", default="", help="Release id (default: the most recent)."
)
@click.option("--json", "as_json", is_flag=True)
@_workspace_options
def show_command(
    *,
    submissions: str | None,
    release_id: str,
    as_json: bool,
    workspace: Path | None,
    assignment: str | None,
) -> None:
    """One release's recorded grades."""
    folder, _, _ = _resolve_submissions(
        submissions=submissions, workspace=workspace, assignment=assignment
    )
    release = _resolve_release(folder, release_id)
    if as_json:
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


@command.command("diff")
@click.option("--submissions")
@click.option(
    "--release", "release_id", default="", help="Release id (default: the most recent)."
)
@click.option(
    "--raw",
    is_flag=True,
    help=(
        "Compare against the release's own curve instead of today's, i.e. "
        "ask 'did any grade change?' rather than 'is their total still what "
        "we told them?'"
    ),
)
@click.option("--json", "as_json", is_flag=True)
@_workspace_options
def diff_command(
    *,
    submissions: str | None,
    release_id: str,
    raw: bool,
    as_json: bool,
    workspace: Path | None,
    assignment: str | None,
) -> None:
    """What has changed since a release handed grades out."""
    folder, opened, assignment_id = _resolve_submissions(
        submissions=submissions, workspace=workspace, assignment=assignment
    )
    release = _resolve_release(folder, release_id)
    context = (
        load_workspace_release_context(opened, assignment_id)
        if opened is not None and assignment_id is not None
        else load_release_context(folder)
    )
    current_curve = None if raw else read_curve(folder)
    comparison = compare_release(release, context.documents, curve=current_curve)

    if as_json:
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
            release.curve if raw else read_curve(folder)
        ),
    )


if __name__ == "__main__":
    command()
