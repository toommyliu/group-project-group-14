from opengrader.core.submissions import (
    RosterMatch,
    resolve_student_identity,
)
from opengrader.core.submissions.processor import (
    UNASSIGNED_SECTION,
    _apply_roster_identity,
    _apply_section,
    _RosterEntry,
    build_student_submission_document_unified,  # noqa: F401
)
from opengrader.core.submissions.student_file import StudentSubmissionDocument


ROSTER = [
    _RosterEntry(student_id="111111111", name="Tommy Liu"),
    _RosterEntry(student_id="222222222", name="Andy Cortes"),
    _RosterEntry(student_id="333333333", name="John Smith"),
    _RosterEntry(student_id="444444444", name="John Smith"),
]


def test_usable_id_uses_combined_match() -> None:
    result = resolve_student_identity("111111111", "Tommy Liu", ROSTER)
    assert result.status == "matched"
    assert result.entry.student_id == "111111111"


def test_name_only_unique_match_fills_id() -> None:
    result = resolve_student_identity("", "Andy Cortes", ROSTER)
    assert result.status == "matched"
    assert result.entry.student_id == "222222222"


def test_name_only_slightly_misread_still_matches() -> None:
    result = resolve_student_identity("", "Tommy Liu", ROSTER)
    assert result.status == "matched"
    assert result.entry.student_id == "111111111"


def test_duplicate_name_is_ambiguous_not_guessed() -> None:
    result = resolve_student_identity("", "John Smith", ROSTER)
    assert result.status == "ambiguous"
    assert result.entry is None
    assert {c.student_id for c in result.candidates} == {"333333333", "444444444"}


def _noop(_msg: str) -> None:
    return None


def test_qr_id_overrides_ambiguous_name() -> None:
    doc = StudentSubmissionDocument(
        student_name="John Smith",
        student_id="",
        course_code=None,
        assignment_title="",
        assignment_version=None,
        source_pdf="john.pdf",
        student_score="",
        questions=(),
    )
    resolved = _apply_roster_identity(doc, ROSTER, _noop, None, "444444444")
    assert resolved.student_id == "444444444"
    assert resolved.student_name == "John Smith"
    assert resolved.id_candidates == ()


def test_qr_id_not_on_roster_is_still_trusted() -> None:
    doc = StudentSubmissionDocument(
        student_name="",
        student_id="",
        course_code=None,
        assignment_title="",
        assignment_version=None,
        source_pdf="x.pdf",
        student_score="",
        questions=(),
    )
    resolved = _apply_roster_identity(doc, ROSTER, _noop, None, "999999")
    assert resolved.student_id == "999999"


def test_unknown_name_is_unmatched() -> None:
    assert resolve_student_identity("", "Nobody Here", ROSTER).status == "unmatched"


def test_blank_identity_is_unmatched() -> None:
    assert resolve_student_identity("", "", ROSTER).status == "unmatched"


def test_short_garbage_id_falls_back_to_name() -> None:
    result = resolve_student_identity("1", "Andy Cortes", ROSTER)
    assert result.status == "matched"
    assert result.entry.student_id == "222222222"


def _doc(name: str, student_id: str) -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name=name,
        student_id=student_id,
        course_code=None,
        assignment_title="Quiz",
        assignment_version=None,
        source_pdf="submission-0001.pdf",
        questions=[],
    )


def test_apply_roster_identity_fills_id_and_reports() -> None:
    messages: list[str] = []
    updated = _apply_roster_identity(_doc("Andy Cortes", ""), ROSTER, messages.append)
    assert updated.student_id == "222222222"
    assert updated.student_name == "Andy Cortes"
    assert any("id 222222222" in m for m in messages)


def test_apply_roster_identity_leaves_ambiguous_blank_and_warns() -> None:
    messages: list[str] = []
    updated = _apply_roster_identity(_doc("John Smith", ""), ROSTER, messages.append)
    assert updated.student_id == ""
    assert updated.student_name == "John Smith"
    assert any("multiple students" in m for m in messages)
    assert {c.student_id for c in updated.id_candidates} == {"333333333", "444444444"}


def test_apply_roster_identity_resolved_ambiguity_records_no_candidates() -> None:
    updated = _apply_roster_identity(
        _doc("John Smith", ""),
        ROSTER,
        lambda _m: None,
        lambda _d, candidates: next(
            c for c in candidates if c.student_id == "444444444"
        ),
    )
    assert updated.student_id == "444444444"
    assert updated.id_candidates == ()


def test_apply_roster_identity_no_roster_is_noop() -> None:
    doc = _doc("Whoever", "")
    assert _apply_roster_identity(doc, None, lambda _m: None) is doc


def test_apply_roster_identity_uses_injected_resolver_on_ambiguous() -> None:
    seen: dict[str, object] = {}

    def resolver(document, candidates):
        seen["name"] = document.student_name
        seen["candidates"] = candidates
        return next(c for c in candidates if c.student_id == "444444444")

    updated = _apply_roster_identity(
        _doc("John Smith", ""), ROSTER, lambda _m: None, resolver
    )
    assert updated.student_id == "444444444"
    assert seen["name"] == "John Smith"
    assert {c.student_id for c in seen["candidates"]} == {"333333333", "444444444"}


def test_apply_roster_identity_resolver_returning_none_leaves_blank() -> None:
    updated = _apply_roster_identity(
        _doc("John Smith", ""), ROSTER, lambda _m: None, lambda _d, _c: None
    )
    assert updated.student_id == ""
    assert {c.student_id for c in updated.id_candidates} == {"333333333", "444444444"}


def test_apply_section_default_tags_every_submission() -> None:
    updated = _apply_section(
        _doc("", ""), "SectionA", None, lambda _m: None
    )
    assert updated.section == "SectionA"


def test_apply_section_routes_by_id_membership() -> None:
    section_by_id = {"111111111": "MW", "222222222": "TTh"}
    updated = _apply_section(
        _doc("Tommy Liu", "111111111"), None, section_by_id, lambda _m: None
    )
    assert updated.section == "MW"


def test_apply_section_unknown_id_is_unassigned() -> None:
    messages: list[str] = []
    updated = _apply_section(
        _doc("Nobody", "999999999"), None, {"111111111": "MW"}, messages.append
    )
    assert updated.section == UNASSIGNED_SECTION
    assert any("unassigned" in m.lower() for m in messages)


def test_apply_section_blank_id_in_mixed_mode_is_unassigned() -> None:
    updated = _apply_section(
        _doc("Someone", ""), None, {"111111111": "MW"}, lambda _m: None
    )
    assert updated.section == UNASSIGNED_SECTION


def test_apply_section_noop_when_no_routing() -> None:
    updated = _apply_section(_doc("Tommy Liu", "111111111"), None, None, lambda _m: None)
    assert updated.section is None


# -- what handwriting read by OCR looks like ----------------------------------

CLASS = [
    _RosterEntry(student_id="111111111", name="Tommy Liu"),
    _RosterEntry(student_id="222222222", name="Andrew Cortes"),
    _RosterEntry(student_id="555555555", name="Amy Liu"),
    _RosterEntry(student_id="666666666", name="Tommy Smith"),
]


def test_last_name_first_matches() -> None:
    result = resolve_student_identity("", "Liu, Tommy", CLASS)
    assert result.status == "matched"
    assert result.entry.student_id == "111111111"


def test_ocr_digits_for_letters_match() -> None:
    result = resolve_student_identity("", "T0mmy L1u", CLASS)
    assert result.status == "matched"
    assert result.entry.student_id == "111111111"


def test_unique_partial_name_matches_below_the_review_floor() -> None:
    from opengrader.core.submissions.review import DEFAULT_IDENTITY_CONFIDENCE_FLOOR

    result = resolve_student_identity("", "Andy Cortes", CLASS)
    assert result.status == "matched"
    assert result.entry.student_id == "222222222"
    # Flagged for a person to confirm, never trusted like a whole name.
    assert result.score * 100 < DEFAULT_IDENTITY_CONFIDENCE_FLOOR


def test_partial_name_shared_by_two_students_is_ambiguous() -> None:
    result = resolve_student_identity("", "Liu", CLASS)
    assert result.status == "ambiguous"
    assert {c.student_id for c in result.candidates} == {"111111111", "555555555"}
    result = resolve_student_identity("", "Tommy", CLASS)
    assert result.status == "ambiguous"
    assert {c.student_id for c in result.candidates} == {"111111111", "666666666"}


def test_whole_name_beats_a_partial_one() -> None:
    roster = [*CLASS, _RosterEntry(student_id="777777777", name="Andy Cortes")]
    result = resolve_student_identity("", "Andy Cortes", roster)
    assert result.status == "matched"
    assert result.entry.student_id == "777777777"


def test_id_with_spaces_and_dashes_matches_exactly() -> None:
    result = resolve_student_identity("111-111 111", "", CLASS)
    assert result.status == "matched"
    assert result.entry.student_id == "111111111"


def test_two_ids_about_as_near_are_not_guessed() -> None:
    roster = [
        _RosterEntry(student_id="123456780", name="Ana Park"),
        _RosterEntry(student_id="123456781", name="Ben Ortiz"),
    ]
    # One digit misread, and no name to tell them apart.
    result = resolve_student_identity("123456789", "", roster)
    assert result.status == "ambiguous"
    assert {c.student_id for c in result.candidates} == {"123456780", "123456781"}


def test_near_ids_are_told_apart_by_the_name() -> None:
    roster = [
        _RosterEntry(student_id="123456780", name="Ana Park"),
        _RosterEntry(student_id="123456781", name="Ben Ortiz"),
    ]
    result = resolve_student_identity("123456789", "Ben Ortiz", roster)
    assert result.status == "matched"
    assert result.entry.student_id == "123456781"
