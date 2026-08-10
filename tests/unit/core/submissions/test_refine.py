"""Confidence-driven selective re-extraction: what gets re-read, and what sticks.

The engine is pure, so every rule is tested against hand-built documents with no
PDF, no Tesseract and no scratch directory in sight.
"""

from pathlib import Path

from opengrader.core.question_types import FREE_RESPONSE
from opengrader.core.submissions.refine import (
    DEFAULT_REFINE_FLOOR,
    RefineRejection,
    RefineSummary,
    RefineTarget,
    is_refine_target,
    merge_refined_responses,
    refine_candidate,
    select_refine_targets,
)
from opengrader.core.submissions.review import DEFAULT_OCR_CONFIDENCE_FLOOR
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)


def _question(
    question_id: str,
    *,
    text: str,
    confidence: float | None,
    points: float | None = None,
) -> StudentSubmissionQuestion:
    return StudentSubmissionQuestion(
        question_id=question_id,
        prompt="",
        possible_points=10,
        response_image_path=f"responses/{question_id}.png",
        response_text=text,
        points_awarded=points,
        rubric_labels=(),
        question_type=FREE_RESPONSE,
        response_confidence=confidence,
    )


def _doc(*questions: StudentSubmissionQuestion) -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name="Tommy Liu",
        student_id="123456789",
        course_code="CS146",
        assignment_title="Quiz 1",
        assignment_version="A",
        source_pdf="submission-0001.pdf",
        questions=questions,
    )


# --- Selection ------------------------------------------------------------


def test_the_floor_is_the_review_queues_floor() -> None:
    """One number, or the tool re-reads answers nobody was worried about."""
    assert DEFAULT_REFINE_FLOOR == DEFAULT_OCR_CONFIDENCE_FLOOR


def test_selects_only_measured_readings_below_the_floor() -> None:
    document = _doc(
        _question("q1", text="shaky", confidence=42.0),
        _question("q2", text="fine", confidence=88.0),
        _question("q3", text="model read", confidence=None),
        _question("q4", text="corrected by hand", confidence=100.0),
    )

    assert [target.question_id for target in select_refine_targets(document)] == ["q1"]


def test_an_unverified_model_reading_is_never_a_target() -> None:
    """`None` means unmeasured, not bad — the convention `review.py` also keeps."""
    question = _question("q1", text="a vision-model transcription", confidence=None)

    assert not is_refine_target(question, floor=DEFAULT_REFINE_FLOOR)


def test_a_human_correction_is_never_a_target() -> None:
    question = _question("q1", text="typed by the grader", confidence=100.0)

    assert not is_refine_target(question, floor=DEFAULT_REFINE_FLOOR)


def test_the_floor_is_exclusive_at_its_own_value() -> None:
    assert not is_refine_target(_question("q1", text="x", confidence=60.0), floor=60.0)
    assert is_refine_target(_question("q1", text="x", confidence=59.9), floor=60.0)


def test_candidate_is_none_when_nothing_needs_re_reading() -> None:
    document = _doc(_question("q1", text="fine", confidence=95.0))

    assert refine_candidate("submission-0001", Path("x.md"), document) is None


def test_candidate_carries_the_targets_and_identity() -> None:
    document = _doc(
        _question("q1", text="shaky", confidence=30.0),
        _question("q2", text="also shaky", confidence=12.0),
        _question("q3", text="fine", confidence=90.0),
    )

    candidate = refine_candidate("submission-0001", Path("a/b.md"), document)

    assert candidate is not None
    assert candidate.key == "submission-0001"
    assert candidate.target_ids == ("q1", "q2")
    assert candidate.identity == "Tommy Liu (123456789)"


# --- The merge ------------------------------------------------------------


def _targets(*pairs: tuple[str, str, float]) -> tuple[RefineTarget, ...]:
    return tuple(
        RefineTarget(question_id=qid, response_text=text, confidence=confidence)
        for qid, text, confidence in pairs
    )


def test_adopts_a_more_confident_reading() -> None:
    existing = _doc(_question("q1", text="Ie as \\ Ww", confidence=21.0))
    refreshed = _doc(_question("q1", text="(Ad WA) LA Wr", confidence=75.0))

    result = merge_refined_responses(
        existing, refreshed, _targets(("q1", "Ie as \\ Ww", 21.0))
    )

    assert result.changed
    (adopted,) = result.adopted
    assert adopted.new_text == "(Ad WA) LA Wr"
    assert adopted.new_confidence == 75.0
    assert adopted.confidence_summary == "21 → 75"


def test_refuses_a_less_confident_reading() -> None:
    existing = _doc(_question("q1", text="original", confidence=45.0))
    refreshed = _doc(_question("q1", text="worse", confidence=30.0))

    result = merge_refined_responses(existing, refreshed, _targets(("q1", "original", 45.0)))

    assert not result.changed
    assert result.rejected == (("q1", RefineRejection.NOT_BETTER),)


def test_refuses_an_equally_confident_reading() -> None:
    existing = _doc(_question("q1", text="original", confidence=45.0))
    refreshed = _doc(_question("q1", text="different words", confidence=45.0))

    result = merge_refined_responses(existing, refreshed, _targets(("q1", "original", 45.0)))

    assert result.rejected == (("q1", RefineRejection.NOT_BETTER),)


def test_refuses_an_empty_reading() -> None:
    """A second attempt that reads nothing means the crop is hard, not blank."""
    existing = _doc(_question("q1", text="a partial reading", confidence=20.0))
    refreshed = _doc(_question("q1", text="   ", confidence=100.0))

    result = merge_refined_responses(
        existing, refreshed, _targets(("q1", "a partial reading", 20.0))
    )

    assert result.rejected == (("q1", RefineRejection.EMPTY),)


def test_refuses_an_unverified_model_reading_by_default() -> None:
    """Trading a known-42% reading for an unknown one launders the review flag."""
    existing = _doc(_question("q1", text="original", confidence=42.0))
    refreshed = _doc(_question("q1", text="a model's words", confidence=None))

    result = merge_refined_responses(existing, refreshed, _targets(("q1", "original", 42.0)))

    assert result.rejected == (("q1", RefineRejection.UNVERIFIED),)


def test_accepts_an_unverified_model_reading_when_the_pass_escalated() -> None:
    existing = _doc(_question("q1", text="original", confidence=42.0))
    refreshed = _doc(_question("q1", text="a model's words", confidence=None))

    result = merge_refined_responses(
        existing,
        refreshed,
        _targets(("q1", "original", 42.0)),
        accept_unverified=True,
    )

    (adopted,) = result.adopted
    assert adopted.new_text == "a model's words"
    # Recorded as unmeasured rather than inheriting the old number.
    assert adopted.new_confidence is None
    assert adopted.confidence_summary == "42 → model read (unverified)"


def test_identical_text_is_not_rewritten() -> None:
    existing = _doc(_question("q1", text="same words", confidence=40.0))
    refreshed = _doc(_question("q1", text="same words", confidence=90.0))

    result = merge_refined_responses(
        existing, refreshed, _targets(("q1", "same words", 40.0))
    )

    assert not result.changed
    assert result.rejected == (("q1", RefineRejection.UNCHANGED),)


def test_only_targeted_questions_are_ever_considered() -> None:
    """A refine may not rewrite an answer it was not launched to fix."""
    existing = _doc(
        _question("q1", text="shaky", confidence=20.0),
        _question("q2", text="already good", confidence=95.0),
    )
    refreshed = _doc(
        _question("q1", text="better", confidence=80.0),
        _question("q2", text="A DIFFERENT READING", confidence=99.0),
    )

    result = merge_refined_responses(existing, refreshed, _targets(("q1", "shaky", 20.0)))

    assert [adopted.question_id for adopted in result.adopted] == ["q1"]
    assert [question_id for question_id, _ in result.rejected] == []


def test_a_grader_correcting_the_answer_mid_run_wins() -> None:
    """The plan is stale; the file is not. The file wins.

    A grader who fixed the transcription by hand while the re-extraction was in
    flight has moved the stored confidence off the value the target recorded, so
    the merge refuses rather than overwriting their typing with a machine's
    second guess.
    """
    existing = _doc(_question("q1", text="human typed this", confidence=100.0))
    refreshed = _doc(_question("q1", text="machine guess", confidence=70.0))

    result = merge_refined_responses(
        existing, refreshed, _targets(("q1", "the old shaky reading", 20.0))
    )

    assert not result.changed
    assert result.rejected == (("q1", RefineRejection.NOT_BETTER),)


def test_a_question_missing_from_the_re_extraction_is_reported() -> None:
    existing = _doc(_question("q1", text="shaky", confidence=20.0))
    refreshed = _doc(_question("q2", text="unrelated", confidence=90.0))

    result = merge_refined_responses(existing, refreshed, _targets(("q1", "shaky", 20.0)))

    assert result.rejected == (("q1", RefineRejection.MISSING),)


# --- Reporting ------------------------------------------------------------


def test_summary_counts_what_a_second_attempt_could_not_fix() -> None:
    summary = RefineSummary(
        submissions_scanned=12,
        submissions_with_targets=4,
        targets=9,
        adopted=6,
        submissions_changed=3,
    )

    assert summary.unchanged == 3
