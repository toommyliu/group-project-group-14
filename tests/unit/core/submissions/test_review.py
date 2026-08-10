from opengrader.core.question_types import FREE_RESPONSE, MULTIPLE_CHOICE
from opengrader.core.roster import RosterStudent
from opengrader.core.source_location import QuestionSourceSegment
from opengrader.core.submissions import (
    ReviewReason,
    build_review_flags,
    flag_missing_submissions,
    flag_submission,
)
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)

_SEG = (QuestionSourceSegment(page_number=1, bbox_norm=(0.1, 0.2, 0.3, 0.4)),)


def _question(
    question_id: str = "q1",
    *,
    question_type: str = FREE_RESPONSE,
    response_text: str = "an answer",
    points_awarded: float | None = None,
    rubric_labels: tuple[str, ...] = (),
    analysis: str = "",
    source_segments=_SEG,
    response_confidence: float | None = None,
) -> StudentSubmissionQuestion:
    return StudentSubmissionQuestion(
        question_id=question_id,
        prompt="",
        possible_points=10,
        response_image_path=None,
        response_text=response_text,
        points_awarded=points_awarded,
        rubric_labels=rubric_labels,
        analysis=analysis,
        question_type=question_type,
        source_segments=source_segments,
        response_confidence=response_confidence,
    )


def _doc(
    *,
    student_id: str = "123456789",
    student_name: str = "Tommy Liu",
    questions: list[StudentSubmissionQuestion] | None = None,
) -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name=student_name,
        student_id=student_id,
        course_code="CS146",
        assignment_title="Quiz 1",
        assignment_version="A",
        source_pdf="submission-0001.pdf",
        questions=questions if questions is not None else [_question()],
    )


def _reasons(flags) -> set[ReviewReason]:
    return {flag.reason for flag in flags}


def test_clean_submission_produces_no_flags() -> None:
    assert flag_submission(_doc()) == []


def test_missing_student_id_is_high_severity() -> None:
    flags = flag_submission(_doc(student_id=""))
    assert len(flags) == 1
    assert flags[0].reason is ReviewReason.MISSING_STUDENT_ID
    assert flags[0].severity == "high"


def test_malformed_student_id_flagged_medium() -> None:
    flags = flag_submission(_doc(student_id="1 2?"))
    assert ReviewReason.MALFORMED_STUDENT_ID in _reasons(flags)
    assert all(f.severity == "medium" for f in flags)


def test_missing_student_name_flagged() -> None:
    flags = flag_submission(_doc(student_name="  "))
    assert ReviewReason.MISSING_STUDENT_NAME in _reasons(flags)


def test_empty_free_response_flagged_but_multiple_choice_is_not() -> None:
    fr = _question("q1", question_type=FREE_RESPONSE, response_text="  none ")
    mc = _question("q2", question_type=MULTIPLE_CHOICE, response_text="")
    flags = flag_submission(_doc(questions=[fr, mc]))
    empty = [f for f in flags if f.reason is ReviewReason.EMPTY_RESPONSE]
    assert [f.question_id for f in empty] == ["q1"]


def test_missing_source_segments_flagged() -> None:
    q = _question("q1", source_segments=())
    flags = flag_submission(_doc(questions=[q]))
    seg = [f for f in flags if f.reason is ReviewReason.MISSING_SOURCE_SEGMENTS]
    assert len(seg) == 1 and seg[0].question_id == "q1"


def test_low_confidence_ai_grade_flagged_high() -> None:
    q = _question("q1", points_awarded=5, rubric_labels=("Partial",), analysis="[AI · 62%] reasonable")
    flags = flag_submission(_doc(questions=[q]))
    ai = [f for f in flags if f.reason is ReviewReason.LOW_CONFIDENCE_AI_GRADE]
    assert len(ai) == 1 and ai[0].severity == "high"


def test_confirmed_ai_grade_not_flagged() -> None:
    # A human has been here and kept the grade. Leaving it flagged would mean
    # the queue never empties and the release pre-flight never clears, which is
    # how a warning becomes something people click past.
    q = _question(
        "q1",
        points_awarded=5,
        rubric_labels=("Partial",),
        analysis="[AI · 62% · llama3.1 · confirmed] reasonable",
    )
    flags = flag_submission(_doc(questions=[q]))
    assert ReviewReason.LOW_CONFIDENCE_AI_GRADE not in _reasons(flags)


def test_overridden_ai_grade_not_flagged() -> None:
    # The common way an AI grade gets reviewed: a grader disagreed and picked a
    # different rubric option. Nobody has to look at it again, so leaving it
    # flagged would mean the queue could never empty.
    q = _question(
        "q1",
        points_awarded=10,
        rubric_labels=("Correct",),
        analysis="[AI · 62% · llama3.1 · overridden]",
    )
    flags = flag_submission(_doc(questions=[q]))
    assert ReviewReason.LOW_CONFIDENCE_AI_GRADE not in _reasons(flags)


def test_unconfirmed_ai_grade_with_a_model_is_still_flagged() -> None:
    # Recording the model must not be mistaken for a human having checked it.
    q = _question(
        "q1",
        points_awarded=5,
        rubric_labels=("Partial",),
        analysis="[AI · 62% · llama3.1] reasonable",
    )
    flags = flag_submission(_doc(questions=[q]))
    assert ReviewReason.LOW_CONFIDENCE_AI_GRADE in _reasons(flags)


def test_high_confidence_ai_grade_not_flagged() -> None:
    q = _question("q1", points_awarded=10, rubric_labels=("Correct",), analysis="[AI · 95%] good")
    flags = flag_submission(_doc(questions=[q]))
    assert ReviewReason.LOW_CONFIDENCE_AI_GRADE not in _reasons(flags)


def test_zero_score_sweep_flagged_when_all_graded_zero() -> None:
    qs = [
        _question("q1", points_awarded=0, rubric_labels=("Incorrect",)),
        _question("q2", points_awarded=0, rubric_labels=("Incorrect",)),
    ]
    flags = flag_submission(_doc(questions=qs))
    assert ReviewReason.ZERO_SCORE_SWEEP in _reasons(flags)


def test_zero_score_sweep_not_flagged_for_single_zero() -> None:
    qs = [
        _question("q1", points_awarded=0, rubric_labels=("Incorrect",)),
        _question("q2", points_awarded=8, rubric_labels=("Partial",)),
    ]
    flags = flag_submission(_doc(questions=qs))
    assert ReviewReason.ZERO_SCORE_SWEEP not in _reasons(flags)


def test_ungraded_only_flagged_when_requested() -> None:
    q = _question("q1")  # no points, no labels
    assert ReviewReason.UNGRADED not in _reasons(flag_submission(_doc(questions=[q])))
    with_ungraded = flag_submission(_doc(questions=[q]), include_ungraded=True)
    assert ReviewReason.UNGRADED in _reasons(with_ungraded)


def test_build_review_flags_sorts_high_severity_first() -> None:
    clean = _doc(student_id="111111111", student_name="Zoe")
    missing_id = _doc(student_id="", student_name="Amy")
    flags = build_review_flags([clean, missing_id])
    assert flags[0].reason is ReviewReason.MISSING_STUDENT_ID
    # Severity ranks are non-decreasing through the list.
    ranks = [f.severity_rank for f in flags]
    assert ranks == sorted(ranks)


# ── Missing-submission (cohort-level) checks ──────────────────────────────────

_ROSTER = [
    RosterStudent(student_id="111111111", name="Tommy Liu"),
    RosterStudent(student_id="222222222", name="Andy Cortes"),
    RosterStudent(student_id="333333333", name="Zoe Ng"),
]


def test_missing_submission_flagged_for_roster_student_with_no_file() -> None:
    submitted = [_doc(student_id="111111111", student_name="Tommy Liu")]
    flags = flag_missing_submissions(_ROSTER, submitted)
    missing_ids = {f.student_id for f in flags}
    assert missing_ids == {"222222222", "333333333"}
    assert all(f.reason is ReviewReason.MISSING_SUBMISSION for f in flags)
    assert all(f.severity == "medium" for f in flags)
    assert all(f.source_pdf == "" and f.question_id == "" for f in flags)


def test_present_student_matched_by_id_is_not_flagged() -> None:
    # Id matches even though the name was mis-transcribed on the submission.
    submitted = [_doc(student_id="222222222", student_name="A. Cortez")]
    flags = flag_missing_submissions(_ROSTER, submitted)
    assert "222222222" not in {f.student_id for f in flags}


def test_present_student_matched_by_unique_name_when_id_missing() -> None:
    # Id box was blank/unreadable, but the unique roster name still matches.
    submitted = [_doc(student_id="", student_name="  andy   cortes ")]
    flags = flag_missing_submissions(_ROSTER, submitted)
    assert "222222222" not in {f.student_id for f in flags}


def test_shared_roster_name_does_not_mask_a_missing_twin() -> None:
    roster = [
        RosterStudent(student_id="444444444", name="John Smith"),
        RosterStudent(student_id="555555555", name="John Smith"),
    ]
    # One "John Smith" submitted by name only; the twin must still be flagged.
    submitted = [_doc(student_id="", student_name="John Smith")]
    flags = flag_missing_submissions(roster, submitted)
    assert {f.student_id for f in flags} == {"444444444", "555555555"}


def test_blank_roster_row_is_ignored() -> None:
    roster = [RosterStudent(student_id="  ", name="  ")]
    assert flag_missing_submissions(roster, []) == []


def test_build_review_flags_includes_missing_submissions_when_roster_given() -> None:
    submitted = [_doc(student_id="111111111", student_name="Tommy Liu")]
    without = build_review_flags(submitted)
    assert ReviewReason.MISSING_SUBMISSION not in _reasons(without)

    with_roster = build_review_flags(submitted, roster=_ROSTER)
    missing = [f for f in with_roster if f.reason is ReviewReason.MISSING_SUBMISSION]
    assert {f.student_id for f in missing} == {"222222222", "333333333"}
    # Still globally sorted by severity.
    ranks = [f.severity_rank for f in with_roster]
    assert ranks == sorted(ranks)


def test_low_confidence_transcription_flagged_medium() -> None:
    flags = flag_submission(
        _doc(questions=[_question(response_text="mitochondria", response_confidence=42.0)])
    )
    assert len(flags) == 1
    assert flags[0].reason is ReviewReason.LOW_CONFIDENCE_TRANSCRIPTION
    assert flags[0].severity == "medium"
    assert flags[0].question_id == "q1"


def test_high_confidence_transcription_not_flagged() -> None:
    flags = flag_submission(
        _doc(questions=[_question(response_text="mitochondria", response_confidence=95.0)])
    )
    assert flags == []


def test_vlm_none_confidence_is_not_flagged() -> None:
    # A VLM transcription (no confidence) is deliberately left unflagged.
    flags = flag_submission(
        _doc(questions=[_question(response_text="mitochondria", response_confidence=None)])
    )
    assert flags == []


def test_measured_empty_response_is_unreadable_not_merely_empty() -> None:
    # An empty response the reader *measured* and distrusts is a stronger
    # signal than a plain blank: OCR that read nothing at 10% confidence, or a
    # bubble sheet with two circles filled, is an extraction failure rather
    # than a student who skipped the question. It gets its own high-severity
    # reason, and never the low-confidence-*transcription* one (there is no
    # transcription to distrust).
    flags = flag_submission(
        _doc(questions=[_question(response_text="", response_confidence=10.0)])
    )
    reasons = _reasons(flags)
    assert ReviewReason.UNREADABLE_RESPONSE in reasons
    assert ReviewReason.LOW_CONFIDENCE_TRANSCRIPTION not in reasons
    assert ReviewReason.EMPTY_RESPONSE not in reasons
    unreadable = [f for f in flags if f.reason is ReviewReason.UNREADABLE_RESPONSE]
    assert unreadable[0].severity == "high"


def test_plain_blank_response_is_still_only_empty() -> None:
    # No measurement (None) or a confident one: the student simply left it
    # blank, which stays the quiet low-severity flag it always was.
    for confidence in (None, 100.0):
        flags = flag_submission(
            _doc(questions=[_question(response_text="", response_confidence=confidence)])
        )
        reasons = _reasons(flags)
        assert ReviewReason.EMPTY_RESPONSE in reasons
        assert ReviewReason.UNREADABLE_RESPONSE not in reasons


def test_unreadable_mark_flagged_on_a_multiple_choice_question() -> None:
    # The bubble-sheet case: multiple choice never raises EMPTY_RESPONSE, so
    # before this reason existed a double-marked bubble surfaced nowhere.
    flags = flag_submission(
        _doc(
            questions=[
                _question(
                    question_type=MULTIPLE_CHOICE,
                    response_text="",
                    response_confidence=0.0,
                )
            ]
        )
    )
    unreadable = [f for f in flags if f.reason is ReviewReason.UNREADABLE_RESPONSE]
    assert len(unreadable) == 1
    assert unreadable[0].severity == "high"
    assert unreadable[0].question_id == "q1"


def test_ocr_confidence_floor_is_configurable() -> None:
    q = [_question(response_text="mitochondria", response_confidence=70.0)]
    assert flag_submission(_doc(questions=q)) == []  # 70 >= default 60
    flags = flag_submission(_doc(questions=q), ocr_confidence_floor=80)
    assert flags[0].reason is ReviewReason.LOW_CONFIDENCE_TRANSCRIPTION
