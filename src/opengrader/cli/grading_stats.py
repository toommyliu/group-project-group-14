"""Summarize grading throughput from the durable audit journal.

The analytics counterpart to ``grade-history``: instead of listing every change,
report how much grading happened, how fast, over how many sessions, per grader —
computed from the timestamps already recorded under
``<submissions>/.opengrader/history/``. Read-only; never changes a grade.
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.analytics import (
    ActorGradingStats,
    GradingSessionReport,
    build_grading_session_report,
)
from opengrader.core.analytics.grading_session import DEFAULT_IDLE_GAP_SECONDS
from opengrader.core.history import read_journal, resolve_history_dir

EPILOG = """
examples:
  uv run grading-stats --input out/student-submissions/
  uv run grading-stats --input out/student-submissions/ --actor prof_kim
  uv run grading-stats --input out/student-submissions/ --idle-gap 600
  uv run grading-stats --input out/student-submissions/ --json
"""


def _format_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _format_rate(rate: float | None, unit: str) -> str:
    return f"{rate:.1f} {unit}" if rate is not None else "—"


def _format_cadence(seconds: float | None) -> str:
    return f"{seconds:.0f}s" if seconds is not None else "—"


def _print_human(report: GradingSessionReport) -> None:
    if report.total_events == 0:
        print("No grading activity recorded yet for this submissions set.")
        return

    print(
        f"{report.total_events} action(s) by {report.distinct_actors} grader(s) "
        f"across {report.sessions} session(s)."
    )
    print(
        f"  Grades: {report.grades}   Comments: {report.comments}   "
        f"Rubric edits: {report.rubric_edits}   ID assignments: "
        f"{report.identity_assignments}"
    )
    print(f"  Students touched: {report.distinct_students}")
    print(f"  Active grading time: {_format_duration(report.active_seconds)}")
    print(
        f"  Throughput: {_format_rate(report.grades_per_active_hour, 'grades/hr')}"
        f"  ·  {_format_rate(report.events_per_active_hour, 'actions/hr')}"
    )
    print(
        f"  Typical time per action: {_format_cadence(report.median_seconds_between_events)}"
    )
    print(f"  (break threshold: {_format_duration(report.idle_gap_seconds)})\n")

    print("Per grader:")
    for actor in report.actors:
        print(f"  {actor.actor}")
        print(
            f"    {actor.total_events} action(s)"
            f"  ({actor.grades} grade / {actor.comments} comment / "
            f"{actor.rubric_edits} rubric / {actor.identity_assignments} id)"
            f"  ·  {actor.sessions} session(s)"
        )
        print(
            f"    active {_format_duration(actor.active_seconds)}"
            f"  ·  {_format_rate(actor.grades_per_active_hour, 'grades/hr')}"
            f"  ·  ~{_format_cadence(actor.median_seconds_between_events)}/action"
            f"  ·  {actor.students_touched} student(s)"
        )


def _actor_to_dict(actor: ActorGradingStats) -> dict[str, object]:
    return {
        "actor": actor.actor,
        "total_events": actor.total_events,
        "grades": actor.grades,
        "comments": actor.comments,
        "rubric_edits": actor.rubric_edits,
        "identity_assignments": actor.identity_assignments,
        "students_touched": actor.students_touched,
        "questions_touched": actor.questions_touched,
        "first_event": actor.first_event.isoformat() if actor.first_event else None,
        "last_event": actor.last_event.isoformat() if actor.last_event else None,
        "sessions": actor.sessions,
        "active_seconds": actor.active_seconds,
        "median_seconds_between_events": actor.median_seconds_between_events,
        "events_per_active_hour": actor.events_per_active_hour,
        "grades_per_active_hour": actor.grades_per_active_hour,
    }


def _report_to_dict(report: GradingSessionReport) -> dict[str, object]:
    return {
        "total_events": report.total_events,
        "grades": report.grades,
        "comments": report.comments,
        "rubric_edits": report.rubric_edits,
        "identity_assignments": report.identity_assignments,
        "distinct_actors": report.distinct_actors,
        "distinct_students": report.distinct_students,
        "first_event": report.first_event.isoformat() if report.first_event else None,
        "last_event": report.last_event.isoformat() if report.last_event else None,
        "sessions": report.sessions,
        "active_seconds": report.active_seconds,
        "median_seconds_between_events": report.median_seconds_between_events,
        "events_per_active_hour": report.events_per_active_hour,
        "grades_per_active_hour": report.grades_per_active_hour,
        "idle_gap_seconds": report.idle_gap_seconds,
        "actors": [_actor_to_dict(actor) for actor in report.actors],
    }


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Summarize grading throughput (how much, how fast, per grader) from "
            "the recorded grade-history journal."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        required=True,
        help="The submissions directory (or file) whose history to analyze.",
    )
    parser.add_argument(
        "--actor",
        default=None,
        help="Only analyze changes by this grader.",
    )
    parser.add_argument(
        "--idle-gap",
        type=float,
        default=DEFAULT_IDLE_GAP_SECONDS,
        metavar="SECONDS",
        help=(
            "Gaps longer than this between a grader's actions count as a break "
            f"(new session, excluded from active time). Default {DEFAULT_IDLE_GAP_SECONDS:g}."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the report as JSON instead of a readable summary.",
    )
    return parser


def run_cli(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input path not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    entries = read_journal(resolve_history_dir(input_path))
    if args.actor is not None:
        entries = [entry for entry in entries if entry.actor == args.actor]

    report = build_grading_session_report(entries, idle_gap_seconds=args.idle_gap)

    if args.as_json:
        print(json.dumps(_report_to_dict(report), indent=2))
    else:
        _print_human(report)
