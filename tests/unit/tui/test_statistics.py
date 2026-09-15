import pytest

from opengrader.tui.data import QuestionSeed, RubricOptionSeed
from opengrader.tui.statistics import (
    compute_question_statistics,
    question_statistics_chart_image,
    render_question_statistics,
    score_from_seed,
    seed_is_graded,
)


def _seed(
    *,
    question_id: str = "Q1",
    points: float = 10.0,
    points_awarded: float | None = None,
    selected_rubric_labels: tuple[str, ...] = (),
    rubric_options: list[RubricOptionSeed] | None = None,
) -> QuestionSeed:
    return QuestionSeed(
        question_id=question_id,
        image_path="image.png",
        rubric_options=rubric_options
        or [
            RubricOptionSeed("Correct", 0.0, "correct", "exclusive"),
            RubricOptionSeed("Partial", -4.0, "partial", "checklist"),
            RubricOptionSeed("Incorrect", -10.0, "incorrect", "exclusive"),
        ],
        points=points,
        points_awarded=points_awarded,
        selected_rubric_labels=selected_rubric_labels,
    )


def test_compute_question_statistics_for_saved_scores() -> None:
    stats = compute_question_statistics(
        "Q1",
        [
            _seed(points_awarded=10.0),
            _seed(points_awarded=8.0),
            _seed(points_awarded=6.0),
            _seed(points_awarded=0.0),
            _seed(),
        ],
        total_submissions=5,
    )

    assert stats.graded_count == 4
    assert stats.ungraded_count == 1
    assert stats.average == 6.0
    assert stats.median == 7.0
    assert stats.std_dev == pytest.approx(3.7416573868)
    assert stats.minimum == 0.0
    assert stats.maximum == 10.0
    assert stats.q1 == 3.0
    assert stats.q3 == 9.0
    assert stats.average_percent == 60.0
    assert stats.full_credit_count == 1
    assert stats.zero_score_count == 1
    assert [(bucket.label, bucket.count) for bucket in stats.buckets] == [
        ("0-20%", 1),
        ("20-40%", 0),
        ("40-60%", 0),
        ("60-80%", 1),
        ("80-100%", 1),
        ("100%+", 1),
    ]


def test_score_from_seed_prefers_numeric_points_awarded() -> None:
    assert seed_is_graded(_seed(points_awarded=8.0))
    assert seed_is_graded(_seed(points_awarded=0.0))
    assert not seed_is_graded(_seed())
    assert score_from_seed(
        _seed(points_awarded=8.0, selected_rubric_labels=("Incorrect",))
    ) == 8.0


def test_score_from_seed_computes_resolvable_rubric_selection() -> None:
    assert score_from_seed(_seed(selected_rubric_labels=("Partial",))) == 6.0


def test_score_from_seed_excludes_unresolved_rubric_selection() -> None:
    assert score_from_seed(_seed(selected_rubric_labels=("Missing",))) is None
    assert score_from_seed(_seed()) is None


def test_no_graded_scores_has_empty_numeric_stats() -> None:
    stats = compute_question_statistics("Q1", [_seed(), _seed()], total_submissions=2)

    assert stats.graded_count == 0
    assert stats.ungraded_count == 2
    assert stats.average is None
    assert stats.median is None
    assert stats.std_dev is None
    assert all(bucket.count == 0 for bucket in stats.buckets)
    assert render_question_statistics(stats) == "Stats\nNo graded scores yet."


def test_single_score_sets_zero_std_dev_and_matching_quartiles() -> None:
    stats = compute_question_statistics(
        "Q1",
        [_seed(points_awarded=7.0)],
        total_submissions=1,
    )

    assert stats.average == 7.0
    assert stats.median == 7.0
    assert stats.std_dev == 0.0
    assert stats.q1 == 7.0
    assert stats.q3 == 7.0


def test_non_positive_possible_points_use_exact_score_buckets() -> None:
    stats = compute_question_statistics(
        "Q1",
        [
            _seed(points=0.0, points_awarded=2.0),
            _seed(points=0.0, points_awarded=2.0),
            _seed(points=0.0, points_awarded=-1.0),
        ],
        total_submissions=3,
    )

    assert stats.average_percent is None
    assert stats.full_credit_count == 0
    assert [(bucket.label, bucket.count) for bucket in stats.buckets] == [
        ("-1.0", 1),
        ("2.0", 2),
    ]


def test_render_question_statistics_can_hide_distribution_for_image_layout() -> None:
    stats = compute_question_statistics(
        "Q1",
        [_seed(points_awarded=10.0), _seed(points_awarded=5.0)],
        total_submissions=2,
    )

    rendered = render_question_statistics(stats, include_distribution=False)

    assert "Avg: 7.5 / 10.0 (75.0%)" in rendered
    assert "Median: 7.5   Std dev: 2.5" in rendered
    assert "Distribution" not in rendered
    assert "40-60%" not in rendered


def test_render_question_statistics_includes_text_bars_by_default() -> None:
    stats = compute_question_statistics(
        "Q1",
        [_seed(points_awarded=10.0), _seed(points_awarded=5.0)],
        total_submissions=2,
    )

    rendered = render_question_statistics(stats, bar_width=4)

    assert "Avg: 7.5 / 10.0 (75.0%)" in rendered
    assert "Median: 7.5   Std dev: 2.5" in rendered
    assert "Distribution" in rendered
    assert "40-60%   | #### 1" in rendered
    assert "100%+    | #### 1" in rendered


def test_question_statistics_chart_image_renders_nonblank_image() -> None:
    stats = compute_question_statistics(
        "Q1",
        [_seed(points_awarded=10.0), _seed(points_awarded=5.0)],
        total_submissions=2,
    )

    image = question_statistics_chart_image(stats, width=320, height=180)

    assert image is not None
    assert image.size == (320, 180)
    colors = image.getcolors(maxcolors=100_000)
    assert colors is not None
    assert len(colors) > 1
