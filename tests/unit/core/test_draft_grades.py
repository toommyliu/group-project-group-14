"""Tests for draft_grades module."""

import json
from collections.abc import Callable

import pytest

from opengrader.core.grading.draft_grades import (
    DraftGradeResult,
    draft_grades_for_groups,
)
from opengrader.core.grading.llm_grader import RubricChoice
from opengrader.core.llm import LLMError
from opengrader.core.question_types import FREE_RESPONSE
from opengrader.core.submissions.answer_groups import AnswerGroup


# Test helpers
def _make_rubric() -> list[RubricChoice]:
    """Create a standard rubric with correct, partial, and incorrect options."""
    return [
        RubricChoice(label="Correct", points_delta=0.0, kind="correct", selection_mode="exclusive"),
        RubricChoice(
            label="Partially Correct - Missing base case",
            points_delta=-2.0,
            kind="partial",
            selection_mode="checklist",
        ),
        RubricChoice(label="Incorrect", points_delta=-5.0, kind="incorrect", selection_mode="exclusive"),
    ]


def _make_group(
    key: str, member_ids: tuple[str, ...], response_text: str = "sample response"
) -> AnswerGroup:
    """Create an AnswerGroup for testing."""
    return AnswerGroup(
        key=key,
        member_ids=member_ids,
        sample_response_text=response_text,
        question_type=FREE_RESPONSE,
    )


class FakeGenerate:
    """A fake generator that records calls and returns configurable responses."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._response_queue: list[str] = []
        self._error_queue: list[type[Exception]] = []
        self._default_response: str = json.dumps(
            {"rubric_labels": ["Correct"], "confidence": 0.9, "rationale": "ok"}
        )

    def enqueue_response(self, response: str) -> None:
        """Queue a response to be returned on the next call."""
        self._response_queue.append(response)

    def enqueue_error(self, error: type[Exception]) -> None:
        """Queue an error to be raised on the next call."""
        self._error_queue.append(error)

    def set_default_response(self, response: str) -> None:
        """Set the default response for calls without queued responses."""
        self._default_response = response

    def __call__(self, prompt: str) -> str:
        """Record the prompt and return a response."""
        self.calls.append(prompt)
        if self._error_queue:
            error_cls = self._error_queue.pop(0)
            raise error_cls("Test error")
        if self._response_queue:
            return self._response_queue.pop(0)
        return self._default_response


# Tests
def test_one_call_per_group() -> None:
    """One model call per group, not per student."""
    generate = FakeGenerate()
    groups = [
        _make_group("a", ("s1", "s2", "s3")),
        _make_group("b", ("s4",)),
    ]
    result = draft_grades_for_groups(
        groups,
        prompt="test",
        expected_answers=[],
        rubric_options=_make_rubric(),
        possible_points=5.0,
        generate=generate,
        min_confidence=0.5,
    )

    # Only 2 calls (one per group), not 4 (one per student)
    assert len(generate.calls) == 2
    # All members from first group are in applied
    assert len(result.applied) == 2
    assert result.applied[0].member_ids == ("s1", "s2", "s3")
    assert result.applied[1].member_ids == ("s4",)


def test_blank_groups_not_sent() -> None:
    """Blank groups (key='') are never sent to generate."""
    generate = FakeGenerate()
    groups = [
        _make_group("a", ("s1",), "answer"),
        _make_group("", ("s2",), ""),  # blank group
    ]
    result = draft_grades_for_groups(
        groups,
        prompt="test",
        expected_answers=[],
        rubric_options=_make_rubric(),
        possible_points=5.0,
        generate=generate,
        min_confidence=0.5,
    )

    # Only 1 call (blank group skipped)
    assert len(generate.calls) == 1
    assert len(result.applied) == 1


def test_empty_text_groups_not_sent() -> None:
    """Groups with only whitespace text are never sent to generate."""
    generate = FakeGenerate()
    groups = [
        _make_group("a", ("s1",), "answer"),
        _make_group("b", ("s2",), "   \n\t  "),  # empty text
    ]
    result = draft_grades_for_groups(
        groups,
        prompt="test",
        expected_answers=[],
        rubric_options=_make_rubric(),
        possible_points=5.0,
        generate=generate,
        min_confidence=0.5,
    )

    # Only 1 call (empty text group skipped)
    assert len(generate.calls) == 1
    assert len(result.applied) == 1


def test_eligible_member_ids_filters() -> None:
    """Only eligible members are included in the result."""
    generate = FakeGenerate()
    groups = [
        _make_group("a", ("s1", "s2", "s3")),
    ]
    result = draft_grades_for_groups(
        groups,
        prompt="test",
        expected_answers=[],
        rubric_options=_make_rubric(),
        possible_points=5.0,
        generate=generate,
        min_confidence=0.5,
        eligible_member_ids={"s1", "s3"},
    )

    # One call still (group is not skipped)
    assert len(generate.calls) == 1
    # But only eligible members in the result
    assert result.applied[0].member_ids == ("s1", "s3")


def test_group_with_no_eligible_members_not_sent() -> None:
    """Groups with no eligible members are not sent."""
    generate = FakeGenerate()
    groups = [
        _make_group("a", ("s1", "s2")),
        _make_group("b", ("s3", "s4")),
    ]
    result = draft_grades_for_groups(
        groups,
        prompt="test",
        expected_answers=[],
        rubric_options=_make_rubric(),
        possible_points=5.0,
        generate=generate,
        min_confidence=0.5,
        eligible_member_ids={"s1"},
    )

    # Only 1 call (second group has no eligible members)
    assert len(generate.calls) == 1
    # Only first group in result
    assert len(result.applied) == 1
    assert result.applied[0].member_ids == ("s1",)


def test_confidence_threshold_applied() -> None:
    """Grades with confidence >= min_confidence go to applied."""
    generate = FakeGenerate()
    generate.enqueue_response(
        json.dumps({"rubric_labels": ["Correct"], "confidence": 0.5, "rationale": "ok"})
    )
    generate.enqueue_response(
        json.dumps({"rubric_labels": ["Correct"], "confidence": 0.6, "rationale": "ok"})
    )

    groups = [
        _make_group("a", ("s1",)),
        _make_group("b", ("s2",)),
    ]
    result = draft_grades_for_groups(
        groups,
        prompt="test",
        expected_answers=[],
        rubric_options=_make_rubric(),
        possible_points=5.0,
        generate=generate,
        min_confidence=0.5,  # threshold exactly at 0.5
    )

    # First group at threshold (0.5 >= 0.5) should be applied
    # Second group above threshold (0.6 >= 0.5) should be applied
    assert len(result.applied) == 2
    assert len(result.low_confidence) == 0


def test_confidence_threshold_boundary() -> None:
    """Test exact boundary: confidence == min_confidence goes to applied."""
    generate = FakeGenerate()
    generate.enqueue_response(
        json.dumps({"rubric_labels": ["Correct"], "confidence": 0.5, "rationale": "ok"})
    )
    generate.enqueue_response(
        json.dumps({"rubric_labels": ["Correct"], "confidence": 0.49, "rationale": "ok"})
    )

    groups = [
        _make_group("a", ("s1",)),
        _make_group("b", ("s2",)),
    ]
    result = draft_grades_for_groups(
        groups,
        prompt="test",
        expected_answers=[],
        rubric_options=_make_rubric(),
        possible_points=5.0,
        generate=generate,
        min_confidence=0.5,
    )

    # First at boundary (0.5 == 0.5) → applied
    # Second below boundary (0.49 < 0.5) → low_confidence
    assert len(result.applied) == 1
    assert len(result.low_confidence) == 1
    assert result.applied[0].member_ids == ("s1",)
    assert result.low_confidence[0].member_ids == ("s2",)


def test_invalid_json_response_fails_group() -> None:
    """Invalid JSON reply increments failed_groups and doesn't apply anything."""
    generate = FakeGenerate()
    generate.enqueue_response("not valid json at all {]}")

    groups = [_make_group("a", ("s1",))]
    result = draft_grades_for_groups(
        groups,
        prompt="test",
        expected_answers=[],
        rubric_options=_make_rubric(),
        possible_points=5.0,
        generate=generate,
        min_confidence=0.5,
    )

    assert result.failed_groups == 1
    assert len(result.applied) == 0
    assert len(result.low_confidence) == 0


def test_llm_error_fails_group_and_continues() -> None:
    """LLMError increments failed_groups and continues processing."""
    generate = FakeGenerate()
    generate.enqueue_error(LLMError)
    generate.enqueue_response(
        json.dumps({"rubric_labels": ["Correct"], "confidence": 0.9, "rationale": "ok"})
    )

    groups = [
        _make_group("a", ("s1",)),
        _make_group("b", ("s2",)),
    ]
    result = draft_grades_for_groups(
        groups,
        prompt="test",
        expected_answers=[],
        rubric_options=_make_rubric(),
        possible_points=5.0,
        generate=generate,
        min_confidence=0.5,
    )

    # First group failed, second processed
    assert result.failed_groups == 1
    assert len(result.applied) == 1
    assert result.applied[0].member_ids == ("s2",)


def test_empty_rubric_generates_nothing() -> None:
    """Empty rubric returns empty result without calling generate."""
    generate = FakeGenerate()
    groups = [_make_group("a", ("s1",))]

    result = draft_grades_for_groups(
        groups,
        prompt="test",
        expected_answers=[],
        rubric_options=[],  # empty rubric
        possible_points=5.0,
        generate=generate,
        min_confidence=0.5,
    )

    # Generate should never be called
    assert len(generate.calls) == 0
    # Result should be empty (but not failed)
    assert len(result.applied) == 0
    assert len(result.low_confidence) == 0
    assert result.failed_groups == 0


def test_on_progress_callback() -> None:
    """on_progress receives (done, total) before each call."""
    generate = FakeGenerate()
    groups = [
        _make_group("a", ("s1",)),
        _make_group("b", ("s2",)),
        _make_group("c", ("s3",)),
    ]

    progress_calls: list[tuple[int, int]] = []

    def on_progress(done: int, total: int) -> None:
        progress_calls.append((done, total))

    result = draft_grades_for_groups(
        groups,
        prompt="test",
        expected_answers=[],
        rubric_options=_make_rubric(),
        possible_points=5.0,
        generate=generate,
        min_confidence=0.5,
        on_progress=on_progress,
    )

    # Should be called 3 times: (0, 3), (1, 3), (2, 3)
    assert progress_calls == [(0, 3), (1, 3), (2, 3)]
    assert len(result.applied) == 3


def test_points_calculation_with_partial() -> None:
    """Selecting a partial option gives correct points."""
    generate = FakeGenerate()
    generate.enqueue_response(
        json.dumps(
            {
                "rubric_labels": ["Partially Correct - Missing base case"],
                "confidence": 0.9,
                "rationale": "ok",
            }
        )
    )

    groups = [_make_group("a", ("s1",))]
    result = draft_grades_for_groups(
        groups,
        prompt="test",
        expected_answers=[],
        rubric_options=_make_rubric(),
        possible_points=5.0,
        generate=generate,
        min_confidence=0.5,
    )

    # Partial deduction is -2.0, so 5.0 - 2.0 = 3.0
    assert len(result.applied) == 1
    assert result.applied[0].suggestion.points_awarded == 3.0


def test_applied_student_count_property() -> None:
    """applied_student_count sums member counts in applied."""
    generate = FakeGenerate()
    groups = [
        _make_group("a", ("s1", "s2")),
        _make_group("b", ("s3", "s4", "s5")),
    ]
    result = draft_grades_for_groups(
        groups,
        prompt="test",
        expected_answers=[],
        rubric_options=_make_rubric(),
        possible_points=5.0,
        generate=generate,
        min_confidence=0.5,
    )

    assert result.applied_student_count == 5


def test_low_confidence_student_count_property() -> None:
    """low_confidence_student_count sums member counts in low_confidence."""
    generate = FakeGenerate()
    generate.enqueue_response(
        json.dumps({"rubric_labels": ["Correct"], "confidence": 0.3, "rationale": "ok"})
    )
    generate.enqueue_response(
        json.dumps({"rubric_labels": ["Correct"], "confidence": 0.2, "rationale": "ok"})
    )

    groups = [
        _make_group("a", ("s1", "s2")),
        _make_group("b", ("s3", "s4", "s5")),
    ]
    result = draft_grades_for_groups(
        groups,
        prompt="test",
        expected_answers=[],
        rubric_options=_make_rubric(),
        possible_points=5.0,
        generate=generate,
        min_confidence=0.5,
    )

    assert result.low_confidence_student_count == 5


def test_on_progress_skips_blank_and_empty_groups() -> None:
    """on_progress total doesn't include blank/empty/ineligible groups."""
    generate = FakeGenerate()
    groups = [
        _make_group("a", ("s1",), "answer"),  # will be sent
        _make_group("", ("s2",), ""),  # blank, won't be sent
        _make_group("b", ("s3",), "   "),  # empty, won't be sent
        _make_group("c", ("s4",), "answer"),  # will be sent
    ]

    progress_calls: list[tuple[int, int]] = []

    def on_progress(done: int, total: int) -> None:
        progress_calls.append((done, total))

    result = draft_grades_for_groups(
        groups,
        prompt="test",
        expected_answers=[],
        rubric_options=_make_rubric(),
        possible_points=5.0,
        generate=generate,
        min_confidence=0.5,
        on_progress=on_progress,
    )

    # Total should be 2 (only non-blank, non-empty groups)
    assert progress_calls == [(0, 2), (1, 2)]


def test_preserves_group_order() -> None:
    """Results preserve input group order."""
    generate = FakeGenerate()
    groups = [
        _make_group("first", ("s1",)),
        _make_group("second", ("s2",)),
        _make_group("third", ("s3",)),
    ]
    result = draft_grades_for_groups(
        groups,
        prompt="test",
        expected_answers=[],
        rubric_options=_make_rubric(),
        possible_points=5.0,
        generate=generate,
        min_confidence=0.5,
    )

    assert len(result.applied) == 3
    assert result.applied[0].member_ids == ("s1",)
    assert result.applied[1].member_ids == ("s2",)
    assert result.applied[2].member_ids == ("s3",)


def test_mixed_applied_and_low_confidence() -> None:
    """Result can contain both applied and low_confidence grades."""
    generate = FakeGenerate()
    generate.enqueue_response(
        json.dumps({"rubric_labels": ["Correct"], "confidence": 0.9, "rationale": "ok"})
    )
    generate.enqueue_response(
        json.dumps({"rubric_labels": ["Correct"], "confidence": 0.3, "rationale": "ok"})
    )
    generate.enqueue_response(
        json.dumps({"rubric_labels": ["Correct"], "confidence": 0.7, "rationale": "ok"})
    )

    groups = [
        _make_group("a", ("s1",)),
        _make_group("b", ("s2",)),
        _make_group("c", ("s3",)),
    ]
    result = draft_grades_for_groups(
        groups,
        prompt="test",
        expected_answers=[],
        rubric_options=_make_rubric(),
        possible_points=5.0,
        generate=generate,
        min_confidence=0.5,
    )

    assert len(result.applied) == 2
    assert len(result.low_confidence) == 1
    assert result.applied_student_count == 2
    assert result.low_confidence_student_count == 1
