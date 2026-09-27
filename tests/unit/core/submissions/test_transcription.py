from __future__ import annotations

import pytest

from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionGradeUpdate,
    StudentSubmissionQuestion,
    parse_student_submission_markdown,
    render_student_submission_markdown,
    update_student_submission_markdown,
)
from opengrader.core.submissions.transcription import (
    SOURCE_HUMAN,
    SOURCE_TESSERACT,
    SOURCE_VISION_MODEL,
    ReadingTally,
    describe_reading_source,
    format_reading_source,
    is_model_reading,
    parse_reading_source,
    should_escalate,
    tally_reading_sources,
)


def test_format_reading_source_appends_the_provider_in_parens():
    assert format_reading_source(SOURCE_VISION_MODEL, "openai") == "vision-model (openai)"


def test_format_reading_source_with_no_provider_is_unchanged():
    assert format_reading_source(SOURCE_VISION_MODEL, None) == SOURCE_VISION_MODEL
    assert format_reading_source(SOURCE_VISION_MODEL, "") == SOURCE_VISION_MODEL


def test_format_reading_source_with_no_source_is_blank_either_way():
    assert format_reading_source(None) == ""
    assert format_reading_source(None, "openai") == ""


def test_parse_reading_source_splits_the_suffix():
    assert parse_reading_source("vision-model (openai)") == (SOURCE_VISION_MODEL, "openai")


def test_parse_reading_source_without_a_suffix_is_an_old_file():
    assert parse_reading_source(SOURCE_TESSERACT) == (SOURCE_TESSERACT, None)
    assert parse_reading_source(None) == (None, None)
    assert parse_reading_source("") == (None, None)


def test_a_confident_reading_is_left_alone():
    assert should_escalate("the answer", 92.0, floor=60.0) is False


def test_a_shaky_reading_is_escalated():
    assert should_escalate("the ansvver", 42.0, floor=60.0) is True


def test_nothing_read_is_always_escalated():
    assert should_escalate("", None, floor=60.0) is True
    assert should_escalate("   ", 95.0, floor=60.0) is True


def test_an_unmeasured_reading_with_text_is_not_escalated():
    assert should_escalate("the answer", None, floor=60.0) is False


@pytest.mark.parametrize("confidence", [59.9, 0.0])
def test_below_the_floor_escalates(confidence: float):
    assert should_escalate("x", confidence) is True


@pytest.mark.parametrize("confidence", [60.0, 100.0])
def test_at_or_above_the_floor_does_not(confidence: float):
    assert should_escalate("x", confidence) is False


def test_a_never_recorded_source_says_so():
    assert describe_reading_source(None) == "unrecorded"


def test_an_unknown_source_is_shown_rather_than_hidden():
    assert describe_reading_source("some-future-reader") == "some-future-reader"


def test_model_readings_are_grouped():
    assert is_model_reading(SOURCE_VISION_MODEL)
    assert not is_model_reading(SOURCE_TESSERACT)
    assert not is_model_reading(None)


def test_tally_orders_largest_first_and_names_the_unrecorded():
    tally = tally_reading_sources(
        [SOURCE_TESSERACT, SOURCE_TESSERACT, SOURCE_VISION_MODEL, None]
    )

    assert tally.counts == (("tesseract", 2), ("unrecorded", 1), ("vision-model", 1))
    assert tally.total == 4
    assert tally.count_of(SOURCE_VISION_MODEL) == 1
    assert "Tesseract OCR 2" in tally.describe()


def test_tallies_merge_across_submissions():
    merged = ReadingTally(counts=(("tesseract", 2),)).merge(
        ReadingTally(counts=(("tesseract", 3), ("vision-model", 1)))
    )

    assert merged.counts == (("tesseract", 5), ("vision-model", 1))


def test_an_empty_tally_says_so_rather_than_printing_nothing():
    assert ReadingTally().describe() == "no responses transcribed"


def _document(**question_kwargs) -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name="Tommy Liu",
        student_id="012345",
        course_code="CS146",
        assignment_title="Quiz 1",
        assignment_version=None,
        source_pdf="submission-0001.pdf",
        questions=[
            StudentSubmissionQuestion(
                question_id="q1",
                # Points would trigger prompt-prefix normalization during this round trip.
                prompt="What is Ohm's law?",
                possible_points=None,
                response_image_path="responses/q1.png",
                response_text="V=IR",
                **question_kwargs,
            )
        ],
    )


def test_the_source_round_trips():
    rendered = render_student_submission_markdown(
        _document(response_source=SOURCE_TESSERACT, response_confidence=42.0)
    )

    assert "  - Read by: tesseract" in rendered
    parsed = parse_student_submission_markdown(rendered)
    assert parsed.questions[0].response_source == SOURCE_TESSERACT


def test_a_file_with_no_source_stays_byte_identical():
    rendered = render_student_submission_markdown(_document())

    assert "Read by" not in rendered
    assert render_student_submission_markdown(
        parse_student_submission_markdown(rendered)
    ) == rendered


def test_an_unknown_source_survives_a_round_trip():
    rendered = render_student_submission_markdown(_document(response_source="future-reader"))

    assert parse_student_submission_markdown(rendered).questions[0].response_source == (
        "future-reader"
    )


def test_the_provider_rides_the_read_by_line_as_a_suffix():
    rendered = render_student_submission_markdown(
        _document(response_source=SOURCE_VISION_MODEL, response_provider="openai")
    )

    assert "  - Read by: vision-model (openai)" in rendered
    parsed = parse_student_submission_markdown(rendered)
    assert parsed.questions[0].response_source == SOURCE_VISION_MODEL
    assert parsed.questions[0].response_provider == "openai"
    assert render_student_submission_markdown(parsed) == rendered


def test_a_source_with_no_provider_stays_byte_identical():
    """The common case (Tesseract, a human edit, an old vision reading with no
    provider on record) must render and round-trip exactly as it did before
    providers existed.
    """
    rendered = render_student_submission_markdown(
        _document(response_source=SOURCE_TESSERACT, response_confidence=42.0)
    )

    assert "(" not in rendered.splitlines()[
        next(i for i, line in enumerate(rendered.splitlines()) if "Read by" in line)
    ]
    parsed = parse_student_submission_markdown(rendered)
    assert parsed.questions[0].response_provider is None
    assert render_student_submission_markdown(parsed) == rendered


def test_an_old_file_written_before_providers_existed_parses_cleanly():
    """A hand-authored `Read by` line with no parenthetical, exactly what every
    file written before this feature looks like.
    """
    rendered = render_student_submission_markdown(_document(response_source=SOURCE_HUMAN))
    assert "Read by: human" in rendered

    parsed = parse_student_submission_markdown(rendered)
    assert parsed.questions[0].response_source == SOURCE_HUMAN
    assert parsed.questions[0].response_provider is None


def test_the_source_line_sits_above_its_confidence():
    rendered = render_student_submission_markdown(
        _document(response_source=SOURCE_TESSERACT, response_confidence=42.0)
    )
    lines = rendered.splitlines()

    assert lines.index("  - Read by: tesseract") < lines.index("  - Confidence: 42")


def test_a_surgical_update_inserts_the_line_where_a_re_render_would():
    original = render_student_submission_markdown(_document())

    updated = update_student_submission_markdown(
        original,
        StudentSubmissionGradeUpdate(
            question_id="q1",
            response_text="V = IR",
            response_confidence=100.0,
            response_source=SOURCE_HUMAN,
        ),
    )

    parsed = parse_student_submission_markdown(updated)
    assert parsed.questions[0].response_source == SOURCE_HUMAN
    assert parsed.questions[0].response_confidence == 100.0
    assert render_student_submission_markdown(parsed) == updated


def test_omitting_the_source_leaves_the_existing_one_alone():
    original = render_student_submission_markdown(
        _document(response_source=SOURCE_TESSERACT)
    )

    updated = update_student_submission_markdown(
        original,
        StudentSubmissionGradeUpdate(question_id="q1", response_text="new text"),
    )

    assert parse_student_submission_markdown(updated).questions[0].response_source == (
        SOURCE_TESSERACT
    )


def test_clearing_the_source_removes_the_line():
    original = render_student_submission_markdown(
        _document(response_source=SOURCE_HUMAN, response_confidence=100.0)
    )

    updated = update_student_submission_markdown(
        original,
        StudentSubmissionGradeUpdate(
            question_id="q1",
            response_text="V=IR",
            clear_response_source=True,
            clear_response_confidence=True,
        ),
    )

    assert "Read by" not in updated
    assert "Confidence" not in updated
    parsed = parse_student_submission_markdown(updated)
    assert parsed.questions[0].response_source is None
    assert parsed.questions[0].response_confidence is None
