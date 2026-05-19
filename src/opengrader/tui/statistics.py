from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import statistics

from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator
from PIL import Image as PILImage

from opengrader.core.qa_file.scoring import awarded_points_for_rubric_selection
from opengrader.tui.data import QuestionSeed


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


def selected_option_indices_for_seed(seed: QuestionSeed) -> set[int]:
    """Resolve persisted rubric state to option indices without guessing."""
    if seed.selected_rubric_labels:
        selected_labels = set(seed.selected_rubric_labels)
        selected_indices = {
            index
            for index, option in enumerate(seed.rubric_options)
            if option.label in selected_labels
        }
        if len(selected_indices) != len(selected_labels):
            return set()
        return selected_indices

    if seed.points_awarded is None:
        return set()

    standard_rubric_deltas = [o.points_delta for o in seed.rubric_options if o.kind != "bonus"]
    matching_indices = [
        index
        for index, option in enumerate(seed.rubric_options)
        if option.kind != "bonus" and awarded_points_for_rubric_selection(
            seed.points,
            [option.points_delta],
            standard_rubric_deltas,
        )
        == seed.points_awarded
    ]
    if len(matching_indices) == 1:
        return {matching_indices[0]}
    return set()


def seed_is_graded(seed: QuestionSeed) -> bool:
    return bool(selected_option_indices_for_seed(seed))


def score_from_seed(seed: QuestionSeed) -> float | None:
    if seed.points_awarded is not None:
        return seed.points_awarded

    selected_indices = selected_option_indices_for_seed(seed)
    if not selected_indices:
        return None

    standard_selected = []
    bonus_selected = []
    for index, option in enumerate(seed.rubric_options):
        if index in selected_indices:
            if option.kind == "bonus":
                bonus_selected.append(option.points_delta)
            else:
                standard_selected.append(option.points_delta)

    return awarded_points_for_rubric_selection(
        seed.points,
        standard_selected,
        [o.points_delta for o in seed.rubric_options if o.kind != "bonus"],
        bonus_points_values=bonus_selected,
    )


def compute_question_statistics(
    question_id: str,
    seeds: list[QuestionSeed],
    *,
    total_submissions: int,
) -> QuestionStatistics:
    possible_points = _possible_points(seeds)
    scores = [score for seed in seeds if (score := score_from_seed(seed)) is not None]
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


def render_question_statistics(
    stats: QuestionStatistics,
    *,
    bar_width: int = 18,
    include_distribution: bool = True,
) -> str:
    lines = ["Stats"]
    if stats.graded_count == 0:
        lines.append("No graded scores yet.")
        return "\n".join(lines)

    lines.extend(
        [
            (
                f"Graded: {stats.graded_count} / {stats.total_submissions}   "
                f"Ungraded: {stats.ungraded_count}"
            ),
            (
                f"Avg: {_format_number(stats.average)} / "
                f"{_format_number(stats.possible_points)}"
                f"{_format_percent_suffix(stats.average_percent)}"
            ),
            (
                f"Median: {_format_number(stats.median)}   "
                f"Std dev: {_format_number(stats.std_dev)}"
            ),
            (
                f"Min/Max: {_format_number(stats.minimum)} / "
                f"{_format_number(stats.maximum)}   "
                f"Q1/Q3: {_format_number(stats.q1)} / {_format_number(stats.q3)}"
            ),
            (
                f"Full credit: {stats.full_credit_count} "
                f"({_format_rate(stats.full_credit_count, stats.graded_count)})   "
                f"Zero: {stats.zero_score_count} "
                f"({_format_rate(stats.zero_score_count, stats.graded_count)})"
            ),
        ]
    )
    if include_distribution:
        lines.extend(("", "Distribution"))
        lines.extend(_render_buckets(stats.buckets, bar_width=bar_width))
    return "\n".join(lines)


def question_statistics_chart_image(
    stats: QuestionStatistics,
    *,
    width: int = 1100,
    height: int = 380,
) -> PILImage.Image | None:
    if stats.graded_count == 0 or not stats.buckets:
        return None

    dpi = 140
    figure = Figure(
        figsize=(width / dpi, height / dpi),
        dpi=dpi,
        facecolor="#181818",
    )
    axis = figure.subplots()
    axis.set_facecolor("#181818")

    labels = [_short_label(bucket.label) for bucket in stats.buckets]
    counts = [bucket.count for bucket in stats.buckets]
    max_count = max(bucket.count for bucket in stats.buckets)
    colors = ["#81A1C1" if label != "100+" else "#A3BE8C" for label in labels]
    bars = axis.bar(labels, counts, color=colors, edgecolor="#D8DEE9", linewidth=0.8)

    axis.set_title(
        f"{stats.question_id} score distribution",
        color="#E4E4E4",
        fontsize=10,
        loc="left",
        pad=8,
    )
    axis.set_ylabel("Students", color="#AEB6BF", fontsize=8)
    axis.set_ylim(0, max(1, max_count) * 1.18)
    axis.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=4))
    axis.grid(axis="y", color="#3B424A", linewidth=0.8, alpha=0.7)
    axis.set_axisbelow(True)
    axis.tick_params(axis="x", colors="#AEB6BF", labelsize=7, length=0, pad=5)
    axis.tick_params(axis="y", colors="#AEB6BF", labelsize=7, length=0)
    for spine in axis.spines.values():
        spine.set_color("#4B5563")
        spine.set_linewidth(0.8)

    for bar, count in zip(bars, counts, strict=True):
        if count == 0:
            continue
        axis.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            str(count),
            ha="center",
            va="bottom",
            color="#ECEFF4",
            fontsize=8,
            fontweight="bold",
        )

    figure.tight_layout(pad=0.7)
    buffer = BytesIO()
    figure.savefig(buffer, format="png", facecolor=figure.get_facecolor())
    buffer.seek(0)
    return PILImage.open(buffer).convert("RGB")


def _possible_points(seeds: list[QuestionSeed]) -> float:
    for seed in seeds:
        return seed.points
    return 0.0


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


def _render_buckets(buckets: tuple[ScoreBucket, ...], *, bar_width: int) -> list[str]:
    max_count = max((bucket.count for bucket in buckets), default=0)
    label_width = max(8, max((len(bucket.label) for bucket in buckets), default=0))
    lines: list[str] = []
    for bucket in buckets:
        bar_length = 0
        if max_count > 0 and bucket.count > 0:
            bar_length = max(1, round(bucket.count / max_count * bar_width))
        lines.append(
            f"{bucket.label:<{label_width}} | {'#' * bar_length} {bucket.count}"
        )
    return lines


def _short_label(label: str) -> str:
    replacements = {
        "0-20%": "0-20",
        "20-40%": "20-40",
        "40-60%": "40-60",
        "60-80%": "60-80",
        "80-100%": "80-100",
        "100%+": "100+",
    }
    return replacements.get(label, label)


def _format_number(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.1f}"


def _format_percent_suffix(value: float | None) -> str:
    if value is None:
        return ""
    return f" ({value:.1f}%)"


def _format_rate(count: int, total: int) -> str:
    if total <= 0:
        return "0.0%"
    return f"{count / total * 100.0:.1f}%"
