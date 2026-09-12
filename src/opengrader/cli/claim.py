"""Claim and divide assignment work between graders."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from pathlib import Path

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

EPILOG = """
examples:
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


def _slice_from_args(args: Namespace, submissions: Path) -> WorkSlice:
    questions, students = _read_cohort(submissions)
    chosen_questions: list[str] = []
    chosen_students: list[str] = []

    if args.questions:
        chosen_questions = _expand_ids(args.questions, questions)
    if args.students:
        chosen_students = _expand_ids(args.students, students)
    if args.student_range:
        chosen_students = _expand_student_range(args.student_range, students)

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


def _run_list(args: Namespace, submissions: Path) -> None:
    claims = read_claims(submissions)
    if not args.show_all:
        claims = [claim for claim in claims if claim.is_active]

    if args.as_json:
        print(json.dumps([_claim_to_dict(claim) for claim in claims], indent=2))
        return

    if not claims:
        scope = "claims" if args.show_all else "active claims"
        print(f"No {scope}. Everyone sees the whole assignment.")
        return
    label = "claim(s)" if args.show_all else "active claim(s)"
    print(f"{len(claims)} {label}:")
    for claim in claims:
        print(_claim_row(claim))


def _run_status(args: Namespace, submissions: Path) -> None:
    questions, students = _read_cohort(submissions)
    report = coverage_report(
        read_claims(submissions), question_ids=questions, student_ids=students
    )
    if args.as_json:
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

    if args.fail_on_overlap and report.has_overlaps:
        sys.exit(2)


def _run_add(args: Namespace, submissions: Path) -> None:
    work = _slice_from_args(args, submissions)
    try:
        claim = claim_work(
            submissions,
            work,
            actor=args.actor,
            note=args.note,
            allow_overlap=args.force,
            allow_everything=args.everything,
        )
    except ClaimConflictError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        for conflict in exc.conflicts:
            print(f"  {_claim_row(conflict).strip()}", file=sys.stderr)
        sys.exit(2)
    print(f"Claimed {claim.work.describe()} for {claim.actor} ({claim.claim_id}).")


def _run_divide(args: Namespace, submissions: Path) -> None:
    questions, students = _read_cohort(submissions)
    graders = [name.strip() for name in args.graders.split(",") if name.strip()]
    shares = divide_work(
        graders, question_ids=questions, student_ids=students, axis=args.by
    )

    if len(shares) < len(graders):
        print(
            f"Note: only {len(shares)} of {len(graders)} graders got work — there "
            f"are fewer {args.by}s than graders.",
            file=sys.stderr,
        )

    if args.dry_run:
        print(f"Would divide by {args.by}:")
        for actor, work in shares:
            print(f"  {actor:<14} {work.describe()}")
        print("\nDry run: nothing written.")
        return

    for actor, work in shares:
        try:
            claim = claim_work(
                submissions, work, actor=actor, note=args.note, allow_overlap=args.force
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
    print(f"\nDivided by {args.by} between {len(shares)} grader(s).")


def _run_close(args: Namespace, submissions: Path, *, complete: bool) -> None:
    action = complete_claim if complete else release_claim
    claim = action(submissions, args.claim_id, actor=args.actor)
    if claim is None:
        print(f"Error: no claim with id {args.claim_id}", file=sys.stderr)
        sys.exit(1)
    verb = "Completed" if complete else "Released"
    print(f"{verb} {claim.claim_id} ({claim.work.describe()}, {claim.actor}).")


def _add_submissions(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--submissions",
        required=True,
        help="Submissions directory (or a single .submission.md file).",
    )


def _add_actor(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--actor",
        default=None,
        help=(
            "Who is claiming (default: OPENGRADER_GRADER, else your OS user — "
            "the same identity the audit journal records)."
        ),
    )


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Divide grading between several people: claim slices of the class "
            "so two graders never mark the same paper."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    listing = subparsers.add_parser("list", help="Show who holds what.")
    _add_submissions(listing)
    listing.add_argument(
        "--all",
        action="store_true",
        dest="show_all",
        help="Include released and completed claims, not just active ones.",
    )
    listing.add_argument("--json", action="store_true", dest="as_json")

    status = subparsers.add_parser(
        "status", help="Coverage: what is claimed, unclaimed, or double-booked."
    )
    _add_submissions(status)
    status.add_argument("--json", action="store_true", dest="as_json")
    status.add_argument(
        "--fail-on-overlap",
        action="store_true",
        help="Exit non-zero if two graders share work (for scripting/CI).",
    )

    add = subparsers.add_parser("add", help="Claim a slice of the work.")
    _add_submissions(add)
    _add_actor(add)
    add.add_argument(
        "--questions",
        default="",
        help="Question ids: a list (q1,q2,q7) or a range (q1-q3). Omit for all.",
    )
    add.add_argument(
        "--students",
        default="",
        help="Student ids: a list, or a range between two ids. Omit for all.",
    )
    add.add_argument(
        "--student-range",
        default="",
        help=(
            "Students by 1-based position in the class (e.g. 1-40), resolved to "
            "real ids at claim time. The same order the TUI numbers s1..sN."
        ),
    )
    add.add_argument("--note", default="", help="Free-text note stored on the claim.")
    add.add_argument(
        "--force",
        action="store_true",
        help="Claim even if it overlaps someone else's active claim.",
    )
    add.add_argument(
        "--everything",
        action="store_true",
        help="Deliberately claim the whole assignment (no --questions/--students).",
    )

    divide = subparsers.add_parser(
        "divide", help="Split the whole class evenly between graders."
    )
    _add_submissions(divide)
    divide.add_argument(
        "--graders",
        required=True,
        help="Comma-separated grader names, in the order they get slices.",
    )
    divide.add_argument(
        "--by",
        choices=("question", "student"),
        default="question",
        help=(
            "What to cut: question (each grader marks a run of questions for "
            "everyone — one rubric applied consistently, the usual choice) or "
            "student (each grader takes a run of students across all questions)."
        ),
    )
    divide.add_argument("--note", default="", help="Note stored on every claim.")
    divide.add_argument(
        "--dry-run", action="store_true", help="Print the split without writing it."
    )
    divide.add_argument(
        "--force",
        action="store_true",
        help="Divide even if it overlaps existing active claims.",
    )

    release = subparsers.add_parser("release", help="Give a claim back to the pool.")
    release.add_argument("claim_id")
    _add_submissions(release)
    _add_actor(release)

    complete = subparsers.add_parser("complete", help="Mark a claim finished.")
    complete.add_argument("claim_id")
    _add_submissions(complete)
    _add_actor(complete)

    return parser


def run_cli(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    submissions = Path(args.submissions)
    if not submissions.exists():
        print(f"Error: submissions path not found: {submissions}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.command == "list":
            _run_list(args, submissions)
        elif args.command == "status":
            _run_status(args, submissions)
        elif args.command == "add":
            _run_add(args, submissions)
        elif args.command == "divide":
            _run_divide(args, submissions)
        elif args.command == "release":
            _run_close(args, submissions, complete=False)
        elif args.command == "complete":
            _run_close(args, submissions, complete=True)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run_cli()
