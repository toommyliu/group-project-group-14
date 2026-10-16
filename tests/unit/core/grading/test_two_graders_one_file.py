"""Two graders on one class folder (each student is one file): saves on
different questions both land; a save over the other's answer is refused."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from opengrader.core.folder_access import FolderAccess, check_folder_access
from opengrader.core.grading.files import AnswerChangedByOther, FileGradingSession
from opengrader.core.history import GradeJournal
from opengrader.core.submissions import load_student_submission_markdown

BLOCK = """- {qid}:
  - Question type: Free Response
  - Prompt: (10 points) {qid}
  - Response text: answer
  - Possible points: 10
  - Points:
  - Rubric:
  - Analysis:
"""


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    path = tmp_path / "submissions" / "s1" / "s1.submission.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nstudent_name: \"Ada\"\nstudent_id: \"7\"\nsource_pdf: \"x.pdf\"\n---\n\n"
        + "\n".join(BLOCK.format(qid=q) for q in ("q1", "q2"))
    )
    return tmp_path / "submissions"


def _session(folder: Path, actor: str) -> FileGradingSession:
    return FileGradingSession(
        folder / "s1" / "s1.submission.md",
        submissions_path=folder,
        journal=GradeJournal(folder / ".opengrader" / "history", actor=actor),
    )


def _grade(session: FileGradingSession, qid: str, points: float) -> None:
    session.save_grade(question_id=qid, possible_points=10, points_awarded=points, rubric_labels=())


def _bump(path: Path) -> None:
    # Coarse filesystem clocks: make sure the other save is seen as a change.
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def test_graders_on_different_questions_of_one_student_both_save(folder):
    alice, bob = _session(folder, "alice"), _session(folder, "bob")

    _grade(alice, "q1", 8)
    _bump(alice.path)
    _grade(bob, "q2", 5)  # bob loaded the file before alice's save

    saved = {q.question_id: q.points_awarded for q in load_student_submission_markdown(alice.path).questions}
    assert saved == {"q1": 8, "q2": 5}


def test_a_save_over_the_other_graders_answer_is_refused_and_names_them(folder):
    alice, bob = _session(folder, "alice"), _session(folder, "bob")

    _grade(alice, "q1", 8)
    _bump(alice.path)
    with pytest.raises(AnswerChangedByOther, match="alice changed q1") as refused:
        _grade(bob, "q1", 2)

    assert refused.value.by == "alice"
    saved = {q.question_id: q.points_awarded for q in load_student_submission_markdown(alice.path).questions}
    assert saved["q1"] == 8
    # Bob's session now holds what is on disk, so his screen shows alice's grade.
    assert bob.questions_by_id["q1"].points_awarded == 8
    # …and his next save on it goes through: he has now seen it.
    _grade(bob, "q1", 3)
    assert load_student_submission_markdown(alice.path).questions[0].points_awarded == 3


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_a_folder_this_account_cannot_write_is_reported_when_opened(folder):
    target = folder / "s1" / "s1.submission.md"
    assert check_folder_access(folder).can_grade
    target.chmod(0o444)
    try:
        access = check_folder_access(folder)
    finally:
        target.chmod(0o644)

    assert not access.can_grade
    assert target in access.unwritable
    assert "Grades given here will not be saved" in access.message()


def test_the_message_names_the_owner_when_the_files_are_someone_elses():
    access = FolderAccess(account="bob", unwritable=(Path("x"),), owners=("alice",), submissions=40)

    assert "they belong to alice" in access.message()
    assert "This account (bob)" in access.message()
