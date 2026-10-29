"""Preview and restore grades from the audit journal."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS, Group
from opengrader.core.export.gradebook import discover_submission_paths
from opengrader.core.history import (
    RESTORABLE_ASPECTS,
    Aspect,
    GradeJournal,
    RestoreError,
    RestorePlan,
    apply_restore_plan,
    current_cells_from_documents,
    document_student_key,
    format_aspect_value,
    parse_restore_time,
    plan_restore,
    read_journal,
    replay_journal,
    resolve_history_dir,
)
from opengrader.core.submissions.student_file import load_student_submission_markdown

EPILOG = """\
\b
Examples:
  opengrader grade-restore show --submissions out/subs/ --at 2026-08-02T12:00
  opengrader grade-restore show --submissions out/subs/ --at 2h --student 012345
  opengrader grade-restore diff --submissions out/subs/ --at 90m
  opengrader grade-restore apply --submissions out/subs/ --at 2026-08-02T12:00 --yes
  opengrader grade-restore apply --submissions out/subs/ --question q3 --at 1d --yes
  opengrader grade-restore apply --submissions out/subs/ --without-actor bot --yes
  opengrader grade-restore log --submissions out/subs/          # who touched what, when

\b
Times:
  --at accepts 2026-08-02T14:30, 2026-08-02 14:30, 2026-08-02 (start of day),
  a full ISO timestamp with an offset, or a relative age: 90s / 30m / 2h / 1d / 1w.
  A time without a timezone is read as this machine's local clock.
"""


_submissions_option = click.option(
    "--submissions",
    required=True,
    help="Submissions directory (or a single .submission.md file).",
)


def _scope_options(function):
    function = click.option(
        "--without-actor",
        multiple=True,
        metavar="NAME",
        help=(
            "Replay as if this grader never acted (repeatable). Their changes "
            "are undone only where nobody edited the same answer afterwards."
        ),
    )(function)
    function = click.option(
        "--at",
        default="",
        metavar="TIME",
        help="The point in time to reconstruct (see 'times' below).",
    )(function)
    function = click.option(
        "--only",
        default="",
        metavar="ASPECTS",
        help=(
            "Comma-separated aspects to consider: grade, comment, transcription "
            "(default: all three)."
        ),
    )(function)
    function = click.option(
        "--question",
        multiple=True,
        metavar="QID",
        help="Limit to this question id, e.g. q3 (repeatable).",
    )(function)
    function = click.option(
        "--student",
        multiple=True,
        metavar="ID",
        help="Limit to this student id (repeatable).",
    )(function)
    return _submissions_option(function)


@click.group(
    "grade-restore",
    cls=Group,
    context_settings=CONTEXT_SETTINGS,
    epilog=EPILOG,
)
def command() -> None:
    """Reconstruct and restore grades from the audit journal: inspect the
    state at any past moment, diff it against today, and write it back.
    """


def _resolve_submissions(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.exists():
        print(f"Error: submissions path not found: {path}", file=sys.stderr)
        sys.exit(1)
    return path


def _parse_aspects(raw: str) -> tuple[Aspect, ...]:
    if not raw.strip():
        return RESTORABLE_ASPECTS
    chosen: list[Aspect] = []
    for token in raw.split(","):
        name = token.strip().lower()
        if not name:
            continue
        if name not in RESTORABLE_ASPECTS:
            print(
                f"Error: unknown aspect {name!r} — choose from "
                f"{', '.join(RESTORABLE_ASPECTS)}",
                file=sys.stderr,
            )
            sys.exit(1)
        chosen.append(name)  # type: ignore[arg-type]
    return tuple(chosen) or RESTORABLE_ASPECTS


def _parse_at(raw: str) -> datetime | None:
    if not raw.strip():
        return None
    try:
        return parse_restore_time(raw)
    except RestoreError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _load_documents(path: Path) -> list[tuple[Path, object]]:
    loaded: list[tuple[Path, object]] = []
    for submission_path in discover_submission_paths(path):
        try:
            loaded.append(
                (submission_path, load_student_submission_markdown(submission_path))
            )
        except (OSError, ValueError) as exc:
            print(f"Warning: skipping {submission_path.name}: {exc}", file=sys.stderr)
    return loaded


def _prepare(  # noqa: ANN202
    *,
    submissions: str,
    student: tuple[str, ...],
    question: tuple[str, ...],
    only: str,
    at: str,
    without_actor: tuple[str, ...],
):
    path = _resolve_submissions(submissions)
    entries = read_journal(resolve_history_dir(path))
    if not entries:
        print(
            f"No grade history recorded for {path}.\n"
            "Nothing can be restored: the journal is written as grades are saved "
            "in the TUI (or by automated-grading), so a folder graded before "
            "journalling — or never graded — has no history to rewind.",
            file=sys.stderr,
        )
        sys.exit(1)

    snapshot = replay_journal(
        entries, at=_parse_at(at), exclude_actors=without_actor
    )

    documents = _load_documents(path)
    paths_by_key = {}
    for submission_path, document in documents:
        key = document_student_key(document)  # type: ignore[arg-type]
        if key:
            paths_by_key.setdefault(key, submission_path)
    current = current_cells_from_documents(doc for _, doc in documents)  # type: ignore[misc]

    plan = plan_restore(
        snapshot,
        current,
        students=student,
        questions=question,
        aspects=_parse_aspects(only),
        known_students=paths_by_key.keys(),
    )
    return path, snapshot, plan, paths_by_key


def _describe_scope(
    snapshot,  # noqa: ANN001
    *,
    student: tuple[str, ...],
    question: tuple[str, ...],
    without_actor: tuple[str, ...],
) -> str:
    parts = []
    if snapshot.at is None:
        parts.append("the whole journal")
    else:
        parts.append(f"the state at {snapshot.at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    if without_actor:
        parts.append(f"without {', '.join(without_actor)}")
    if student:
        parts.append(f"students {', '.join(student)}")
    if question:
        parts.append(f"questions {', '.join(question)}")
    return "; ".join(parts)


def _print_unresolved(plan: RestorePlan) -> None:
    if not plan.unresolved:
        return
    print(
        f"\n{len(plan.unresolved)} student(s) in the history have no submission "
        "file here and were skipped:",
        file=sys.stderr,
    )
    for key in plan.unresolved:
        print(f"  - {key}", file=sys.stderr)


@command.command("show")
@_scope_options
def show_command(
    submissions: str,
    student: tuple[str, ...],
    question: tuple[str, ...],
    only: str,
    at: str,
    without_actor: tuple[str, ...],
) -> None:
    """Print the reconstructed grades at a point in time."""
    path, snapshot, plan, _paths = _prepare(
        submissions=submissions,
        student=student,
        question=question,
        only=only,
        at=at,
        without_actor=without_actor,
    )
    scope = _describe_scope(
        snapshot, student=student, question=question, without_actor=without_actor
    )
    print(f"Reconstructed {scope} for {path}.\n")

    wanted_students = {s.strip().casefold() for s in student if s.strip()}
    wanted_questions = {q.strip().casefold() for q in question if q.strip()}
    aspects = _parse_aspects(only)

    by_student: dict[str, list[tuple[str, str]]] = {}
    for (key, question_id), cell in snapshot.cells.items():
        if wanted_students and key not in wanted_students:
            continue
        if wanted_questions and question_id.strip().casefold() not in wanted_questions:
            continue
        for aspect in aspects:
            value = cell.value(aspect)
            if value is None:
                continue
            by_student.setdefault(snapshot.label_for(key), []).append(
                (question_id, f"{aspect}: {format_aspect_value(aspect, value)}")
            )

    if not by_student:
        print("The journal has no record matching that scope.")
        return

    for label in sorted(by_student, key=str.lower):
        print(label)
        for question_id, rendered in sorted(by_student[label]):
            print(f"  {question_id:<6} {rendered}")
        print()

    if snapshot.unattributed_entries:
        print(
            f"Note: {snapshot.unattributed_entries} journal entry(ies) could not be "
            "tied to a student (no id and no name recorded) and were ignored.",
            file=sys.stderr,
        )
    _print_unresolved(plan)


def _print_plan(plan: RestorePlan) -> None:
    for change in plan.changes:
        print(f"  {change.describe()}")


@command.command("diff")
@_scope_options
def diff_command(
    submissions: str,
    student: tuple[str, ...],
    question: tuple[str, ...],
    only: str,
    at: str,
    without_actor: tuple[str, ...],
) -> None:
    """Show what differs between then and the files as they are now."""
    _path, snapshot, plan, _paths = _prepare(
        submissions=submissions,
        student=student,
        question=question,
        only=only,
        at=at,
        without_actor=without_actor,
    )
    scope = _describe_scope(
        snapshot, student=student, question=question, without_actor=without_actor
    )
    if not plan:
        print(f"No differences: the files already match {scope}.")
        _print_unresolved(plan)
        return
    print(
        f"{len(plan.changes)} change(s) across {plan.student_count} student(s) "
        f"between {scope} and the files as they are now:\n"
    )
    _print_plan(plan)
    _print_unresolved(plan)


@command.command("apply")
@_scope_options
@click.option(
    "--yes",
    is_flag=True,
    help="Actually write. Without it the changes are only previewed.",
)
@click.option(
    "--reason",
    default="",
    help="Why the restore was performed; recorded in the audit journal.",
)
def apply_command(
    submissions: str,
    student: tuple[str, ...],
    question: tuple[str, ...],
    only: str,
    at: str,
    without_actor: tuple[str, ...],
    yes: bool,
    reason: str,
) -> None:
    """Write the reconstructed grades back to the submission files."""
    path, snapshot, plan, paths_by_key = _prepare(
        submissions=submissions,
        student=student,
        question=question,
        only=only,
        at=at,
        without_actor=without_actor,
    )
    scope = _describe_scope(
        snapshot, student=student, question=question, without_actor=without_actor
    )
    if not plan:
        print(f"Nothing to restore: the files already match {scope}.")
        _print_unresolved(plan)
        return

    print(
        f"{len(plan.changes)} change(s) across {plan.student_count} student(s) "
        f"to restore {scope}:\n"
    )
    _print_plan(plan)
    _print_unresolved(plan)

    if not yes:
        print("\nPreview only. Re-run with --yes to write these changes.")
        return

    journal = GradeJournal(resolve_history_dir(path))
    outcome = apply_restore_plan(
        plan, paths_by_key, journal=journal, reason=reason
    )
    print(
        f"\nRestored {len(outcome.written)} change(s) across "
        f"{outcome.files_touched} student(s)."
    )
    if outcome.failed:
        print(f"{len(outcome.failed)} change(s) could not be written:", file=sys.stderr)
        for change, error in outcome.failed:
            print(f"  {change.describe()}\n    {error}", file=sys.stderr)
        sys.exit(1)


@command.command("log")
@_submissions_option
def log_command(submissions: str) -> None:
    """Summarize the journal: actors, time span, and what is restorable."""
    path = _resolve_submissions(submissions)
    entries = read_journal(resolve_history_dir(path))
    if not entries:
        print(f"No grade history recorded for {path}.")
        return

    by_actor: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for entry in entries:
        by_actor[entry.actor] = by_actor.get(entry.actor, 0) + 1
        by_kind[entry.kind] = by_kind.get(entry.kind, 0) + 1

    restorable = sum(by_kind.get(aspect, 0) for aspect in RESTORABLE_ASPECTS)
    first = entries[0].timestamp
    last = entries[-1].timestamp
    print(f"{len(entries)} journal entries for {path}")
    print(f"  span:       {first}  →  {last}")
    print(f"  restorable: {restorable} (grade/comment/transcription)")
    print("  by actor:")
    for actor, count in sorted(by_actor.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    {actor:<20} {count}")
    print("  by kind:")
    for kind, count in sorted(by_kind.items(), key=lambda kv: (-kv[1], kv[0])):
        note = "" if kind in RESTORABLE_ASPECTS else "  (not restorable)"
        print(f"    {kind:<20} {count}{note}")


if __name__ == "__main__":
    command()
