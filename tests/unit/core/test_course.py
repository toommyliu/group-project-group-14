"""Tests for the course-level gradebook (course.toml → weighted grades).

Builds real assignment folders on disk, because the whole point of the module is
that it reads ordinary assignment folders and writes nothing into them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opengrader.core.course import (
    CourseConfigError,
    build_course_report,
    letter_for,
    load_course_config,
)

QA = """---
title: {title}
---

- (10 points) Explain.
  - **Correct**
    - x
  - **Outcome** CLO1
- (10 points) Apply.
  - **Correct**
    - y
  - **Outcome** CLO2
"""


def _submission(student_id: str, name: str, marks: dict[str, float | None]) -> str:
    blocks = ""
    for question_id, awarded in marks.items():
        blocks += (
            f"- {question_id}:\n"
            "  - Question type: Free Response\n"
            "  - Prompt: p\n"
            "  - Response text: answer\n"
            "  - Possible points: 10\n"
            f"  - Points: {'' if awarded is None else f'{awarded:g}'}\n"
            "  - Rubric:\n  - Analysis:\n\n"
        )
    return (
        "---\n"
        f'student_name: "{name}"\n'
        f'student_id: "{student_id}"\n'
        "course_code: CS146\n"
        'assignment_title: "A"\n'
        "assignment_version: null\n"
        f'source_pdf: "{student_id}.pdf"\n'
        'score: ""\n'
        "---\n\n\n" + blocks
    )


def _assignment(
    root: Path,
    folder: str,
    students: dict[str, tuple[str, dict[str, float | None]]],
    *,
    solutions: bool = True,
) -> Path:
    directory = root / folder / "student-submissions"
    directory.mkdir(parents=True)
    if solutions:
        (root / folder / "solutions.md").write_text(QA.format(title=folder))
    for student_id, (name, marks) in students.items():
        (directory / f"{student_id}.submission.md").write_text(
            _submission(student_id, name, marks)
        )
    return directory


def _course(root: Path, body: str) -> Path:
    path = root / "course.toml"
    path.write_text(body)
    return path


@pytest.fixture
def two_assignment_course(tmp_path: Path) -> Path:
    _assignment(
        tmp_path,
        "quiz",
        {
            "101": ("Ana", {"q1": 10, "q2": 8}),  # 90%
            "102": ("Ben", {"q1": 5, "q2": 5}),  # 50%
        },
    )
    _assignment(
        tmp_path,
        "final",
        {
            "101": ("Ana", {"q1": 8, "q2": 8}),  # 80%
            "102": ("Ben", {"q1": 6, "q2": 6}),  # 60%
        },
    )
    return _course(
        tmp_path,
        """
name = "CS 146"

[[assignment]]
name = "Quiz"
path = "quiz/student-submissions"
weight = 25
solutions = "quiz/solutions.md"

[[assignment]]
name = "Final"
path = "final/student-submissions"
weight = 75
solutions = "final/solutions.md"
""",
    )


# ── the config file ──────────────────────────────────────────────────────────


def test_relative_paths_resolve_against_the_course_file(two_assignment_course: Path) -> None:
    """So a course folder can be moved or shared without editing it."""
    config = load_course_config(two_assignment_course)
    assert config.assignments[0].path.is_dir()
    assert config.assignments[0].solutions is not None


def test_a_directory_argument_finds_the_course_file(two_assignment_course: Path) -> None:
    config = load_course_config(two_assignment_course.parent)
    assert config.name == "CS 146"


def test_a_missing_file_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(CourseConfigError, match="course file not found"):
        load_course_config(tmp_path / "nope.toml")


def test_a_course_with_no_assignments_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CourseConfigError, match="at least one"):
        load_course_config(_course(tmp_path, 'name = "X"\n'))


def test_a_duplicate_assignment_name_is_refused(tmp_path: Path) -> None:
    """Names key every per-assignment column, so a duplicate would silently
    overwrite one assignment with another."""
    body = (
        'name = "X"\n'
        '[[assignment]]\nname = "A"\npath = "a"\nweight = 1\n'
        '[[assignment]]\nname = "A"\npath = "b"\nweight = 1\n'
    )
    with pytest.raises(CourseConfigError, match="duplicate assignment name"):
        load_course_config(_course(tmp_path, body))


def test_a_bad_missing_policy_is_refused(tmp_path: Path) -> None:
    body = (
        'name = "X"\n'
        '[[assignment]]\nname = "A"\npath = "a"\nweight = 1\nmissing = "guess"\n'
    )
    with pytest.raises(CourseConfigError, match="missing must be one of"):
        load_course_config(_course(tmp_path, body))


def test_invalid_toml_names_the_file(tmp_path: Path) -> None:
    with pytest.raises(CourseConfigError, match="not valid TOML"):
        load_course_config(_course(tmp_path, "name = [unclosed\n"))


# ── weighting ────────────────────────────────────────────────────────────────


def test_weighted_grades_use_the_declared_weights(two_assignment_course: Path) -> None:
    report = build_course_report(load_course_config(two_assignment_course))
    by_id = {s.student_id: s for s in report.students}
    # Ana: 0.25*90 + 0.75*80 = 82.5
    assert by_id["101"].weighted_percent == pytest.approx(82.5)
    # Ben: 0.25*50 + 0.75*60 = 57.5
    assert by_id["102"].weighted_percent == pytest.approx(57.5)


def test_weights_are_relative_not_percentages(tmp_path: Path) -> None:
    """1/3 must mean the same as 25/75."""
    _assignment(tmp_path, "quiz", {"101": ("Ana", {"q1": 10, "q2": 8})})
    _assignment(tmp_path, "final", {"101": ("Ana", {"q1": 8, "q2": 8})})
    path = _course(
        tmp_path,
        'name = "X"\n'
        '[[assignment]]\nname = "Quiz"\npath = "quiz/student-submissions"\nweight = 1\n'
        '[[assignment]]\nname = "Final"\npath = "final/student-submissions"\nweight = 3\n',
    )
    report = build_course_report(load_course_config(path))
    assert report.students[0].weighted_percent == pytest.approx(82.5)


def test_letter_grades_come_from_the_scale() -> None:
    scale = (("A", 90.0), ("B", 80.0), ("C", 70.0), ("F", 0.0))
    assert letter_for(95, scale) == "A"
    assert letter_for(80, scale) == "B"  # the boundary counts
    assert letter_for(12, scale) == "F"
    assert letter_for(None, scale) == "-"


def test_a_custom_grade_scale_is_honoured(tmp_path: Path) -> None:
    _assignment(tmp_path, "quiz", {"101": ("Ana", {"q1": 8, "q2": 8})})  # 80%
    path = _course(
        tmp_path,
        'name = "X"\n'
        '[[assignment]]\nname = "Quiz"\npath = "quiz/student-submissions"\nweight = 1\n'
        "[grades]\nExcellent = 95\nPass = 60\n",
    )
    report = build_course_report(load_course_config(path))
    assert report.students[0].letter == "Pass"


# ── missing work ─────────────────────────────────────────────────────────────


def test_missing_work_is_skipped_by_default(tmp_path: Path) -> None:
    """Mid-semester, no submission means "not marked yet" far more often than
    "scored nothing" — averaging in zeros invents failing students."""
    _assignment(tmp_path, "quiz", {"101": ("Ana", {"q1": 10, "q2": 8})})
    _assignment(tmp_path, "final", {})
    (tmp_path / "final" / "student-submissions" / "x.submission.md").write_text(
        _submission("999", "Other", {"q1": 5, "q2": 5})
    )
    path = _course(
        tmp_path,
        'name = "X"\n'
        '[[assignment]]\nname = "Quiz"\npath = "quiz/student-submissions"\nweight = 25\n'
        '[[assignment]]\nname = "Final"\npath = "final/student-submissions"\nweight = 75\n',
    )
    report = build_course_report(load_course_config(path))
    ana = next(s for s in report.students if s.student_id == "101")
    assert ana.weighted_percent == pytest.approx(90.0)  # her quiz alone
    assert ana.missing == ("Final",)
    assert not ana.is_complete


def test_missing_zero_counts_the_weight_against_the_student(tmp_path: Path) -> None:
    _assignment(tmp_path, "quiz", {"101": ("Ana", {"q1": 10, "q2": 8})})
    (tmp_path / "final" / "student-submissions").mkdir(parents=True)
    (tmp_path / "final" / "student-submissions" / "x.submission.md").write_text(
        _submission("999", "Other", {"q1": 5, "q2": 5})
    )
    path = _course(
        tmp_path,
        'name = "X"\n'
        '[[assignment]]\nname = "Quiz"\npath = "quiz/student-submissions"\nweight = 25\n'
        '[[assignment]]\nname = "Final"\npath = "final/student-submissions"\n'
        'weight = 75\nmissing = "zero"\n',
    )
    report = build_course_report(load_course_config(path))
    ana = next(s for s in report.students if s.student_id == "101")
    # 0.25*90 + 0.75*0 = 22.5
    assert ana.weighted_percent == pytest.approx(22.5)


def test_an_ungraded_question_does_not_drag_the_assignment_down(tmp_path: Path) -> None:
    _assignment(tmp_path, "quiz", {"101": ("Ana", {"q1": 9, "q2": None})})
    path = _course(
        tmp_path,
        'name = "X"\n'
        '[[assignment]]\nname = "Quiz"\npath = "quiz/student-submissions"\nweight = 1\n',
    )
    report = build_course_report(load_course_config(path))
    assert report.students[0].weighted_percent == pytest.approx(90.0)


# ── at risk and trend ────────────────────────────────────────────────────────


def test_at_risk_lists_the_low_scorers_lowest_first(two_assignment_course: Path) -> None:
    report = build_course_report(load_course_config(two_assignment_course))
    assert [s.student_id for s in report.at_risk] == ["102"]


def test_a_student_with_no_graded_work_is_not_at_risk(tmp_path: Path) -> None:
    """There is no evidence they are struggling, and burying real cases under an
    ungraded class is how an at-risk list gets ignored."""
    _assignment(tmp_path, "quiz", {"101": ("Ana", {"q1": None, "q2": None})})
    path = _course(
        tmp_path,
        'name = "X"\n'
        '[[assignment]]\nname = "Quiz"\npath = "quiz/student-submissions"\nweight = 1\n',
    )
    report = build_course_report(load_course_config(path))
    assert report.students[0].weighted_percent is None
    assert report.at_risk == ()


def test_trend_reports_the_per_assignment_percentages(two_assignment_course: Path) -> None:
    report = build_course_report(load_course_config(two_assignment_course))
    ana = next(s for s in report.students if s.student_id == "101")
    assert ana.trend() == (("Quiz", 90.0), ("Final", 80.0))
    assert ana.direction() == pytest.approx(-10.0)  # 90 -> 80


def test_direction_needs_two_graded_assignments(tmp_path: Path) -> None:
    _assignment(tmp_path, "quiz", {"101": ("Ana", {"q1": 9, "q2": 9})})
    path = _course(
        tmp_path,
        'name = "X"\n'
        '[[assignment]]\nname = "Quiz"\npath = "quiz/student-submissions"\nweight = 1\n',
    )
    report = build_course_report(load_course_config(path))
    assert report.students[0].direction() is None


# ── students across assignments ──────────────────────────────────────────────


def test_students_are_matched_by_id_across_folders(two_assignment_course: Path) -> None:
    report = build_course_report(load_course_config(two_assignment_course))
    assert len(report.students) == 2
    assert all(len(s.scores) == 2 for s in report.students)


def test_a_student_present_in_only_one_assignment_still_appears(tmp_path: Path) -> None:
    _assignment(tmp_path, "quiz", {"101": ("Ana", {"q1": 9, "q2": 9})})
    _assignment(tmp_path, "final", {"102": ("Ben", {"q1": 5, "q2": 5})})
    path = _course(
        tmp_path,
        'name = "X"\n'
        '[[assignment]]\nname = "Quiz"\npath = "quiz/student-submissions"\nweight = 1\n'
        '[[assignment]]\nname = "Final"\npath = "final/student-submissions"\nweight = 1\n',
    )
    report = build_course_report(load_course_config(path))
    assert {s.student_id for s in report.students} == {"101", "102"}


# ── failures degrade, they don't destroy ─────────────────────────────────────


def test_an_unreadable_assignment_is_a_problem_not_a_crash(tmp_path: Path) -> None:
    """One bad folder in a twelve-assignment course must not destroy the report
    a week before grades are due."""
    _assignment(tmp_path, "quiz", {"101": ("Ana", {"q1": 9, "q2": 9})})
    path = _course(
        tmp_path,
        'name = "X"\n'
        '[[assignment]]\nname = "Quiz"\npath = "quiz/student-submissions"\nweight = 1\n'
        '[[assignment]]\nname = "Gone"\npath = "nowhere"\nweight = 1\n',
    )
    report = build_course_report(load_course_config(path))
    assert len(report.problems) == 1
    assert "Gone" in report.problems[0]
    assert report.students[0].weighted_percent == pytest.approx(90.0)


def test_the_course_report_writes_nothing(two_assignment_course: Path) -> None:
    root = two_assignment_course.parent
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    build_course_report(load_course_config(two_assignment_course))
    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before


# ── outcomes across the course (the accreditation artifact) ──────────────────


def test_outcomes_aggregate_across_assignments(two_assignment_course: Path) -> None:
    report = build_course_report(load_course_config(two_assignment_course))
    names = [o.outcome for o in report.outcomes]
    assert names == ["CLO1", "CLO2"]
    clo1 = report.outcomes[0]
    assert clo1.assignments == ("Quiz", "Final")
    # q1 is worth 10 on each of the two assignments.
    assert clo1.possible_points == 20.0
    assert clo1.students_measured == 2


def test_outcome_mastery_pools_points_rather_than_averaging_assignments(
    two_assignment_course: Path,
) -> None:
    report = build_course_report(load_course_config(two_assignment_course))
    clo1 = report.outcomes[0]
    # Ana q1: 10/10 then 8/10 -> 18/20 = 90%. Ben: 5/10 then 6/10 -> 55%.
    assert clo1.students_mastered == 1
    assert clo1.mean_mastery == pytest.approx((0.9 + 0.55) / 2)


def test_an_assignment_without_solutions_contributes_no_outcomes(tmp_path: Path) -> None:
    """The tags live in the answer key; without one there is nothing to read."""
    _assignment(tmp_path, "quiz", {"101": ("Ana", {"q1": 9, "q2": 9})}, solutions=False)
    path = _course(
        tmp_path,
        'name = "X"\n'
        '[[assignment]]\nname = "Quiz"\npath = "quiz/student-submissions"\nweight = 1\n',
    )
    report = build_course_report(load_course_config(path))
    assert report.outcomes == ()


# ── building a course file ───────────────────────────────────────────────────


def test_weight_shares_turn_relative_weights_into_percentages() -> None:
    """The whole meaning of `weight`, and what the builder shows live."""
    from opengrader.core.course import weight_shares

    assert weight_shares([1, 1, 2]) == [0.25, 0.25, 0.5]
    # The same course written as percentages.
    assert weight_shares([25, 25, 50]) == [0.25, 0.25, 0.5]
    # A weight of 0 contributes nothing and takes no share.
    assert weight_shares([1, 0, 1]) == [0.5, 0.0, 0.5]


def test_weight_shares_survive_all_zero_weights() -> None:
    """A half-typed form must not divide by zero."""
    from opengrader.core.course import weight_shares

    assert weight_shares([0, 0]) == [0.0, 0.0]
    assert weight_shares([]) == []


def test_a_written_course_file_loads_back(tmp_path: Path) -> None:
    from opengrader.core.course import CourseAssignment, write_course_toml

    _assignment(tmp_path, "quiz", {"101": ("Ana", {"q1": 9, "q2": 9})})
    written = write_course_toml(
        tmp_path / "course.toml",
        name="CS 146",
        assignments=[
            CourseAssignment(
                name="Quiz 1",
                path=tmp_path / "quiz" / "student-submissions",
                weight=3,
                solutions=tmp_path / "quiz" / "solutions.md",
            )
        ],
    )
    config = load_course_config(written)
    assert config.name == "CS 146"
    assert config.assignments[0].name == "Quiz 1"
    assert config.assignments[0].weight == 3.0
    assert config.assignments[0].path.is_dir()
    assert config.assignments[0].solutions is not None


def test_written_paths_are_relative_to_the_course_file(tmp_path: Path) -> None:
    """That relativity is what makes a course folder movable and shareable."""
    from opengrader.core.course import CourseAssignment, write_course_toml

    written = write_course_toml(
        tmp_path / "course.toml",
        name="X",
        assignments=[
            CourseAssignment(name="Q", path=tmp_path / "quiz" / "subs", weight=1)
        ],
    )
    assert 'path = "quiz/subs"' in written.read_text()


def test_a_path_outside_the_course_folder_stays_absolute(tmp_path: Path) -> None:
    """An honest absolute path beats a fragile ../../ chain."""
    from opengrader.core.course import CourseAssignment, render_course_toml

    outside = Path("/somewhere/else/subs")
    text = render_course_toml(
        name="X",
        assignments=[CourseAssignment(name="Q", path=outside, weight=1)],
        relative_to=tmp_path,
    )
    assert 'path = "/somewhere/else/subs"' in text


def test_the_written_file_explains_what_weight_means(tmp_path: Path) -> None:
    """It is hand-edited afterwards; the next reader needs the rule in the file."""
    from opengrader.core.course import CourseAssignment, render_course_toml

    text = render_course_toml(
        name="X",
        assignments=[CourseAssignment(name="Q", path=Path("subs"), weight=1)],
    )
    assert "weight = how much an assignment counts" in text
    assert "relative" in text


def test_shares_are_written_as_comments_not_keys(tmp_path: Path) -> None:
    """They are derived from the weights and would go stale on a hand edit."""
    from opengrader.core.course import CourseAssignment, render_course_toml

    text = render_course_toml(
        name="X",
        assignments=[
            CourseAssignment(name="A", path=Path("a"), weight=1),
            CourseAssignment(name="B", path=Path("b"), weight=3),
        ],
    )
    assert "weight = 1  # 25% of the grade" in text
    assert "weight = 3  # 75% of the grade" in text


def test_quotes_in_names_and_paths_are_escaped(tmp_path: Path) -> None:
    from opengrader.core.course import CourseAssignment, load_course_config, write_course_toml

    written = write_course_toml(
        tmp_path / "course.toml",
        name='The "Real" Course',
        assignments=[
            CourseAssignment(name='Quiz "1"', path=tmp_path / "subs", weight=1)
        ],
    )
    config = load_course_config(written)
    assert config.name == 'The "Real" Course'
    assert config.assignments[0].name == 'Quiz "1"'


# ── discovering assignments ──────────────────────────────────────────────────


def test_discovery_finds_every_assignment_folder(tmp_path: Path) -> None:
    from opengrader.core.course import discover_course_assignments

    _assignment(tmp_path / "quizzes" / "quiz-1", "", {"1": ("A", {"q1": 1})})
    _assignment(tmp_path / "exams" / "midterm-2", "", {"1": ("A", {"q1": 1})})
    found = discover_course_assignments(tmp_path)
    assert [f.name for f in found] == ["Midterm 2", "Quiz 1"]
    assert all(f.solutions is not None for f in found)


def test_discovery_names_come_from_the_parent_of_a_generic_folder(tmp_path: Path) -> None:
    """`midterm/student-submissions` is the midterm, not a thing called
    "student submissions"."""
    from opengrader.core.course import humanize_assignment_name

    assert humanize_assignment_name(Path("/x/midterm-2/student-submissions")) == "Midterm 2"
    assert humanize_assignment_name(Path("/x/final_exam/submissions")) == "Final Exam"
    # A non-generic folder keeps its own name, and existing casing is preserved.
    assert humanize_assignment_name(Path("/x/HW2b")) == "HW2b"


def test_discovery_does_not_report_a_parent_and_its_child(tmp_path: Path) -> None:
    _assignment(tmp_path / "quiz", "", {"1": ("A", {"q1": 1})})
    from opengrader.core.course import discover_course_assignments

    assert len(discover_course_assignments(tmp_path)) == 1


def test_a_submission_file_is_never_mistaken_for_an_answer_key(tmp_path: Path) -> None:
    """Student submissions are markdown too — the lone-.md guess must skip them."""
    from opengrader.core.course import find_solutions_near

    subs = tmp_path / "subs"
    subs.mkdir()
    (subs / "s1.submission.md").write_text("x")
    assert find_solutions_near(subs) is None


def test_an_answer_key_beside_the_submissions_is_found(tmp_path: Path) -> None:
    from opengrader.core.course import find_solutions_near

    subs = tmp_path / "quiz" / "student-submissions"
    subs.mkdir(parents=True)
    key = tmp_path / "quiz" / "solutions.md"
    key.write_text("x")
    assert find_solutions_near(subs) == key


def test_discovery_of_a_missing_folder_is_empty_not_an_error(tmp_path: Path) -> None:
    from opengrader.core.course import discover_course_assignments

    assert discover_course_assignments(tmp_path / "nope") == []
