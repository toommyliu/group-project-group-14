"""Claim and divide assignment work between graders."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS, Group
from opengrader.core.claims import (
    ClaimConflictError,
    CoverageReport,
    WorkClaim,
    WorkSlice,
    claim_work,
    complete_claim,
    coverage_report,
    divide_work,
    read_claims,
    release_claim,
)
from opengrader.core.export.gradebook import (
    discover_submission_paths,
    ordered_question_ids,
)
from opengrader.core.submissions.student_file import load_student_submission_markdown

EPILOG = """\
\b
Examples:
  uv run claim divide --submissions out/subs/ --graders alice,bob,carol
  uv run claim divide --submissions out/subs/ --graders alice,bob --by student
  uv run claim add --submissions out/subs/ --questions q1-q3
  uv run claim add --submissions out/subs/ --questions q1,q2,q7 --actor alice
  uv run claim add --submissions out/subs/ --student-range 1-40 --note "morning batch"
  uv run claim list --submissions out/subs/                 # active claims
  uv run claim list --submissions out/subs/ --all --json
  uv run claim status --submissions out/subs/               # gaps and overlaps
  uv run claim release cl-1a2b3c4d --submissions out/subs/
  uv run claim complete cl-1a2b3c4d --submissions out/subs/
"""

_STATUS_GLYPH = {"active": "●", "completed": "✓", "released": "○"}


def _read_cohort(submissions: Path) -> tuple[list[str], list[str]]:
    paths = discover_submission_paths(submissions)
    if not paths:
        raise FileNotFoundError(f"no .submission.md files found under {submissions}")
    documents = []
    for path in paths:
        try:
            documents.append(load_student_submission_markdown(path))
        except ValueError as exc:
            print(f"Warning: could not read {path}: {exc}", file=sys.stderr)
    students = [doc.student_id.strip() for doc in documents if doc.student_id.strip()]
    return ordered_question_ids(documents), students


def _expand_ids(raw: str, known: list[str]) -> list[str]:
    wanted: list[str] = []
    position = {value: index for index, value in enumerate(known)}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk and chunk.count("-") == 1:
            start, end = (part.strip() for part in chunk.split("-"))
            if start in position and end in position:
                low, high = sorted((position[start], position[end]))
                wanted.extend(known[low : high + 1])
                continue
        wanted.append(chunk)

    unknown = [value for value in wanted if value not in position]
    if unknown:
        raise ValueError(f"not in this assignment: {', '.join(unknown)}")
    chosen = set(wanted)
    return [value for value in known if value in chosen]


def _expand_student_range(raw: str, students: list[str]) -> list[str]:
    chunk = raw.strip()
    start_text, _, end_text = chunk.partition("-")
    try:
        start = int(start_text)
        end = int(end_text) if end_text.strip() else start
    except ValueError as exc:
        raise ValueError(f"--student-range wants N-M, got {raw!r}") from exc
    if start < 1 or end < start:
        raise ValueError(f"--student-range wants 1-based N-M with N <= M, got {raw!r}")
    if start > len(students):
        raise ValueError(
            f"--student-range starts at {start} but the class has "
            f"{len(students)} student(s)"
        )
    return students[start - 1 : end]


def _slice_from_args(
    submissions: Path, *, raw_questions: str, raw_students: str, student_range: str
) -> WorkSlice:
    questions, students = _read_cohort(submissions)
    chosen_questions: list[str] = []
    chosen_students: list[str] = []

    if raw_questions:
        chosen_questions = _expand_ids(raw_questions, questions)
    if raw_students:
        chosen_students = _expand_ids(raw_students, students)
    if student_range:
        chosen_students = _expand_student_range(student_range, students)

    return WorkSlice(
        question_ids=tuple(chosen_questions), student_ids=tuple(chosen_students)
    )


def _claim_row(claim: WorkClaim) -> str:
    glyph = _STATUS_GLYPH.get(claim.status, "·")
    note = f"  — {claim.note}" if claim.note else ""
    return (
        f"  {glyph} {claim.claim_id}  {claim.actor:<14} "
        f"{claim.work.describe()}{note}"
    )


def _claim_to_dict(claim: WorkClaim) -> dict[str, object]:
    return {
        "claim_id": claim.claim_id,
        "actor": claim.actor,
        "status": claim.status,
        "questions": list(claim.work.question_ids),
        "students": list(claim.work.student_ids),
        "note": claim.note,
        "created_at": claim.created_at,
        "updated_at": claim.updated_at,
    }


def _print_status(report: CoverageReport) -> None:
    if report.total_cells == 0:
        print("Nothing to divide: no gradable work found.")
        return

    percent = 100.0 * report.claimed_cells / report.total_cells
    print(
        f"{report.claimed_cells}/{report.total_cells} answers claimed "
        f"({percent:.0f}%), {report.unclaimed_cells} unclaimed."
    )
    if report.cells_by_actor:
        print("\nby grader:")
        for actor, cells in sorted(
            report.cells_by_actor.items(), key=lambda item: (-item[1], item[0])
        ):
            print(f"  {actor:<14} {cells} answer(s)")

    if report.has_overlaps:
        print("\n! double-booked work:")
        for overlap in report.overlaps:
            print(
                f"  {overlap.first.actor} and {overlap.second.actor} share "
                f"{overlap.shared_cells} answer(s) "
                f"({overlap.first.claim_id} / {overlap.second.claim_id})"
            )
    elif report.is_fully_claimed:
        print("\nEvery answer is claimed exactly once. ✓")


def _run_list(submissions: Path, *, show_all: bool, as_json: bool) -> None:
    claims = read_claims(submissions)
    if not show_all:
        claims = [claim for claim in claims if claim.is_active]

    if as_json:
        print(json.dumps([_claim_to_dict(claim) for claim in claims], indent=2))
        return

    if not claims:
        scope = "claims" if show_all else "active claims"
        print(f"No {scope}. Everyone sees the whole assignment.")
        return
    label = "claim(s)" if show_all else "active claim(s)"
    print(f"{len(claims)} {label}:")
    for claim in claims:
        print(_claim_row(claim))


def _run_status(submissions: Path, *, as_json: bool, fail_on_overlap: bool) -> None:
    questions, students = _read_cohort(submissions)
    report = coverage_report(
        read_claims(submissions), question_ids=questions, student_ids=students
    )
    if as_json:
        print(
            json.dumps(
                {
                    "total_cells": report.total_cells,
                    "claimed_cells": report.claimed_cells,
                    "unclaimed_cells": report.unclaimed_cells,
                    "cells_by_actor": report.cells_by_actor,
                    "overlaps": [
                        {
                            "first": overlap.first.claim_id,
                            "second": overlap.second.claim_id,
                            "actors": [overlap.first.actor, overlap.second.actor],
                            "shared_cells": overlap.shared_cells,
                        }
                        for overlap in report.overlaps
                    ],
                },
                indent=2,
            )
        )
    else:
        _print_status(report)

    if fail_on_overlap and report.has_overlaps:
        sys.exit(2)


def _run_add(
    submissions: Path,
    *,
    actor: str | None,
    raw_questions: str,
    raw_students: str,
    student_range: str,
    note: str,
    force: bool,
    everything: bool,
) -> None:
    work = _slice_from_args(
        submissions,
        raw_questions=raw_questions,
        raw_students=raw_students,
        student_range=student_range,
    )
    try:
        claim = claim_work(
            submissions,
            work,
            actor=actor,
            note=note,
            allow_overlap=force,
            allow_everything=everything,
        )
    except ClaimConflictError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        for conflict in exc.conflicts:
            print(f"  {_claim_row(conflict).strip()}", file=sys.stderr)
        sys.exit(2)
    print(f"Claimed {claim.work.describe()} for {claim.actor} ({claim.claim_id}).")


def _run_divide(
    submissions: Path,
    *,
    raw_graders: str,
    by: str,
    note: str,
    dry_run: bool,
    force: bool,
) -> None:
    questions, students = _read_cohort(submissions)
    graders = [name.strip() for name in raw_graders.split(",") if name.strip()]
    shares = divide_work(
        graders, question_ids=questions, student_ids=students, axis=by
    )

    if len(shares) < len(graders):
        print(
            f"Note: only {len(shares)} of {len(graders)} graders got work — there "
            f"are fewer {by}s than graders.",
            file=sys.stderr,
        )

    if dry_run:
        print(f"Would divide by {by}:")
        for actor, work in shares:
            print(f"  {actor:<14} {work.describe()}")
        print("\nDry run: nothing written.")
        return

    for actor, work in shares:
        try:
            claim = claim_work(
                submissions, work, actor=actor, note=note, allow_overlap=force
            )
        except ClaimConflictError as exc:
            print(f"Error: {actor}: {exc}", file=sys.stderr)
            print(
                "Nothing further written. Release the existing claims "
                "(claim release …) or re-run with --force.",
                file=sys.stderr,
            )
            sys.exit(2)
        print(f"  {claim.actor:<14} {claim.work.describe()}  ({claim.claim_id})")
    print(f"\nDivided by {by} between {len(shares)} grader(s).")


def _run_close(
    submissions: Path, *, claim_id: str, actor: str | None, complete: bool
) -> None:
    action = complete_claim if complete else release_claim
    claim = action(submissions, claim_id, actor=actor)
    if claim is None:
        print(f"Error: no claim with id {claim_id}", file=sys.stderr)
        sys.exit(1)
    verb = "Completed" if complete else "Released"
    print(f"{verb} {claim.claim_id} ({claim.work.describe()}, {claim.actor}).")


@contextmanager
def _submissions_or_exit(raw: str) -> Iterator[Path]:
    submissions = Path(raw)
    if not submissions.exists():
        print(f"Error: submissions path not found: {submissions}", file=sys.stderr)
        sys.exit(1)
    try:
        yield submissions
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


_submissions_option = click.option(
    "--submissions",
    required=True,
    help="Submissions directory (or a single .submission.md file).",
)
_actor_option = click.option(
    "--actor",
    help=(
        "Who is claiming (default: OPENGRADER_GRADER, else your OS user — "
        "the same identity the audit journal records)."
    ),
)


@click.group("claim", cls=Group, context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
def command() -> None:
    """Divide grading between several people: claim slices of the class so
    two graders never mark the same paper.
    """


@command.command("list")
@_submissions_option
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="Include released and completed claims, not just active ones.",
)
@click.option("--json", "as_json", is_flag=True)
def list_command(submissions: str, show_all: bool, as_json: bool) -> None:
    """Show who holds what."""
    with _submissions_or_exit(submissions) as path:
        _run_list(path, show_all=show_all, as_json=as_json)


@command.command("status")
@_submissions_option
@click.option("--json", "as_json", is_flag=True)
@click.option(
    "--fail-on-overlap",
    is_flag=True,
    help="Exit non-zero if two graders share work (for scripting/CI).",
)
def status_command(submissions: str, as_json: bool, fail_on_overlap: bool) -> None:
    """Coverage: what is claimed, unclaimed, or double-booked."""
    with _submissions_or_exit(submissions) as path:
        _run_status(path, as_json=as_json, fail_on_overlap=fail_on_overlap)


@command.command("add")
@_submissions_option
@_actor_option
@click.option(
    "--questions",
    "raw_questions",
    default="",
    help="Question ids: a list (q1,q2,q7) or a range (q1-q3). Omit for all.",
)
@click.option(
    "--students",
    "raw_students",
    default="",
    help="Student ids: a list, or a range between two ids. Omit for all.",
)
@click.option(
    "--student-range",
    default="",
    help=(
        "Students by 1-based position in the class (e.g. 1-40), resolved to "
        "real ids at claim time. The same order the TUI numbers s1..sN."
    ),
)
@click.option("--note", default="", help="Free-text note stored on the claim.")
@click.option(
    "--force",
    is_flag=True,
    help="Claim even if it overlaps someone else's active claim.",
)
@click.option(
    "--everything",
    is_flag=True,
    help="Deliberately claim the whole assignment (no --questions/--students).",
)
def add_command(
    submissions: str,
    actor: str | None,
    raw_questions: str,
    raw_students: str,
    student_range: str,
    note: str,
    force: bool,
    everything: bool,
) -> None:
    """Claim a slice of the work."""
    with _submissions_or_exit(submissions) as path:
        _run_add(
            path,
            actor=actor,
            raw_questions=raw_questions,
            raw_students=raw_students,
            student_range=student_range,
            note=note,
            force=force,
            everything=everything,
        )


@command.command("divide")
@_submissions_option
@click.option(
    "--graders",
    "raw_graders",
    required=True,
    help="Comma-separated grader names, in the order they get slices.",
)
@click.option(
    "--by",
    type=click.Choice(("question", "student")),
    default="question",
    help=(
        "What to cut: question (each grader marks a run of questions for "
        "everyone — one rubric applied consistently, the usual choice) or "
        "student (each grader takes a run of students across all questions)."
    ),
)
@click.option("--note", default="", help="Note stored on every claim.")
@click.option("--dry-run", is_flag=True, help="Print the split without writing it.")
@click.option(
    "--force",
    is_flag=True,
    help="Divide even if it overlaps existing active claims.",
)
def divide_command(
    submissions: str,
    raw_graders: str,
    by: str,
    note: str,
    dry_run: bool,
    force: bool,
) -> None:
    """Split the whole class evenly between graders."""
    with _submissions_or_exit(submissions) as path:
        _run_divide(
            path,
            raw_graders=raw_graders,
            by=by,
            note=note,
            dry_run=dry_run,
            force=force,
        )


@command.command("release")
@click.argument("claim_id")
@_submissions_option
@_actor_option
def release_command(claim_id: str, submissions: str, actor: str | None) -> None:
    """Give a claim back to the pool."""
    with _submissions_or_exit(submissions) as path:
        _run_close(path, claim_id=claim_id, actor=actor, complete=False)


@command.command("complete")
@click.argument("claim_id")
@_submissions_option
@_actor_option
def complete_command(claim_id: str, submissions: str, actor: str | None) -> None:
    """Mark a claim finished."""
    with _submissions_or_exit(submissions) as path:
        _run_close(path, claim_id=claim_id, actor=actor, complete=True)


if __name__ == "__main__":
    command()
