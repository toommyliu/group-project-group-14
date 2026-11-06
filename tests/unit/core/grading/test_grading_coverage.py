from __future__ import annotations

import shutil
from pathlib import Path

from opengrader.core.grading.automated import grading_coverage
from opengrader.core.submissions.student_file import (
    StudentSubmissionGradeUpdate,
    update_student_submission_markdown,
)

REPO = Path(__file__).resolve().parents[4]
SAMPLE = REPO / "samples" / "cs146-quiz1" / "student-submissions"


def _update(path: Path, update: StudentSubmissionGradeUpdate) -> None:
    path.write_text(
        update_student_submission_markdown(path.read_text(encoding="utf-8"), update),
        encoding="utf-8",
    )


def test_coverage_tells_scored_from_read_and_from_never_read(tmp_path) -> None:
    folder = tmp_path / "submissions"
    shutil.copytree(SAMPLE, folder, ignore=shutil.ignore_patterns("._*"))
    first = sorted(folder.rglob("*.submission.md"))[0]

    before = grading_coverage(folder)
    # The sample's handwriting was never read: nothing to score.
    assert before.scored == 0 and before.unread == before.total > 0

    _update(first, StudentSubmissionGradeUpdate(question_id="q1", possible_points=2, points_awarded=2))
    _update(first, StudentSubmissionGradeUpdate(question_id="q2", response_text="a stack"))

    after = grading_coverage(folder)
    assert after.total == before.total
    assert after.scored == 1
    assert after.unread == before.total - 2  # q1 scored, q2 read but unscored
    assert after.left == before.total - 1


def test_an_empty_or_missing_folder_has_nothing(tmp_path) -> None:
    assert grading_coverage(tmp_path / "nowhere").total == 0
