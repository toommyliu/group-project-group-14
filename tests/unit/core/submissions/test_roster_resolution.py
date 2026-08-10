from opengrader.core.submissions import (
    RosterMatch,
    resolve_student_identity,
)
from opengrader.core.submissions.processor import (
    UNASSIGNED_SECTION,
    _apply_roster_identity,
    _apply_section,
    _RosterEntry,
    build_student_submission_document_unified,  # noqa: F401  (import sanity)
)
from opengrader.core.submissions.student_file import StudentSubmissionDocument


ROSTER = [
    _RosterEntry(student_id="111111111", name="Tommy Liu"),
    _RosterEntry(student_id="222222222", name="Andy Cortes"),
    _RosterEntry(student_id="333333333", name="John Smith"),
    _RosterEntry(student_id="444444444", name="John Smith"),  # duplicate name
]


def test_usable_id_uses_combined_match() -> None:
    result = resolve_student_identity("111111111", "Tommy Liu", ROSTER)
    assert result.status == "matched"
    assert result.entry.student_id == "111111111"


def test_name_only_unique_match_fills_id() -> None:
    # Id box was blank; the name alone identifies the student.
    result = resolve_student_identity("", "Andy Cortes", ROSTER)
    assert result.status == "matched"
    assert result.entry.student_id == "222222222"


def test_name_only_slightly_misread_still_matches() -> None:
    result = resolve_student_identity("", "Tommy Liu", ROSTER)  # missing an 'l'
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
    # Duplicate name would normally be ambiguous; a QR id resolves it outright,
    # pulling the canonical roster name and skipping the candidate list entirely.
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
    assert resolved.id_candidates == ()  # no ambiguity recorded


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
    # An unusable id ("1") should not block a confident name match.
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
    # Never guesses between duplicate names.
    assert updated.student_id == ""
    assert updated.student_name == "John Smith"
    assert any("multiple students" in m for m in messages)
    # The tied roster people are recorded so a human can pick from them later
    # (in the review queue) instead of re-typing an id.
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
        # Pick the second "John Smith".
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
    assert updated.student_id == ""  # skipped -> still needs manual assignment
    # Still records the candidates for the review queue to offer.
    assert {c.student_id for c in updated.id_candidates} == {"333333333", "444444444"}


# --- Section assignment (`_apply_section`) --------------------------------------


def test_apply_section_default_tags_every_submission() -> None:
    # One-stack-per-section: even an unidentified paper takes the run's section.
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
    # A mixed stack can only route by a resolved id; a blank one can't be placed.
    updated = _apply_section(
        _doc("Someone", ""), None, {"111111111": "MW"}, lambda _m: None
    )
    assert updated.section == UNASSIGNED_SECTION


def test_apply_section_noop_when_no_routing() -> None:
    updated = _apply_section(_doc("Tommy Liu", "111111111"), None, None, lambda _m: None)
    assert updated.section is None
