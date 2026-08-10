"""Grading-session analytics over the durable audit journal (pure, no I/O).

The journal (`core/history/journal.py`) already records *who* changed *what*,
*when*. This engine reads those timestamps back as a productivity view: how much
grading happened, how fast, spread across how many sittings, per grader. It's
the analytics counterpart to the raw `grade-history` list — same data, summarized
instead of enumerated.

Design notes:

* **Active time, not wall-clock.** Two consecutive actions close together are
  "active grading"; a gap longer than ``idle_gap_seconds`` (default 5 min) is a
  break, excluded from active time and treated as a session boundary. So a
  grader who did an hour of work across a morning and an afternoon shows ~1h of
  *active* time over 2 sessions, not the 6h of wall-clock between them.
* **Per-actor, then pooled.** Timing is computed within each actor's own
  timeline (you can't infer cadence across two people grading at once), then the
  overall figures pool those — active seconds and session counts add up, and the
  median cadence pools every within-session gap. This keeps concurrent graders
  from inventing phantom idle time.
* **Deterministic.** Actors are ordered by grading effort (events desc, then
  name) so the report is stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median

from opengrader.core.history.journal import GradeJournalEntry

# Gaps longer than this (seconds) between one grader's actions are treated as a
# break: they don't count toward active grading time and they split sessions.
DEFAULT_IDLE_GAP_SECONDS = 300.0


@dataclass(frozen=True)
class ActorGradingStats:
    """One grader's slice of the grading-session report."""

    actor: str
    total_events: int
    grades: int
    comments: int
    rubric_edits: int
    identity_assignments: int
    students_touched: int
    questions_touched: int
    first_event: datetime | None
    last_event: datetime | None
    sessions: int
    active_seconds: float
    median_seconds_between_events: float | None
    events_per_active_hour: float | None
    grades_per_active_hour: float | None


@dataclass(frozen=True)
class GradingSessionReport:
    """Cohort-wide grading throughput, plus a per-grader breakdown."""

    total_events: int
    grades: int
    comments: int
    rubric_edits: int
    identity_assignments: int
    distinct_actors: int
    distinct_students: int
    first_event: datetime | None
    last_event: datetime | None
    sessions: int
    active_seconds: float
    median_seconds_between_events: float | None
    events_per_active_hour: float | None
    grades_per_active_hour: float | None
    actors: tuple[ActorGradingStats, ...]
    idle_gap_seconds: float


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _rate_per_hour(count: float, active_seconds: float) -> float | None:
    if active_seconds <= 0:
        return None
    return count / (active_seconds / 3600.0)


def _timeline_stats(
    times: list[datetime], idle_gap_seconds: float
) -> tuple[int, float, list[float]]:
    """Return (sessions, active_seconds, within_session_gaps) for one grader.

    ``times`` must be sorted ascending. A gap <= ``idle_gap_seconds`` is active
    grading time and stays within the current session; a longer gap is a break
    that starts a new session and is excluded from active time.
    """
    if not times:
        return 0, 0.0, []
    sessions = 1
    active_seconds = 0.0
    gaps: list[float] = []
    for earlier, later in zip(times, times[1:]):
        delta = (later - earlier).total_seconds()
        if delta < 0:
            delta = 0.0
        if delta <= idle_gap_seconds:
            active_seconds += delta
            gaps.append(delta)
        else:
            sessions += 1
    return sessions, active_seconds, gaps


def _actor_stats(
    actor: str,
    entries: list[GradeJournalEntry],
    idle_gap_seconds: float,
) -> tuple[ActorGradingStats, list[float]]:
    kinds = [entry.kind for entry in entries]
    times = sorted(
        t for t in (_parse_timestamp(entry.timestamp) for entry in entries) if t is not None
    )
    sessions, active_seconds, gaps = _timeline_stats(times, idle_gap_seconds)
    grades = kinds.count("grade")
    stats = ActorGradingStats(
        actor=actor,
        total_events=len(entries),
        grades=grades,
        comments=kinds.count("comment"),
        rubric_edits=kinds.count("rubric"),
        identity_assignments=kinds.count("identity"),
        students_touched=len({e.student_id for e in entries if e.student_id}),
        questions_touched=len({e.question_id for e in entries if e.question_id}),
        first_event=times[0] if times else None,
        last_event=times[-1] if times else None,
        sessions=sessions,
        active_seconds=active_seconds,
        median_seconds_between_events=median(gaps) if gaps else None,
        events_per_active_hour=_rate_per_hour(len(entries), active_seconds),
        grades_per_active_hour=_rate_per_hour(grades, active_seconds),
    )
    return stats, gaps


def build_grading_session_report(
    entries: list[GradeJournalEntry],
    *,
    idle_gap_seconds: float = DEFAULT_IDLE_GAP_SECONDS,
) -> GradingSessionReport:
    """Summarize grading throughput from journal entries.

    Read-only: computes statistics from the entries and returns them; nothing is
    written and no grade is changed. Safe on an empty journal (returns zeros).
    """
    by_actor: dict[str, list[GradeJournalEntry]] = {}
    for entry in entries:
        by_actor.setdefault(entry.actor, []).append(entry)

    actor_stats: list[ActorGradingStats] = []
    pooled_gaps: list[float] = []
    total_active = 0.0
    total_sessions = 0
    for actor, actor_entries in by_actor.items():
        stats, gaps = _actor_stats(actor, actor_entries, idle_gap_seconds)
        actor_stats.append(stats)
        pooled_gaps.extend(gaps)
        total_active += stats.active_seconds
        total_sessions += stats.sessions

    # Most grading effort first; ties broken by name for determinism.
    actor_stats.sort(key=lambda s: (-s.total_events, s.actor))

    all_times = sorted(
        t for t in (_parse_timestamp(e.timestamp) for e in entries) if t is not None
    )
    kinds = [entry.kind for entry in entries]
    grades = kinds.count("grade")

    return GradingSessionReport(
        total_events=len(entries),
        grades=grades,
        comments=kinds.count("comment"),
        rubric_edits=kinds.count("rubric"),
        identity_assignments=kinds.count("identity"),
        distinct_actors=len(by_actor),
        distinct_students=len({e.student_id for e in entries if e.student_id}),
        first_event=all_times[0] if all_times else None,
        last_event=all_times[-1] if all_times else None,
        sessions=total_sessions,
        active_seconds=total_active,
        median_seconds_between_events=median(pooled_gaps) if pooled_gaps else None,
        events_per_active_hour=_rate_per_hour(len(entries), total_active),
        grades_per_active_hour=_rate_per_hour(grades, total_active),
        actors=tuple(actor_stats),
        idle_gap_seconds=idle_gap_seconds,
    )
