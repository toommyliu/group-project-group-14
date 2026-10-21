"""What auto-grading remembers writing, and how that moves with the grades."""

from __future__ import annotations

from pathlib import Path

from opengrader.core.grading.ownership import AutoGrades, Owner
from opengrader.core.submissions import StudentSubmissionDocument, StudentSubmissionQuestion


def _graded(points: float) -> tuple[StudentSubmissionDocument, StudentSubmissionQuestion]:
    question = StudentSubmissionQuestion(
        question_id="q1", prompt="", possible_points=10, response_image_path=None,
        response_text="", points_awarded=points,
    )
    document = StudentSubmissionDocument(
        student_name="", student_id="", course_code=None, assignment_title="",
        assignment_version=None, source_pdf="", questions=[question],
    )
    return document, question


def _file(root: Path, stem: str) -> Path:
    path = root / stem / f"{stem}.submission.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def test_grades_that_swap_files_take_what_auto_grading_wrote_with_them(tmp_path: Path) -> None:
    first, second = _file(tmp_path, "submission-0001"), _file(tmp_path, "submission-0002")
    record = AutoGrades(tmp_path)
    record.remember(first, "q1", points=10, rubric=(), analysis="Correct")
    record.remember(second, "q1", points=0, rubric=(), analysis="")
    record.move({first: second, second: first})
    record.save()

    reloaded = AutoGrades(tmp_path)
    assert reloaded.grade_owner(second, *_graded(10)) is Owner.AUTO
    assert reloaded.grade_owner(first, *_graded(0)) is Owner.AUTO
    # 10 on the first file is no longer the auto-grader's: that was the other student.
    assert reloaded.grade_owner(first, *_graded(10)) is Owner.PERSON


def test_a_file_read_fresh_forgets_what_was_written_on_it(tmp_path: Path) -> None:
    first = _file(tmp_path, "submission-0001")
    record = AutoGrades(tmp_path)
    record.remember(first, "q1", points=10, rubric=(), analysis="Correct")
    record.move({first: None})
    # Nothing recorded and no history: a grade there now is a person's.
    assert record.grade_owner(first, *_graded(10)) is Owner.PERSON
