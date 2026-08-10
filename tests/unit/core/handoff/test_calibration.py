"""Calibration sets: the same sample, to every grader, blind.

The load-bearing assertions here are the ones about what a grader can *see*: a
packet that shows someone else's marks, or that shares an alias with another
grader's copy, would anchor exactly the independent judgement a norming session
exists to measure.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from opengrader.core.analytics.grader_agreement import (
    answers_from_graded_copies,
    build_grader_agreement_report,
)
from opengrader.core.handoff.calibration import (
    CalibrationError,
    build_calibration_set,
    collect_calibration_gradings,
    find_calibration_set,
    list_calibration_sets,
    parse_calibration_set,
    render_calibration_set,
    resolve_calibration_dir,
    sample_student_keys,
)
from opengrader.core.handoff.packet import PACKET_SUBMISSIONS_DIRNAME
from opengrader.core.submissions.student_file import (
    StudentSubmissionGradeUpdate,
    load_student_submission_markdown,
    update_student_submission_file,
)

_STAMP = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def _submission(name: str, student_id: str, *, points: str = "") -> str:
    return f"""---
student_name: "{name}"
student_id: "{student_id}"
course_code: "CS146"
assignment_title: "Quiz 1"
assignment_version: "A"
source_pdf: "../../split/{student_id}.pdf"
student_score: ""
---


- q1:
  - Question type: Free Response
  - Prompt: What is a loop?
  - Response image: ![q1 response](responses/q1.png)
  - Response text: a block that repeats until a condition stops holding
  - Possible points: 10
  - Points: {points}
  - Rubric:{" Partially Correct" if points else ""}
  - Analysis:
"""


@pytest.fixture()
def cohort(tmp_path: Path) -> Path:
    """A six-student cohort, ungraded, each with a response crop."""
    root = tmp_path / "subs"
    for index in range(1, 7):
        student = root / f"sub-{index:04d}"
        (student / "responses").mkdir(parents=True)
        (student / f"sub-{index:04d}.submission.md").write_text(
            _submission(f"Student {index}", f"10000{index}")
        )
        (student / "responses" / "q1.png").write_bytes(b"crop-bytes")
    return root


def _packets_dir(tmp_path: Path) -> Path:
    return tmp_path / "calibration-packets"


# ── sampling ──────────────────────────────────────────────────────────────


def test_a_sample_is_reproducible_from_its_seed():
    keys = [f"student-{index}" for index in range(50)]

    first = sample_student_keys(keys, size=8, seed=1234)
    second = sample_student_keys(keys, size=8, seed=1234)

    assert first == second
    assert len(first) == 8


def test_a_sample_does_not_depend_on_the_order_submissions_were_listed():
    """Re-cutting a lost packet must give the grader the same students back."""
    keys = [f"student-{index}" for index in range(50)]

    assert sample_student_keys(keys, size=8, seed=7) == sample_student_keys(
        list(reversed(keys)), size=8, seed=7
    )


def test_a_small_class_is_sampled_whole_rather_than_refused():
    keys = ["a", "b", "c"]

    assert sample_student_keys(keys, size=10, seed=1) == ("a", "b", "c")


def test_an_empty_or_zero_sample_is_an_error():
    with pytest.raises(CalibrationError):
        sample_student_keys([], size=4, seed=1)
    with pytest.raises(CalibrationError):
        sample_student_keys(["a"], size=0, seed=1)


# ── cutting a set ─────────────────────────────────────────────────────────


def test_every_grader_gets_a_packet_holding_the_same_students(
    cohort: Path, tmp_path: Path
):
    result = build_calibration_set(
        cohort,
        ["alice", "bob", "carol"],
        _packets_dir(tmp_path),
        sample_size=3,
        seed=42,
        now=_STAMP,
    )

    assert len(result.packets) == 3
    assert result.calibration_set.sample_size == 3
    # Same students in every packet — agreement can only be measured on
    # identical work.
    student_keys = {
        tuple(sorted(member.student_key for member in packet.key.members))
        for packet in result.packets
    }
    assert len(student_keys) == 1
    assert set(next(iter(student_keys))) == set(result.calibration_set.student_keys)


def test_each_grader_sees_different_aliases_for_the_same_student(
    cohort: Path, tmp_path: Path
):
    """Two TAs comparing notes over a shared code is the anchoring to avoid."""
    result = build_calibration_set(
        cohort, ["alice", "bob"], _packets_dir(tmp_path), sample_size=3, seed=42, now=_STAMP
    )

    alice, bob = result.packets
    aliases_by_student = {}
    for packet in (alice, bob):
        for member in packet.key.members:
            aliases_by_student.setdefault(member.student_key, set()).add(member.alias)

    assert all(len(aliases) == 2 for aliases in aliases_by_student.values())


def test_a_packet_carries_no_student_name_or_id(cohort: Path, tmp_path: Path):
    result = build_calibration_set(
        cohort, ["alice", "bob"], _packets_dir(tmp_path), sample_size=3, seed=42, now=_STAMP
    )

    for packet in result.packets:
        blob = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in packet.packet_path.rglob("*")
            if path.is_file() and path.suffix in {".md", ".json", ".txt"}
        )
        for index in range(1, 7):
            assert f"Student {index}" not in blob
            assert f"10000{index}" not in blob


def test_a_calibration_packet_is_always_handed_over_ungraded(
    cohort: Path, tmp_path: Path
):
    """The first number a grader sees is the number they agree with."""
    # Grade the whole cohort first, so the source is *not* a blank slate.
    for path in sorted(cohort.glob("*/*.submission.md")):
        update_student_submission_file(
            path,
            StudentSubmissionGradeUpdate(
                question_id="q1",
                possible_points=10.0,
                points_awarded=7.0,
                rubric_labels=("Partially Correct",),
                analysis="an existing comment",
            ),
        )

    result = build_calibration_set(
        cohort, ["alice", "bob"], _packets_dir(tmp_path), sample_size=3, seed=42, now=_STAMP
    )

    for packet in result.packets:
        root = packet.packet_path / PACKET_SUBMISSIONS_DIRNAME
        for path in sorted(root.glob("*/*.submission.md")):
            document = load_student_submission_markdown(path)
            for question in document.questions:
                assert question.points_awarded is None
                assert question.rubric_labels == ()
                assert question.analysis == ""
            assert "an existing comment" not in path.read_text(encoding="utf-8")

    # The teacher's own folder is untouched.
    for path in sorted(cohort.glob("*/*.submission.md")):
        document = load_student_submission_markdown(path)
        assert document.questions[0].points_awarded == 7.0


def test_a_set_needs_at_least_two_graders(cohort: Path, tmp_path: Path):
    with pytest.raises(CalibrationError, match="at least two graders"):
        build_calibration_set(cohort, ["alice"], _packets_dir(tmp_path), now=_STAMP)


def test_duplicate_grader_names_are_refused(cohort: Path, tmp_path: Path):
    with pytest.raises(CalibrationError, match="duplicate grader"):
        build_calibration_set(
            cohort, ["alice", "alice"], _packets_dir(tmp_path), now=_STAMP
        )


def test_reusing_a_set_name_is_refused_rather_than_overwriting(
    cohort: Path, tmp_path: Path
):
    build_calibration_set(
        cohort,
        ["alice", "bob"],
        _packets_dir(tmp_path),
        sample_size=2,
        set_id="norming",
        now=_STAMP,
    )

    with pytest.raises(CalibrationError, match="already exists"):
        build_calibration_set(
            cohort,
            ["carol", "dave"],
            _packets_dir(tmp_path) / "second",
            sample_size=2,
            set_id="norming",
            now=_STAMP,
        )


# ── the record ────────────────────────────────────────────────────────────


def test_a_set_record_round_trips(cohort: Path, tmp_path: Path):
    result = build_calibration_set(
        cohort, ["alice", "bob"], _packets_dir(tmp_path), sample_size=3, seed=9, now=_STAMP
    )

    reparsed = parse_calibration_set(render_calibration_set(result.calibration_set))

    assert reparsed == result.calibration_set


def test_sets_are_listed_and_found_by_id(cohort: Path, tmp_path: Path):
    build_calibration_set(
        cohort,
        ["alice", "bob"],
        _packets_dir(tmp_path),
        sample_size=2,
        set_id="first",
        now=_STAMP,
    )

    assert resolve_calibration_dir(cohort).is_dir()
    assert [record.set_id for record in list_calibration_sets(cohort)] == ["first"]
    assert find_calibration_set(cohort, "first") is not None
    assert find_calibration_set(cohort, "nope") is None
    # No id asks for the most recent session.
    assert find_calibration_set(cohort).set_id == "first"


def test_an_unreadable_record_is_skipped_not_raised(cohort: Path, tmp_path: Path):
    build_calibration_set(
        cohort, ["alice", "bob"], _packets_dir(tmp_path), sample_size=2, now=_STAMP
    )
    (resolve_calibration_dir(cohort) / "damaged.json").write_text("{not json")

    # One damaged file must not hide the sessions that are fine.
    assert len(list_calibration_sets(cohort)) == 1


# ── collecting the round trip ─────────────────────────────────────────────


def _grade_packet(packet_path: Path, points: float) -> None:
    """Mark every answer in a returned packet, as a grader would in the TUI."""
    root = packet_path / PACKET_SUBMISSIONS_DIRNAME
    for path in sorted(root.glob("*/*.submission.md")):
        document = load_student_submission_markdown(path)
        for question in document.questions:
            update_student_submission_file(
                path,
                StudentSubmissionGradeUpdate(
                    question_id=question.question_id,
                    possible_points=question.possible_points,
                    points_awarded=points,
                    rubric_labels=("Partially Correct",),
                ),
            )


def test_returned_packets_are_de_aliased_back_to_real_students(
    cohort: Path, tmp_path: Path
):
    result = build_calibration_set(
        cohort, ["alice", "bob"], _packets_dir(tmp_path), sample_size=3, seed=42, now=_STAMP
    )
    _grade_packet(result.packets[0].packet_path, 9.0)
    _grade_packet(result.packets[1].packet_path, 5.0)

    collection = collect_calibration_gradings(cohort, result.calibration_set)

    assert collection.returned == 2
    assert collection.missing == ()
    sampled = set(result.calibration_set.student_keys)
    for grading in collection.gradings:
        # Keyed by the teacher's student keys, not the packet's aliases.
        assert set(grading.documents) == sampled
        assert grading.untranslated == ()
        assert grading.graded_answers == 3


def test_an_ungraded_or_missing_packet_is_reported_not_silently_empty(
    cohort: Path, tmp_path: Path
):
    result = build_calibration_set(
        cohort,
        ["alice", "bob", "carol"],
        _packets_dir(tmp_path),
        sample_size=2,
        seed=42,
        now=_STAMP,
    )
    _grade_packet(result.packets[0].packet_path, 8.0)
    # bob has not started; carol's packet is gone entirely.
    import shutil

    shutil.rmtree(result.packets[2].packet_path)

    collection = collect_calibration_gradings(cohort, result.calibration_set)

    assert collection.returned == 1
    reasons = dict(collection.missing)
    assert "no graded answers yet" in reasons["bob"]
    assert "packet not found" in reasons["carol"]


def test_a_returned_packet_without_its_key_is_reported_loudly(
    cohort: Path, tmp_path: Path
):
    """With no key every alias fails to match, which must not read as 'did nothing'."""
    result = build_calibration_set(
        cohort, ["alice", "bob"], _packets_dir(tmp_path), sample_size=2, seed=42, now=_STAMP
    )
    _grade_packet(result.packets[0].packet_path, 8.0)
    result.packets[0].key_path.unlink()

    collection = collect_calibration_gradings(cohort, result.calibration_set)

    reasons = dict(collection.missing)
    assert "handoff key not found" in reasons["alice"]


def test_a_packet_handed_back_somewhere_else_can_be_pointed_at(
    cohort: Path, tmp_path: Path
):
    result = build_calibration_set(
        cohort, ["alice", "bob"], _packets_dir(tmp_path), sample_size=2, seed=42, now=_STAMP
    )
    _grade_packet(result.packets[0].packet_path, 8.0)
    moved = tmp_path / "from-alices-usb-stick"
    result.packets[0].packet_path.rename(moved)

    collection = collect_calibration_gradings(
        cohort, result.calibration_set, packet_paths={"alice": moved}
    )

    assert [grading.grader for grading in collection.gradings] == ["alice"]


def test_the_whole_norming_round_trip_measures_disagreement(
    cohort: Path, tmp_path: Path
):
    """Cut, grade differently, collect, and read the leniency out the far end."""
    result = build_calibration_set(
        cohort, ["alice", "bob"], _packets_dir(tmp_path), sample_size=4, seed=42, now=_STAMP
    )
    _grade_packet(result.packets[0].packet_path, 9.0)
    _grade_packet(result.packets[1].packet_path, 5.0)

    collection = collect_calibration_gradings(cohort, result.calibration_set)
    answers = answers_from_graded_copies(
        (grading.grader, grading.documents) for grading in collection.gradings
    )
    report = build_grader_agreement_report(answers, min_pair_samples=4)

    assert report.distinct_graders == 2
    assert len(report.same_work_pairs) == 1
    pair = report.same_work_pairs[0]
    assert pair.basis == "same_work"
    assert pair.samples == 4
    assert pair.sufficient is True
    assert pair.exact_agreement == 0.0
    assert pair.more_generous == "alice"
    assert pair.mean_absolute_gap == pytest.approx(40.0)
