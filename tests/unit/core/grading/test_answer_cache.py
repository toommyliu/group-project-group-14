from __future__ import annotations

import pytest

from opengrader.core.grading.answer_cache import (
    AnswerGradeCache,
    AnswerGradeKey,
    answer_grade_key,
)
from opengrader.core.grading.llm_grader import GradeSuggestion
from opengrader.core.llm import OllamaError
from opengrader.core.question_types import FREE_RESPONSE, MULTIPLE_CHOICE


def _suggestion(points: float = 5.0) -> GradeSuggestion:
    return GradeSuggestion(
        rubric_labels=("Correct",),
        points_awarded=points,
        confidence=0.9,
        rationale="Looks right.",
    )


def _key(text: str, *, question_id: str = "q1", version: str = "") -> AnswerGradeKey:
    return answer_grade_key(
        question_id=question_id,
        assignment_version=version,
        response_text=text,
        question_type=FREE_RESPONSE,
    )


def test_cosmetically_different_answers_share_a_key() -> None:
    assert _key("Because it is stable.") == _key("  because it is STABLE  ")


def test_different_answers_do_not_share_a_key() -> None:
    assert _key("is stable") != _key("is not stable")


def test_versions_do_not_share_a_key() -> None:
    assert _key("42", version="A") != _key("42", version="B")


def test_questions_do_not_share_a_key() -> None:
    assert _key("yes", question_id="q1") != _key("yes", question_id="q2")


def test_multiple_choice_keys_normalize_labels() -> None:
    a = answer_grade_key(
        question_id="q1",
        assignment_version="",
        response_text="b)",
        question_type=MULTIPLE_CHOICE,
    )
    b = answer_grade_key(
        question_id="q1",
        assignment_version="",
        response_text="B.",
        question_type=MULTIPLE_CHOICE,
    )
    assert a == b


def test_identical_answers_cost_one_model_call() -> None:
    cache = AnswerGradeCache()
    calls = 0

    def factory() -> GradeSuggestion:
        nonlocal calls
        calls += 1
        return _suggestion()

    for _ in range(50):
        cache.suggest(_key("the same answer"), factory)

    assert calls == 1
    assert cache.model_calls == 1
    assert cache.reused == 49
    assert cache.answers_decided == 50


def test_identical_answers_get_the_identical_decision() -> None:
    cache = AnswerGradeCache()
    results = iter([_suggestion(5.0), _suggestion(0.0)])
    first = cache.suggest(_key("same"), lambda: next(results))
    second = cache.suggest(_key("same"), lambda: next(results))
    assert first is second
    assert second.points_awarded == 5.0


def test_distinct_answers_each_cost_a_call() -> None:
    cache = AnswerGradeCache()
    cache.suggest(_key("one"), lambda: _suggestion())
    cache.suggest(_key("two"), lambda: _suggestion())
    assert cache.model_calls == 2
    assert cache.reused == 0


def test_none_verdict_is_cached() -> None:
    cache = AnswerGradeCache()
    calls = 0

    def factory() -> GradeSuggestion | None:
        nonlocal calls
        calls += 1
        return None

    assert cache.suggest(_key("x"), factory) is None
    assert cache.suggest(_key("x"), factory) is None
    assert calls == 1


def test_transport_errors_are_not_cached() -> None:
    cache = AnswerGradeCache()
    attempts = 0

    def factory() -> GradeSuggestion:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OllamaError("connection refused")
        return _suggestion()

    with pytest.raises(OllamaError):
        cache.suggest(_key("x"), factory)
    assert cache.suggest(_key("x"), factory) == _suggestion()
    assert attempts == 2
    assert cache.model_calls == 1


def test_summary_is_silent_when_nothing_was_reused() -> None:
    cache = AnswerGradeCache()
    cache.suggest(_key("only one"), lambda: _suggestion())
    assert cache.summary_line() == ""


def test_summary_reports_what_grouping_bought() -> None:
    cache = AnswerGradeCache()
    for _ in range(4):
        cache.suggest(_key("shared"), lambda: _suggestion())
    line = cache.summary_line()
    assert "4 answers" in line
    assert "1 model call" in line
    assert "3 shared" in line
