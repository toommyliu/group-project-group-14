from pathlib import Path

from opengrader.core.release import load_release_context, ReadinessKind
from opengrader.core.roster import RosterStudent
from opengrader.core.submissions.review import (
    ReviewReason,
    build_review_flags,
    flag_off_roster,
)
from opengrader.core.submissions.roster_check import (
    find_class_roster,
    folder_roster_coverage,
    load_class_roster,
    roster_coverage,
)
from opengrader.core.submissions.student_file import StudentSubmissionDocument

ROSTER = [RosterStudent("111", "Tommy Liu"), RosterStudent("222", "Andy Cortes")]


def _doc(student_id: str, name: str = "", source: str = "a.pdf") -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name=name,
        student_id=student_id,
        course_code=None,
        assignment_title="Quiz",
        assignment_version=None,
        source_pdf=source,
        questions=[],
    )


def _write_paper(folder: Path, stem: str, student_id: str, name: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{stem}.submission.md").write_text(
        f"---\nstudent_id: '{student_id}'\nstudent_name: {name}\nsource_pdf: {stem}.pdf\n---\n"
        "- q1:\n  - Response text: Yes\n  - Possible points: 10\n  - Points: 7\n"
    )


def test_off_roster_ids_are_flagged_and_missing_ids_are_not() -> None:
    flags = flag_off_roster(ROSTER, [_doc("111"), _doc("999", source="b.pdf"), _doc("")])
    assert [(flag.student_id, flag.reason) for flag in flags] == [
        ("999", ReviewReason.NOT_ON_ROSTER)
    ]
    assert flags[0].severity == "high"


def test_empty_roster_flags_nothing() -> None:
    assert flag_off_roster([], [_doc("999")]) == []


def test_build_review_flags_includes_off_roster_when_given_a_roster() -> None:
    reasons = {flag.reason for flag in build_review_flags([_doc("999")], roster=ROSTER)}
    assert ReviewReason.NOT_ON_ROSTER in reasons
    without = {flag.reason for flag in build_review_flags([_doc("999")])}
    assert ReviewReason.NOT_ON_ROSTER not in without


def test_coverage_counts_blank_and_off_roster_ids() -> None:
    coverage = roster_coverage([_doc("111"), _doc("999"), _doc("")], ROSTER)
    assert (coverage.total, coverage.count) == (3, 2)
    assert coverage.summary() == "2 of 3 papers aren't matched to the class roster."


def test_class_roster_is_found_beside_the_submissions_folder(tmp_path: Path) -> None:
    (tmp_path / "roster.tsv").write_text("111\tTommy Liu\n222\tAndy Cortes\n")
    _write_paper(tmp_path / "submissions", "s1", "111", "Tommy Liu")
    found = find_class_roster(tmp_path / "submissions")
    assert found is not None
    assert found[0] == tmp_path.resolve()
    assert [student.student_id for student in load_class_roster(found[1])] == ["111", "222"]


def test_no_roster_means_no_coverage(tmp_path: Path) -> None:
    _write_paper(tmp_path / "submissions", "s1", "999", "Nobody")
    assert find_class_roster(tmp_path / "submissions") is None
    assert folder_roster_coverage(tmp_path / "submissions") is None


def test_folder_coverage_reads_nested_papers(tmp_path: Path) -> None:
    (tmp_path / "roster.tsv").write_text("111\tTommy Liu\n222\tAndy Cortes\n")
    _write_paper(tmp_path / "submissions" / "s1", "s1", "111", "Tommy Liu")
    _write_paper(tmp_path / "submissions" / "s2", "s2", "998", "T0mmy")
    coverage = folder_roster_coverage(tmp_path / "submissions")
    assert (coverage.total, coverage.count) == (2, 1)


def test_release_is_blocked_by_a_paper_not_on_the_class_roster(tmp_path: Path) -> None:
    (tmp_path / "roster.tsv").write_text("111\tTommy Liu\n222\tAndy Cortes\n")
    _write_paper(tmp_path / "submissions", "s1", "111", "Tommy Liu")
    _write_paper(tmp_path / "submissions", "s2", "99999", "Someone")
    readiness = load_release_context(tmp_path / "submissions").readiness
    identity = [
        issue for issue in readiness.issues if issue.kind is ReadinessKind.UNRESOLVED_IDENTITY
    ]
    assert identity and identity[0].severity == "blocking"
    assert "not on the roster" in identity[0].summary
