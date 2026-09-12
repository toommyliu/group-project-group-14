from __future__ import annotations

import json
from dataclasses import replace

from opengrader.core.grading.evaluation import (
    EvalCase,
    EvalTarget,
    SkipTally,
    bucket_by_confidence,
    build_llm_eval_report,
    evaluate_target,
    group_by_question_type,
    recommend_threshold,
    select_eval_targets,
    spread_targets,
    summarize,
    threshold_curve,
)
from opengrader.core.grading.llm_grader import RubricChoice
from opengrader.core.grading.solutions import solution_entries_for_submission
from opengrader.core.qa_file import parse_qa_markdown
from opengrader.core.submissions import parse_student_submission_markdown


RUBRIC = (
    RubricChoice("Correct", 0.0, "correct", "exclusive"),
    RubricChoice("Partial", -2.0, "partial", "exclusive"),
    RubricChoice("Incorrect", -4.0, "incorrect", "exclusive"),
)


def _target(
    *,
    student_id: str = "s1",
    question_id: str = "q1",
    question_type: str = "free_response",
    human_points: float = 4.0,
    human_rubric: tuple[str, ...] = ("Correct",),
    possible_points: float = 4.0,
) -> EvalTarget:
    return EvalTarget(
        student_id=student_id,
        student_name="Tommy Liu",
        question_id=question_id,
        question_type=question_type,
        prompt="Explain the difference between a stack and a queue.",
        expected_answers=("LIFO vs FIFO",),
        response_text="One is last in first out, the other first in first out.",
        rubric_options=RUBRIC,
        possible_points=possible_points,
        human_points=human_points,
        human_rubric=human_rubric,
    )


def _case(
    *,
    model: str = "llama3.1",
    human_points: float = 4.0,
    llm_points: float | None = 4.0,
    confidence: float | None = 0.9,
    question_type: str = "free_response",
    student_id: str = "s1",
    question_id: str = "q1",
    llm_rubric: tuple[str, ...] = ("Correct",),
    human_rubric: tuple[str, ...] = ("Correct",),
) -> EvalCase:
    return EvalCase(
        model=model,
        target=_target(
            student_id=student_id,
            question_id=question_id,
            question_type=question_type,
            human_points=human_points,
            human_rubric=human_rubric,
        ),
        llm_points=llm_points,
        llm_rubric=llm_rubric,
        llm_confidence=confidence,
        rationale="Names both orderings.",
    )


def _model_saying(labels: list[str], confidence: float):  # noqa: ANN202
    payload = json.dumps(
        {"rubric_labels": labels, "confidence": confidence, "rationale": "because"}
    )
    return lambda _prompt: payload


QA = """---
title: Quiz
---

- (4 points) Explain the difference between a stack and a queue.
  - **Correct**
    - LIFO vs FIFO
- (2 points) What is 2 + 2?
  - **Correct**
    - 4
- (1 points) Which is a tree?
  - **Choices**
    - A) Heap
    - B) Queue
  - **Correct**
    - A
"""


def _submission(blocks: str) -> str:
    return (
        "---\n"
        'name: "Tommy Liu"\n'
        'student_id: "s1"\n'
        "course_code: CS146\n"
        'assignment_title: "Quiz"\n'
        "assignment_version: null\n"
        'source_pdf: "s1.pdf"\n'
        'score: ""\n'
        "---\n\n\n" + blocks
    )


def _question_block(
    qid: str,
    *,
    question_type: str,
    response: str,
    points: str = "",
    analysis: str = "",
    possible: str = "4",
    confidence: str | None = None,
) -> str:
    lines = [
        f"- {qid}:",
        f"  - Question type: {question_type}",
        "  - Prompt: p",
        f"  - Response text: {response}",
    ]
    if confidence is not None:
        lines.append(f"  - Confidence: {confidence}")
    lines.extend(
        [
            f"  - Possible points: {possible}",
            f"  - Points: {points}",
            "  - Rubric:",
            f"  - Analysis: {analysis}",
        ]
    )
    return "\n".join(lines) + "\n\n"


def _targets_for(blocks: str, *, class_label: str = ""):  # noqa: ANN202
    document = parse_student_submission_markdown(_submission(blocks))
    solutions = solution_entries_for_submission(parse_qa_markdown(QA), document)
    return select_eval_targets(document, solutions, class_label=class_label)


def _targets_for_with_floor(blocks: str, *, floor: float):  # noqa: ANN202
    document = parse_student_submission_markdown(_submission(blocks))
    solutions = solution_entries_for_submission(parse_qa_markdown(QA), document)
    return select_eval_targets(
        document, solutions, min_transcription_confidence=floor
    )


def test_weak_transcription_is_excluded_and_counted_separately() -> None:
    targets, tally = _targets_for(
        _question_block(
            "q1",
            question_type="Free Response",
            response="a st4ck smudge",
            points="3",
            confidence="30",
        )
    )
    assert targets == []
    assert tally.low_confidence == 1
    assert tally.blank == 0
    assert tally.total == 1


def test_weak_transcription_is_evaluated_when_the_floor_is_disabled() -> None:
    targets, tally = _targets_for_with_floor(
        _question_block(
            "q1",
            question_type="Free Response",
            response="a st4ck smudge",
            points="3",
            confidence="30",
        ),
        floor=0,
    )
    assert len(targets) == 1
    assert tally.low_confidence == 0


def test_strong_transcription_is_evaluated() -> None:
    targets, tally = _targets_for(
        _question_block(
            "q1",
            question_type="Free Response",
            response="a stack",
            points="3",
            confidence="92",
        )
    )
    assert len(targets) == 1
    assert tally.low_confidence == 0


def test_a_human_graded_free_response_is_a_case() -> None:
    targets, tally = _targets_for(
        _question_block("q1", question_type="Free Response", response="a stack", points="3")
    )
    assert len(targets) == 1
    assert targets[0].human_points == 3.0
    assert targets[0].possible_points == 4.0
    assert tally.total == 0


def test_objective_answers_are_excluded_because_a_model_never_sees_them() -> None:
    targets, tally = _targets_for(
        _question_block(
            "q3", question_type="Multiple Choice", response="A", points="1", possible="1"
        )
    )
    assert targets == []
    assert tally.objective == 1


def test_a_numeric_miss_is_excluded_the_same_way_production_excludes_it() -> None:
    targets, tally = _targets_for(
        _question_block(
            "q2", question_type="Free Response", response="5", points="0", possible="2"
        )
    )
    assert targets == []
    assert tally.numeric == 1


def test_an_ungraded_answer_has_no_ground_truth_and_is_reported_as_such() -> None:
    targets, tally = _targets_for(
        _question_block("q1", question_type="Free Response", response="a stack")
    )
    assert targets == []
    assert tally.ungraded == 1


def test_an_already_ai_graded_answer_is_excluded_as_circular() -> None:
    targets, tally = _targets_for(
        _question_block(
            "q1",
            question_type="Free Response",
            response="a stack",
            points="3",
            analysis="[AI · 82%] partial credit",
        )
    )
    assert targets == []
    assert tally.ai_graded == 1


def test_a_blank_response_and_an_unknown_question_are_counted_separately() -> None:
    blocks = _question_block(
        "q1", question_type="Free Response", response="", points="0"
    ) + _question_block(
        "q9", question_type="Free Response", response="something", points="1"
    )
    targets, tally = _targets_for(blocks)
    assert targets == []
    assert tally.blank == 1
    assert tally.no_solution == 1


def test_skip_tallies_add_up() -> None:
    combined = SkipTally(objective=1, ungraded=2) + SkipTally(objective=3, blank=1)
    assert combined.objective == 4
    assert combined.ungraded == 2
    assert combined.blank == 1
    assert combined.total == 7


def test_evaluate_target_records_what_the_model_said() -> None:
    case = evaluate_target(
        _target(human_points=2.0),
        model="llama3.1",
        generate=_model_saying(["Partial"], 0.8),
    )
    assert case.llm_points == 2.0
    assert case.llm_rubric == ("Partial",)
    assert case.llm_confidence == 0.8
    assert case.outcome() == "agree"


def test_a_model_that_returns_junk_is_no_suggestion_not_a_disagreement() -> None:
    case = evaluate_target(
        _target(), model="llama3.1", generate=lambda _p: "I cannot do that."
    )
    assert case.llm_points is None
    assert case.outcome() == "no_suggestion"
    assert case.points_error is None

    stats = summarize([case])
    assert stats.cases == 1
    assert stats.scored == 0
    assert stats.no_suggestion == 1
    assert stats.agreement_rate is None


def test_outcome_direction_says_generous_or_harsh() -> None:
    assert _case(human_points=2.0, llm_points=4.0).outcome() == "generous"
    assert _case(human_points=4.0, llm_points=2.0).outcome() == "harsh"


def test_points_tolerance_widens_what_counts_as_agreement() -> None:
    case = _case(human_points=4.0, llm_points=3.5)
    assert case.outcome() == "harsh"
    assert case.outcome(tolerance=0.5) == "agree"


def test_rubric_comparison_is_none_when_the_human_recorded_no_rubric() -> None:
    assert _case(human_rubric=()).rubric_matches is None
    assert _case(llm_rubric=("Partial",)).rubric_matches is False
    assert _case().rubric_matches is True


def test_summarize_counts_direction_error_and_bias() -> None:
    stats = summarize(
        [
            _case(human_points=4.0, llm_points=4.0),
            _case(human_points=2.0, llm_points=4.0),
            _case(human_points=4.0, llm_points=3.0),
        ]
    )
    assert (stats.cases, stats.scored, stats.agreements) == (3, 3, 1)
    assert (stats.generous, stats.harsh) == (1, 1)
    assert stats.mean_absolute_error == 1.0
    assert stats.max_absolute_error == 2.0
    assert round(stats.mean_signed_error, 6) == round(1 / 3, 6)
    assert stats.bias == "balanced"


def test_bias_names_the_direction_a_model_leans() -> None:
    generous = summarize(
        [_case(human_points=1.0, llm_points=4.0), _case(human_points=2.0, llm_points=4.0)]
    )
    assert generous.bias == "generous"


def test_confidence_buckets_group_scored_cases_and_drop_empty_ones() -> None:
    buckets = bucket_by_confidence(
        [
            _case(confidence=0.95),
            _case(confidence=0.91),
            _case(confidence=0.42, human_points=1.0),
            _case(confidence=None, llm_points=None),
        ],
        bucket_size=0.1,
    )
    labels = [bucket.label for bucket in buckets]
    assert labels == ["0.40-0.50", "0.90-1.00"]
    assert buckets[1].stats.scored == 2
    assert sum(bucket.stats.cases for bucket in buckets) == 3


def test_confidence_of_exactly_one_lands_in_the_top_bucket() -> None:
    buckets = bucket_by_confidence([_case(confidence=1.0)], bucket_size=0.1)
    assert buckets[0].label == "0.90-1.00"


def test_question_type_breakdown_orders_by_sample_size() -> None:
    groups = group_by_question_type(
        [
            _case(question_type="free_response"),
            _case(question_type="free_response"),
            _case(question_type="multiple_answer"),
        ]
    )
    assert [name for name, _ in groups] == ["free_response", "multiple_answer"]
    assert groups[0][1].cases == 2


def test_threshold_curve_reports_coverage_and_agreement_at_each_cutoff() -> None:
    cases = [
        _case(confidence=0.95, human_points=4.0, llm_points=4.0),
        _case(confidence=0.85, human_points=4.0, llm_points=4.0),
        _case(confidence=0.45, human_points=4.0, llm_points=0.0),
    ]
    rows = {row.threshold: row for row in threshold_curve(cases, step=0.05)}

    at_zero = rows[0.0]
    assert at_zero.applied == 3
    assert at_zero.coverage == 1.0
    assert round(at_zero.agreement_rate, 4) == round(2 / 3, 4)
    assert at_zero.deferred == 0

    at_high = rows[0.85]
    assert at_high.applied == 2
    assert at_high.agreement_rate == 1.0
    assert at_high.deferred == 1


def test_a_no_suggestion_case_is_never_applied_at_any_threshold() -> None:
    rows = threshold_curve([_case(confidence=None, llm_points=None)], step=0.5)
    assert all(row.applied == 0 for row in rows)
    assert rows[0].deferred == 1


def test_recommendation_picks_the_loosest_cutoff_that_holds_up() -> None:
    cases = [_case(confidence=0.8 + i * 0.01) for i in range(8)] + [
        _case(confidence=0.75, human_points=4.0, llm_points=0.0) for _ in range(8)
    ]
    recommendation = recommend_threshold(
        threshold_curve(cases, step=0.05), target_agreement=0.9, min_sample=5
    )
    assert recommendation.threshold == 0.8
    assert recommendation.row is not None
    assert recommendation.row.agreement_rate == 1.0


def test_a_lucky_low_cutoff_under_a_bad_stretch_is_not_recommended() -> None:
    cases = (
        [_case(confidence=0.96, human_points=4.0, llm_points=0.0) for _ in range(5)]
        + [_case(confidence=0.55) for _ in range(15)]
    )
    recommendation = recommend_threshold(
        threshold_curve(cases, step=0.05), target_agreement=0.9, min_sample=5
    )
    assert recommendation.threshold is None
    assert "no confidence cutoff reaches" in recommendation.reason


def test_a_threshold_with_too_small_a_sample_cannot_be_recommended() -> None:
    recommendation = recommend_threshold(
        threshold_curve([_case(confidence=0.99) for _ in range(3)], step=0.05),
        target_agreement=0.9,
        min_sample=5,
    )
    assert recommendation.threshold is None
    assert "not enough evidence" in recommendation.reason


def test_a_model_that_never_reaches_the_target_is_reported_with_its_best() -> None:
    cases = [_case(confidence=0.9, human_points=4.0, llm_points=0.0) for _ in range(5)]
    cases += [_case(confidence=0.9) for _ in range(5)]
    recommendation = recommend_threshold(
        threshold_curve(cases, step=0.05), target_agreement=0.9, min_sample=5
    )
    assert recommendation.threshold is None
    assert recommendation.row is not None
    assert recommendation.row.agreement_rate == 0.5


def test_report_separates_models_and_names_the_best_by_coverage() -> None:
    good = [_case(model="good", confidence=0.6) for _ in range(10)]
    strict = [_case(model="strict", confidence=0.95) for _ in range(5)]
    strict += [
        _case(model="strict", confidence=0.4, human_points=4.0, llm_points=0.0)
        for _ in range(5)
    ]

    report = build_llm_eval_report(good + strict, min_sample=5)
    assert [evaluation.model for evaluation in report.models] == ["good", "strict"]
    assert report.best_model is not None
    assert report.best_model.model == "good"
    assert report.best_model.recommendation.row.coverage == 1.0


def test_report_on_an_empty_input_is_safe() -> None:
    report = build_llm_eval_report([], skipped=SkipTally(ungraded=4))
    assert report.models == ()
    assert report.best_model is None
    assert report.cases_per_model == 0
    assert report.skipped.ungraded == 4


def test_worst_disagreements_lead_with_the_biggest_point_gap() -> None:
    report = build_llm_eval_report(
        [
            _case(question_id="q1", human_points=4.0, llm_points=3.0),
            _case(question_id="q2", human_points=0.0, llm_points=4.0),
            _case(question_id="q3"),
        ],
        disagreement_samples=5,
    )
    worst = report.models[0].worst_disagreements
    assert [case.target.question_id for case in worst] == ["q2", "q1"]


def test_a_limit_takes_from_every_student_not_just_the_first() -> None:
    targets = [
        _target(student_id=f"s{s}", question_id=f"q{q}")
        for s in range(1, 6)
        for q in range(1, 5)
    ]
    picked = spread_targets(targets, 5)

    assert len(picked) == 5
    assert {t.student_id for t in picked} == {"s1", "s2", "s3", "s4", "s5"}


def test_a_limit_rotates_through_questions_as_it_goes_round_again() -> None:
    targets = [
        _target(student_id=f"s{s}", question_id=f"q{q}")
        for s in range(1, 4)
        for q in range(1, 4)
    ]
    picked = spread_targets(targets, 6)

    assert len(picked) == 6
    assert sorted(t.student_id for t in picked) == ["s1", "s1", "s2", "s2", "s3", "s3"]
    assert {t.question_id for t in picked} == {"q1", "q2"}


def test_a_limit_spreads_across_pooled_cohorts() -> None:
    targets = [
        replace(_target(student_id=f"s{s}"), class_label=class_label)
        for class_label in ("2026-spring", "2025-fall")
        for s in range(1, 5)
    ]
    picked = spread_targets(targets, 4)

    assert len(picked) == 4
    assert {t.class_label for t in picked} == {"2026-spring", "2025-fall"}


def test_the_same_student_id_in_two_cohorts_is_two_people() -> None:
    targets = [
        replace(_target(student_id="s1", question_id=f"q{q}"), class_label=class_label)
        for class_label in ("a", "b")
        for q in range(1, 4)
    ]
    picked = spread_targets(targets, 2)

    assert {t.class_label for t in picked} == {"a", "b"}


def test_a_limit_at_or_above_the_pool_returns_everything_in_order() -> None:
    targets = [_target(question_id=f"q{q}") for q in range(1, 4)]
    assert spread_targets(targets, 3) == targets
    assert spread_targets(targets, 99) == targets


def test_a_limit_of_zero_or_less_evaluates_nothing() -> None:
    targets = [_target(question_id="q1")]
    assert spread_targets(targets, 0) == []
    assert spread_targets(targets, -1) == []


def test_spreading_is_deterministic() -> None:
    targets = [
        _target(student_id=f"s{s}", question_id=f"q{q}")
        for s in range(1, 8)
        for q in range(1, 4)
    ]
    assert spread_targets(targets, 9) == spread_targets(targets, 9)


def test_targets_carry_the_cohort_they_came_from() -> None:
    targets, _ = _targets_for(
        _question_block("q1", question_type="Free Response", response="a", points="3"),
        class_label="2025-fall",
    )
    assert targets[0].class_label == "2025-fall"
    assert targets[0].person == ("2025-fall", "s1")


def test_a_single_cohort_run_carries_no_label() -> None:
    targets, _ = _targets_for(
        _question_block("q1", question_type="Free Response", response="a", points="3")
    )
    assert targets[0].class_label == ""


def test_a_report_knows_whether_it_pooled() -> None:
    single = build_llm_eval_report([], classes=["spring"])
    pooled = build_llm_eval_report([], classes=["spring", "fall"])
    assert single.is_pooled is False
    assert pooled.is_pooled is True
    assert pooled.classes == ("spring", "fall")
