"""Multi-grader work claims.

The engine's whole job is that two graders never hold the same work, so most of
these tests are about the boundary: what overlaps, what doesn't, what gets
refused, and the one dangerous default (an empty slice means *everything*).
"""

from pathlib import Path

import pytest

from opengrader.core.claims import (
    ClaimConflictError,
    WorkClaim,
    WorkSlice,
    active_claims,
    claim_for,
    claim_work,
    complete_claim,
    coverage_report,
    divide_work,
    find_conflicts,
    is_owned_by_other,
    read_claims,
    release_claim,
    resolve_claims_path,
)

QUESTIONS = ["q1", "q2", "q3", "q4"]
STUDENTS = ["s-a", "s-b", "s-c"]


def _claim(actor: str, **slice_kwargs) -> WorkClaim:
    return WorkClaim(
        claim_id=f"cl-{actor}",
        actor=actor,
        work=WorkSlice(**slice_kwargs),
        status="active",
    )


# --------------------------------------------------------------------------- #
# WorkSlice: what a slice covers and what it collides with
# --------------------------------------------------------------------------- #


def test_an_empty_slice_is_the_whole_assignment() -> None:
    everything = WorkSlice()
    assert everything.is_everything
    assert everything.covers("q1", "s-a")
    assert everything.describe() == "everything"


def test_one_axis_left_empty_means_all_of_that_axis() -> None:
    # "q1-q2, every student" — the shape `divide --by question` produces.
    work = WorkSlice(question_ids=("q1", "q2"))
    assert work.covers("q1", "s-a")
    assert work.covers("q2", "s-zzz")
    assert not work.covers("q3", "s-a")


def test_both_axes_narrow_to_the_intersection() -> None:
    work = WorkSlice(question_ids=("q1",), student_ids=("s-a",))
    assert work.covers("q1", "s-a")
    assert not work.covers("q1", "s-b")
    assert not work.covers("q2", "s-a")


def test_disjoint_question_runs_do_not_overlap() -> None:
    assert not WorkSlice(question_ids=("q1", "q2")).overlaps(
        WorkSlice(question_ids=("q3", "q4"))
    )


def test_disjoint_student_runs_do_not_overlap() -> None:
    assert not WorkSlice(student_ids=("s-a",)).overlaps(WorkSlice(student_ids=("s-b",)))


def test_overlap_needs_both_axes_to_intersect() -> None:
    # Same questions but different students is a legitimate division of labour.
    first = WorkSlice(question_ids=("q1",), student_ids=("s-a",))
    second = WorkSlice(question_ids=("q1",), student_ids=("s-b",))
    assert not first.overlaps(second)

    third = WorkSlice(question_ids=("q1",), student_ids=("s-a", "s-b"))
    assert first.overlaps(third)


def test_everything_overlaps_anything() -> None:
    assert WorkSlice().overlaps(WorkSlice(question_ids=("q1",)))
    assert WorkSlice(question_ids=("q1",)).overlaps(WorkSlice())


def test_describe_reads_like_a_person_wrote_it() -> None:
    assert WorkSlice(question_ids=("q1",)).describe() == "q1"
    assert WorkSlice(question_ids=("q1", "q2", "q3")).describe() == "q1-q3"
    assert (
        WorkSlice(question_ids=("q1", "q2"), student_ids=("a", "b")).describe()
        == "q1-q2 · 2 students"
    )


def test_a_broken_question_run_is_still_named_not_counted() -> None:
    """Real assignments number questions q1, q2, q5a, q5b — not 1..n.

    Those ids are not a numeric run, and a grader told they hold "6 questions"
    has learned nothing about which six. Short sets are spelled out.
    """
    assert WorkSlice(question_ids=("q1", "q4")).describe() == "q1, q4"
    assert (
        WorkSlice(question_ids=("q1", "q2", "q3", "q4", "q5a", "q5b")).describe()
        == "q1, q2, q3, q4, q5a, q5b"
    )
    # Past the point where a line stays readable, fall back to bounds + a count.
    many = tuple(f"q{n}" for n in (1, 3, 5, 7, 9, 11, 13, 15, 17))
    assert WorkSlice(question_ids=many).describe() == "q1…q17 (9 questions)"


def test_a_single_student_is_named_but_a_pile_is_counted() -> None:
    # Student ids are opaque digits: forty of them listed helps nobody.
    assert WorkSlice(student_ids=("id07",)).describe() == "student id07"
    assert (
        WorkSlice(student_ids=tuple(f"id{n:02d}" for n in range(40))).describe()
        == "40 students"
    )


# --------------------------------------------------------------------------- #
# Conflicts and ownership
# --------------------------------------------------------------------------- #


def test_a_conflict_is_another_actors_overlapping_claim() -> None:
    held = [_claim("alice", question_ids=("q1", "q2"))]
    assert find_conflicts(
        WorkSlice(question_ids=("q2",)), held, actor="bob"
    ) == held


def test_overlapping_your_own_claim_is_not_a_conflict() -> None:
    # Re-claiming or widening your own slice is not a double-booking.
    held = [_claim("alice", question_ids=("q1", "q2"))]
    assert find_conflicts(WorkSlice(question_ids=("q2",)), held, actor="alice") == []


def test_a_released_claim_frees_its_work() -> None:
    held = [
        WorkClaim(
            claim_id="cl-1",
            actor="alice",
            work=WorkSlice(question_ids=("q1",)),
            status="released",
        )
    ]
    assert find_conflicts(WorkSlice(question_ids=("q1",)), held, actor="bob") == []


def test_claim_for_finds_the_owner_of_one_answer() -> None:
    held = [_claim("alice", question_ids=("q1",)), _claim("bob", question_ids=("q2",))]
    assert claim_for(held, "q2", "s-a").actor == "bob"
    assert claim_for(held, "q4", "s-a") is None


def test_unclaimed_work_belongs_to_nobody() -> None:
    """The property that keeps an undivided assignment working exactly as before.

    A folder with no claims — or the remainder of a partial division — must not
    read as "someone else's", or a lone grader would be locked out of their own
    assignment the moment anyone claimed anything.
    """
    held = [_claim("alice", question_ids=("q1",))]
    assert not is_owned_by_other(held, "q4", "s-a", actor="bob")
    assert not is_owned_by_other([], "q1", "s-a", actor="bob")
    assert is_owned_by_other(held, "q1", "s-a", actor="bob")
    assert not is_owned_by_other(held, "q1", "s-a", actor="alice")


# --------------------------------------------------------------------------- #
# Dividing the pile
# --------------------------------------------------------------------------- #


def test_divide_by_question_gives_contiguous_runs() -> None:
    shares = divide_work(["alice", "bob"], question_ids=QUESTIONS, axis="question")
    assert [actor for actor, _ in shares] == ["alice", "bob"]
    assert shares[0][1] == WorkSlice(question_ids=("q1", "q2"))
    assert shares[1][1] == WorkSlice(question_ids=("q3", "q4"))


def test_divide_by_student_cuts_the_other_axis() -> None:
    shares = divide_work(["alice", "bob"], student_ids=STUDENTS, axis="student")
    assert shares[0][1] == WorkSlice(student_ids=("s-a", "s-b"))
    assert shares[1][1] == WorkSlice(student_ids=("s-c",))


def test_the_remainder_goes_to_the_earliest_graders() -> None:
    shares = divide_work(["a", "b", "c"], question_ids=QUESTIONS)
    assert [len(work.question_ids) for _actor, work in shares] == [2, 1, 1]


def test_a_grader_with_no_work_is_dropped_not_given_everything() -> None:
    """The dangerous case: an empty slice means *all* work, not none.

    Five graders over three questions must leave two graders with no claim at
    all — emitting an empty ``WorkSlice`` for them would hand each of them the
    entire assignment.
    """
    shares = divide_work(["a", "b", "c", "d", "e"], question_ids=["q1", "q2", "q3"])
    assert [actor for actor, _ in shares] == ["a", "b", "c"]
    assert all(not work.is_everything for _actor, work in shares)


def test_a_full_division_leaves_no_overlaps() -> None:
    shares = divide_work(["a", "b", "c"], question_ids=QUESTIONS)
    for index, (_actor, first) in enumerate(shares):
        for _other, second in shares[index + 1 :]:
            assert not first.overlaps(second)


def test_dividing_needs_graders_and_work() -> None:
    with pytest.raises(ValueError, match="at least one grader"):
        divide_work([], question_ids=QUESTIONS)
    with pytest.raises(ValueError, match="no questions to divide"):
        divide_work(["a"], question_ids=[])


# --------------------------------------------------------------------------- #
# The append-only store
# --------------------------------------------------------------------------- #


def test_a_claim_round_trips_through_the_log(tmp_path: Path) -> None:
    claim = claim_work(
        tmp_path, WorkSlice(question_ids=("q1", "q2")), actor="alice", note="morning"
    )
    (stored,) = read_claims(tmp_path)
    assert stored.claim_id == claim.claim_id
    assert stored.actor == "alice"
    assert stored.work == WorkSlice(question_ids=("q1", "q2"))
    assert stored.note == "morning"
    assert stored.is_active


def test_the_log_lives_in_the_dot_dir(tmp_path: Path) -> None:
    claim_work(tmp_path, WorkSlice(question_ids=("q1",)), actor="alice")
    path = resolve_claims_path(tmp_path)
    assert path == tmp_path / ".opengrader" / "claims.jsonl"
    assert path.exists()


def test_releasing_appends_rather_than_rewrites(tmp_path: Path) -> None:
    claim = claim_work(tmp_path, WorkSlice(question_ids=("q1",)), actor="alice")
    release_claim(tmp_path, claim.claim_id, actor="alice")

    # Two lines on disk, one claim after the fold — that is what makes several
    # graders writing at once safe.
    lines = resolve_claims_path(tmp_path).read_text().strip().splitlines()
    assert len(lines) == 2
    (stored,) = read_claims(tmp_path)
    assert stored.status == "released"
    assert active_claims(tmp_path) == []


def test_completing_is_distinct_from_releasing(tmp_path: Path) -> None:
    # "I finished my forty" and "I'm handing mine back" are different answers to
    # "is this assignment graded yet".
    claim = claim_work(tmp_path, WorkSlice(question_ids=("q1",)), actor="alice")
    complete_claim(tmp_path, claim.claim_id)
    (stored,) = read_claims(tmp_path)
    assert stored.status == "completed"


def test_closing_an_unknown_claim_returns_none(tmp_path: Path) -> None:
    assert release_claim(tmp_path, "cl-nope") is None
    assert complete_claim(tmp_path, "cl-nope") is None


def test_claiming_taken_work_is_refused(tmp_path: Path) -> None:
    claim_work(tmp_path, WorkSlice(question_ids=("q1", "q2")), actor="alice")
    with pytest.raises(ClaimConflictError) as conflict:
        claim_work(tmp_path, WorkSlice(question_ids=("q2", "q3")), actor="bob")

    assert conflict.value.conflicts[0].actor == "alice"
    assert "alice" in str(conflict.value)
    # The refusal must not have written anything.
    assert len(read_claims(tmp_path)) == 1


def test_force_allows_a_deliberate_overlap(tmp_path: Path) -> None:
    claim_work(tmp_path, WorkSlice(question_ids=("q1",)), actor="alice")
    claim_work(
        tmp_path, WorkSlice(question_ids=("q1",)), actor="bob", allow_overlap=True
    )
    assert len(active_claims(tmp_path)) == 2


def test_released_work_can_be_reclaimed(tmp_path: Path) -> None:
    claim = claim_work(tmp_path, WorkSlice(question_ids=("q1",)), actor="alice")
    release_claim(tmp_path, claim.claim_id)
    reclaimed = claim_work(tmp_path, WorkSlice(question_ids=("q1",)), actor="bob")
    assert reclaimed.actor == "bob"


def test_claiming_everything_needs_saying_so(tmp_path: Path) -> None:
    # Guards against a caller that computed an empty slice by accident.
    with pytest.raises(ValueError, match="entire assignment"):
        claim_work(tmp_path, WorkSlice(), actor="alice")
    claim = claim_work(tmp_path, WorkSlice(), actor="alice", allow_everything=True)
    assert claim.work.is_everything


def test_an_absent_log_is_simply_no_claims(tmp_path: Path) -> None:
    assert read_claims(tmp_path) == []
    assert active_claims(tmp_path) == []


def test_a_truncated_line_is_skipped_not_fatal(tmp_path: Path) -> None:
    claim_work(tmp_path, WorkSlice(question_ids=("q1",)), actor="alice")
    path = resolve_claims_path(tmp_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"claim_id": "cl-broken", "actor":\n')  # killed mid-write
    claim_work(tmp_path, WorkSlice(question_ids=("q2",)), actor="bob")

    assert [claim.actor for claim in read_claims(tmp_path)] == ["alice", "bob"]


# --------------------------------------------------------------------------- #
# Coverage
# --------------------------------------------------------------------------- #


def test_coverage_counts_answers_not_claims() -> None:
    # alice holds q1+q2 for all 3 students = 6 of the 12 answers.
    held = [_claim("alice", question_ids=("q1", "q2"))]
    report = coverage_report(held, question_ids=QUESTIONS, student_ids=STUDENTS)
    assert report.total_cells == 12
    assert report.claimed_cells == 6
    assert report.unclaimed_cells == 6
    assert report.cells_by_actor == {"alice": 6}
    assert not report.is_fully_claimed


def test_a_complete_division_reports_full_coverage_and_no_overlap() -> None:
    shares = divide_work(["alice", "bob"], question_ids=QUESTIONS)
    held = [
        WorkClaim(claim_id=f"cl-{actor}", actor=actor, work=work, status="active")
        for actor, work in shares
    ]
    report = coverage_report(held, question_ids=QUESTIONS, student_ids=STUDENTS)
    assert report.is_fully_claimed
    assert not report.has_overlaps
    assert report.cells_by_actor == {"alice": 6, "bob": 6}


def test_coverage_names_double_booked_work() -> None:
    held = [
        _claim("alice", question_ids=("q1", "q2")),
        _claim("bob", question_ids=("q2", "q3")),
    ]
    report = coverage_report(held, question_ids=QUESTIONS, student_ids=STUDENTS)
    (overlap,) = report.overlaps
    assert {overlap.first.actor, overlap.second.actor} == {"alice", "bob"}
    # q2 for each of the three students.
    assert overlap.shared_cells == 3


def test_two_graders_on_different_students_is_not_an_overlap() -> None:
    held = [
        _claim("alice", question_ids=("q1",), student_ids=("s-a",)),
        _claim("bob", question_ids=("q1",), student_ids=("s-b",)),
    ]
    report = coverage_report(held, question_ids=QUESTIONS, student_ids=STUDENTS)
    assert not report.has_overlaps
