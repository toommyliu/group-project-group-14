"""Field-level name/id confidence: recorded at extraction, read at triage.

The frontmatter (``student_name_confidence``/``student_id_confidence``) has
round-tripped for a while but nothing ever wrote it. These cover the two halves
that close it: which resolution path records what, and what the review queue
does with the number afterwards.
"""

from __future__ import annotations

import pytest

from opengrader.core.submissions.processor import (
    _RosterEntry,
    _apply_roster_identity,
    resolve_student_identity,
)
from opengrader.core.submissions.review import (
    DEFAULT_IDENTITY_CONFIDENCE_FLOOR,
    ReviewReason,
    flag_submission,
)
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    parse_student_submission_markdown,
    render_student_submission_markdown,
)


def _doc(
    *,
    student_id: str = "",
    student_name: str = "",
    name_confidence: float | None = None,
    id_confidence: float | None = None,
) -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name=student_name,
        student_id=student_id,
        course_code=None,
        assignment_title="Quiz 1",
        assignment_version=None,
        source_pdf="sub-0001.pdf",
        student_score="",
        name_confidence=name_confidence,
        id_confidence=id_confidence,
        questions=[
            StudentSubmissionQuestion(
                question_id="q1",
                prompt="",
                possible_points=5,
                response_image_path=None,
                response_text="an answer",
                points_awarded=3,
            )
        ],
    )


ROSTER = [
    _RosterEntry(student_id="012345", name="Tommy Liu"),
    _RosterEntry(student_id="067890", name="Andy Cortes"),
]


def _quiet(_message: str) -> None:
    """Progress callback that swallows the extractor's chatter."""


# --------------------------------------------------------------------------- #
# What each resolution path records
# --------------------------------------------------------------------------- #


def test_a_qr_id_is_recorded_as_exact():
    """The symbol is error-corrected: it decodes or it doesn't."""
    resolved = _apply_roster_identity(
        _doc(student_name="Tommy Liu"), ROSTER, _quiet, None, "012345"
    )
    assert resolved.student_id == "012345"
    assert resolved.id_confidence == 100.0
    assert resolved.name_confidence == 100.0


def test_a_qr_id_off_the_roster_verifies_only_the_id():
    """The name is still whatever was read off the sheet, so it stays as read."""
    document = _doc(student_name="Tomy Liu", name_confidence=54.0)
    resolved = _apply_roster_identity(document, ROSTER, _quiet, None, "999999")

    assert resolved.id_confidence == 100.0
    assert resolved.name_confidence == 54.0


def test_a_roster_match_records_its_own_similarity():
    document = _doc(student_name="Tommy Liu")
    resolved = _apply_roster_identity(document, ROSTER, _quiet, None, None)

    assert resolved.student_id == "012345"
    # An exact name match, so the recorded confidence is the top of the scale.
    assert resolved.id_confidence == 100.0


def test_a_weaker_roster_match_records_a_lower_number():
    """The whole point: a match that barely cleared its threshold says so."""
    # Above the 0.85 name threshold, so it matches — but not by much, which is
    # exactly the case the recorded number exists to expose.
    document = _doc(student_name="Tommy Lju")
    match = resolve_student_identity("", "Tommy Lju", ROSTER)
    assert match.status == "matched"
    assert match.score is not None and match.score < 1.0

    resolved = _apply_roster_identity(document, ROSTER, _quiet, None, None)
    assert resolved.id_confidence == pytest.approx(round(match.score * 100, 1))
    assert resolved.id_confidence < 100.0


def test_a_human_resolving_an_ambiguity_is_recorded_as_certain():
    twins = [
        _RosterEntry(student_id="111111", name="Sam Lee"),
        _RosterEntry(student_id="222222", name="Sam Lee"),
    ]
    resolved = _apply_roster_identity(
        _doc(student_name="Sam Lee"), twins, _quiet, lambda _doc, c: c[0], None
    )
    assert resolved.student_id == "111111"
    assert resolved.id_confidence == 100.0


def test_an_unresolved_ambiguity_records_no_confidence():
    """Nothing was decided, so there is no confidence to state."""
    twins = [
        _RosterEntry(student_id="111111", name="Sam Lee"),
        _RosterEntry(student_id="222222", name="Sam Lee"),
    ]
    resolved = _apply_roster_identity(
        _doc(student_name="Sam Lee"), twins, _quiet, None, None
    )
    assert resolved.student_id == ""
    assert resolved.id_confidence is None
    assert len(resolved.id_candidates) == 2


def test_no_roster_leaves_the_readers_own_confidence_alone():
    document = _doc(student_id="012345", student_name="Tommy Liu", id_confidence=71.0)
    resolved = _apply_roster_identity(document, None, _quiet, None, None)
    assert resolved.id_confidence == 71.0


def test_confidence_round_trips_through_the_submission_file():
    document = _doc(
        student_id="012345",
        student_name="Tommy Liu",
        name_confidence=88.5,
        id_confidence=61.0,
    )
    reparsed = parse_student_submission_markdown(
        render_student_submission_markdown(document)
    )
    assert reparsed.name_confidence == 88.5
    assert reparsed.id_confidence == 61.0


def test_a_file_with_no_confidence_stays_byte_identical():
    """Emitted only when present, like every other optional frontmatter key."""
    document = _doc(student_id="012345", student_name="Tommy Liu")
    rendered = render_student_submission_markdown(document)
    assert "student_name_confidence" not in rendered
    assert "student_id_confidence" not in rendered


# --------------------------------------------------------------------------- #
# What triage does with it
# --------------------------------------------------------------------------- #


def _reasons(document) -> set[ReviewReason]:
    return {flag.reason for flag in flag_submission(document)}


def test_a_low_confidence_identity_is_flagged():
    document = _doc(student_id="012345", student_name="Tommy Liu", id_confidence=55.0)
    flags = [
        flag
        for flag in flag_submission(document)
        if flag.reason is ReviewReason.LOW_CONFIDENCE_IDENTITY
    ]
    assert len(flags) == 1
    assert flags[0].severity == "medium"
    assert "id 55%" in flags[0].detail


def test_both_fields_are_named_in_one_flag():
    """One flag per submission, not one per field — this is one question."""
    document = _doc(
        student_id="012345",
        student_name="Tommy Liu",
        id_confidence=40.0,
        name_confidence=50.0,
    )
    flags = [
        flag
        for flag in flag_submission(document)
        if flag.reason is ReviewReason.LOW_CONFIDENCE_IDENTITY
    ]
    assert len(flags) == 1
    assert "id 40%" in flags[0].detail
    assert "name 50%" in flags[0].detail


def test_an_unmeasured_confidence_is_never_flagged():
    """``None`` means nobody measured it, which is not evidence of a problem."""
    document = _doc(student_id="012345", student_name="Tommy Liu")
    assert ReviewReason.LOW_CONFIDENCE_IDENTITY not in _reasons(document)


def test_a_confident_identity_is_not_flagged():
    document = _doc(
        student_id="012345",
        student_name="Tommy Liu",
        id_confidence=float(DEFAULT_IDENTITY_CONFIDENCE_FLOOR),
        name_confidence=99.0,
    )
    assert ReviewReason.LOW_CONFIDENCE_IDENTITY not in _reasons(document)


def test_a_malformed_id_does_not_also_raise_the_confidence_flag():
    """Two flags about the same field is noise; the structural one wins."""
    document = _doc(student_id="1?", student_name="Tommy Liu", id_confidence=20.0)
    reasons = _reasons(document)
    assert ReviewReason.MALFORMED_STUDENT_ID in reasons
    assert ReviewReason.LOW_CONFIDENCE_IDENTITY not in reasons


def test_a_missing_id_does_not_also_raise_the_confidence_flag():
    document = _doc(student_id="", student_name="Tommy Liu", name_confidence=99.0)
    reasons = _reasons(document)
    assert ReviewReason.MISSING_STUDENT_ID in reasons
    assert ReviewReason.LOW_CONFIDENCE_IDENTITY not in reasons


def test_the_floor_is_configurable():
    document = _doc(student_id="012345", student_name="Tommy Liu", id_confidence=85.0)
    assert not [
        flag
        for flag in flag_submission(document, identity_confidence_floor=80)
        if flag.reason is ReviewReason.LOW_CONFIDENCE_IDENTITY
    ]
    assert [
        flag
        for flag in flag_submission(document, identity_confidence_floor=90)
        if flag.reason is ReviewReason.LOW_CONFIDENCE_IDENTITY
    ]


def test_an_unsure_identity_warns_the_release_but_does_not_block_it():
    from opengrader.core.release import ReadinessKind, build_readiness_report
    from opengrader.core.submissions.review import build_review_flags

    document = _doc(student_id="012345", student_name="Tommy Liu", id_confidence=40.0)
    report = build_readiness_report(
        [document], review_flags=build_review_flags([document])
    )
    kinds = {issue.kind for issue in report.issues}
    assert ReadinessKind.UNSURE_IDENTITY in kinds
    assert report.ready
