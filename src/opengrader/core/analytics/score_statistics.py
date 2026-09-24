from __future__ import annotations

from dataclasses import dataclass
import statistics


@dataclass(frozen=True)
class ScoreBucket:
    label: str
    count: int


@dataclass(frozen=True)
class QuestionStatistics:
    question_id: str
    possible_points: float
    total_submissions: int
    graded_count: int
    ungraded_count: int
    average: float | None
    median: float | None
    std_dev: float | None
    minimum: float | None
    maximum: float | None
    q1: float | None
    q3: float | None
    average_percent: float | None
    full_credit_count: int
    zero_score_count: int
    buckets: tuple[ScoreBucket, ...]


_PERCENT_BUCKETS = (
    ("0-20%", 0.0, 20.0),
    ("20-40%", 20.0, 40.0),
    ("40-60%", 40.0, 60.0),
    ("60-80%", 60.0, 80.0),
    ("80-100%", 80.0, 100.0),
    ("100%+", 100.0, None),
)


def statistics_from_scores(
    question_id: str,
    scores: list[float],
    *,
    possible_points: float,
    total_submissions: int,
) -> QuestionStatistics:
    graded_count = len(scores)
    ungraded_count = max(0, total_submissions - graded_count)

    if not scores:
        return QuestionStatistics(
            question_id=question_id,
            possible_points=possible_points,
            total_submissions=total_submissions,
            graded_count=0,
            ungraded_count=ungraded_count,
            average=None,
            median=None,
            std_dev=None,
            minimum=None,
            maximum=None,
            q1=None,
            q3=None,
            average_percent=None,
            full_credit_count=0,
            zero_score_count=0,
            buckets=_empty_buckets(possible_points),
        )

    average = statistics.fmean(scores)
    q1, q3 = _quartiles(scores)
    return QuestionStatistics(
        question_id=question_id,
        possible_points=possible_points,
        total_submissions=total_submissions,
        graded_count=graded_count,
        ungraded_count=ungraded_count,
        average=average,
        median=statistics.median(scores),
        std_dev=statistics.pstdev(scores) if len(scores) > 1 else 0.0,
        minimum=min(scores),
        maximum=max(scores),
        q1=q1,
        q3=q3,
        average_percent=(
            (average / possible_points) * 100.0 if possible_points > 0 else None
        ),
        full_credit_count=sum(1 for score in scores if score >= possible_points)
        if possible_points > 0
        else 0,
        zero_score_count=sum(1 for score in scores if score == 0),
        buckets=_score_buckets(scores, possible_points),
    )


def _quartiles(scores: list[float]) -> tuple[float, float]:
    ordered = sorted(scores)
    if len(ordered) == 1:
        return ordered[0], ordered[0]

    midpoint = len(ordered) // 2
    if len(ordered) % 2 == 0:
        lower = ordered[:midpoint]
        upper = ordered[midpoint:]
    else:
        lower = ordered[: midpoint + 1]
        upper = ordered[midpoint:]
    return statistics.median(lower), statistics.median(upper)


def _empty_buckets(possible_points: float) -> tuple[ScoreBucket, ...]:
    if possible_points > 0:
        return tuple(ScoreBucket(label, 0) for label, _, _ in _PERCENT_BUCKETS)
    return ()


def _score_buckets(
    scores: list[float],
    possible_points: float,
) -> tuple[ScoreBucket, ...]:
    if possible_points <= 0:
        counts: dict[float, int] = {}
        for score in scores:
            counts[score] = counts.get(score, 0) + 1
        return tuple(
            ScoreBucket(_format_number(score), counts[score]) for score in sorted(counts)
        )

    counts = {label: 0 for label, _, _ in _PERCENT_BUCKETS}
    for score in scores:
        percent = (score / possible_points) * 100.0
        bucket_percent = max(0.0, percent)
        for label, lower, upper in _PERCENT_BUCKETS:
            if bucket_percent >= lower and (upper is None or bucket_percent < upper):
                counts[label] += 1
                break

    return tuple(ScoreBucket(label, counts[label]) for label, _, _ in _PERCENT_BUCKETS)


def _format_number(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.1f}"
