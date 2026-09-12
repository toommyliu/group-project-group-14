from pathlib import Path

from opengrader.core.submissions.regrade import (
    RegradeRequest,
    add_regrade_request,
    deny_regrade_request,
    open_regrade_requests,
    read_regrade_requests,
    resolve_regrade_request,
    resolve_regrades_path,
)
from opengrader.core.submissions.review import (
    ReviewReason,
    build_review_flags,
    flag_regrade_requests,
)
from opengrader.core.submissions.student_file import StudentSubmissionDocument


def _doc(student_id: str = "012345", student_name: str = "Ada Lovelace"):
    return StudentSubmissionDocument(
        student_name=student_name,
        student_id=student_id,
        course_code="CS146",
        assignment_title="Quiz 1",
        assignment_version="A",
        source_pdf="submission-0001.pdf",
        questions=[],
    )


def test_add_and_read_round_trip(tmp_path: Path) -> None:
    request = add_regrade_request(
        tmp_path, student_id="012345", reason="Q3 wrong", question_id="q3"
    )
    assert request.request_id.startswith("rg-")
    assert request.status == "open"
    assert request.source == "instructor"
    assert request.created_by

    stored = read_regrade_requests(tmp_path)
    assert stored == [request]


def test_whole_assignment_dispute_has_empty_question(tmp_path: Path) -> None:
    request = add_regrade_request(tmp_path, student_id="012345", reason="total off")
    assert request.question_id == ""


def test_add_requires_student_id(tmp_path: Path) -> None:
    try:
        add_regrade_request(tmp_path, student_id="   ", reason="x")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for blank student id")


def test_resolve_marks_resolved_and_drops_from_open(tmp_path: Path) -> None:
    request = add_regrade_request(tmp_path, student_id="012345", reason="Q3", question_id="q3")
    updated = resolve_regrade_request(tmp_path, request.request_id, note="regraded +5")
    assert updated is not None
    assert updated.status == "resolved"
    assert updated.resolution_note == "regraded +5"
    assert updated.resolved_by
    assert open_regrade_requests(tmp_path) == []
    assert [r.status for r in read_regrade_requests(tmp_path)] == ["resolved"]


def test_deny_marks_denied(tmp_path: Path) -> None:
    request = add_regrade_request(tmp_path, student_id="012345", reason="Q3")
    updated = deny_regrade_request(tmp_path, request.request_id, note="answer is wrong")
    assert updated is not None
    assert updated.status == "denied"
    assert open_regrade_requests(tmp_path) == []


def test_resolve_unknown_id_returns_none(tmp_path: Path) -> None:
    assert resolve_regrade_request(tmp_path, "rg-nope") is None
    assert deny_regrade_request(tmp_path, "rg-nope") is None


def test_event_sourced_latest_line_wins(tmp_path: Path) -> None:
    request = add_regrade_request(tmp_path, student_id="012345", reason="Q3")
    resolve_regrade_request(tmp_path, request.request_id, note="done")
    raw = resolve_regrades_path(tmp_path).read_text(encoding="utf-8")
    assert len([ln for ln in raw.splitlines() if ln.strip()]) == 2
    assert len(read_regrade_requests(tmp_path)) == 1


def test_missing_file_reads_empty(tmp_path: Path) -> None:
    assert read_regrade_requests(tmp_path) == []
    assert open_regrade_requests(tmp_path) == []


def test_malformed_line_is_skipped(tmp_path: Path) -> None:
    add_regrade_request(tmp_path, student_id="012345", reason="Q3")
    path = resolve_regrades_path(tmp_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not json at all\n")
    assert len(read_regrade_requests(tmp_path)) == 1


def test_from_json_normalizes_bad_status_and_source() -> None:
    line = RegradeRequest(
        request_id="rg-1", student_id="1", reason="x"
    ).to_json().replace('"status": "open"', '"status": "weird"')
    parsed = RegradeRequest.from_json(line)
    assert parsed.status == "open"


def test_flag_regrade_requests_enriches_from_documents(tmp_path: Path) -> None:
    request = add_regrade_request(
        tmp_path, student_id="012345", reason="Q3 wrong", question_id="q3"
    )
    flags = flag_regrade_requests([request], [_doc()])
    assert len(flags) == 1
    flag = flags[0]
    assert flag.reason is ReviewReason.OPEN_REGRADE_REQUEST
    assert flag.severity == "high"
    assert flag.student_name == "Ada Lovelace"
    assert flag.source_pdf == "submission-0001.pdf"
    assert flag.question_id == "q3"
    assert "Q3 wrong" in flag.detail


def test_flag_regrade_requests_unmatched_id_still_surfaces(tmp_path: Path) -> None:
    request = add_regrade_request(tmp_path, student_id="999999", reason="x")
    flags = flag_regrade_requests([request], [_doc(student_id="012345")])
    assert len(flags) == 1
    assert flags[0].student_id == "999999"
    assert flags[0].student_name == ""


def test_resolved_requests_are_not_flagged(tmp_path: Path) -> None:
    request = add_regrade_request(tmp_path, student_id="012345", reason="x", question_id="q3")
    resolve_regrade_request(tmp_path, request.request_id)
    flags = flag_regrade_requests(open_regrade_requests(tmp_path), [_doc()])
    assert flags == []


def test_build_review_flags_folds_in_open_regrades(tmp_path: Path) -> None:
    request = add_regrade_request(
        tmp_path, student_id="012345", reason="Q3", question_id="q3"
    )
    flags = build_review_flags(
        [_doc()], regrade_requests=open_regrade_requests(tmp_path)
    )
    reasons = {flag.reason for flag in flags}
    assert ReviewReason.OPEN_REGRADE_REQUEST in reasons
    assert flags[0].reason is ReviewReason.OPEN_REGRADE_REQUEST
    assert request.request_id
