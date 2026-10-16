"""A paper's answer-key version is read, never guessed (core/grading/versions.py)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from opengrader.core.grading.automated import grade_student_file
from opengrader.core.grading.questions import questions_for_submission
from opengrader.core.grading.solutions import (
    solution_entries_for_submission,
    solution_version_for_submission,
)
from opengrader.core.grading.versions import match_version
from opengrader.core.qa_file import parse_qa_markdown
from opengrader.core.submissions import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    load_student_submission_markdown,
    render_student_submission_markdown,
    set_assignment_version_file,
)
from opengrader.core.submissions.review import ReviewReason, build_review_flags

TWO_VERSIONS = """---
title: Quiz
---

## Version A

- (10 points) What is 2 + 2?
  - **Correct**
    - 4

## Version B

- (10 points) What is 3 + 3?
  - **Correct**
    - 6
"""

ONE_VERSION = TWO_VERSIONS.split("## Version B")[0]


def _paper(version: str | None, *, answer: str = "6", prompt: str = "") -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name="Ada",
        student_id="12345",
        course_code="CS1",
        assignment_title="Quiz",
        assignment_version=version,
        source_pdf="ada.pdf",
        questions=[
            StudentSubmissionQuestion(
                question_id="q1",
                prompt=prompt,
                possible_points=10,
                response_image_path=None,
                response_text=answer,
            )
        ],
    )


@pytest.fixture
def key():
    return parse_qa_markdown(TWO_VERSIONS)


def test_the_version_read_off_the_paper_is_used(key) -> None:
    match = match_version(key, _paper("b"))

    assert (match.version, match.known) == ("B", True)
    assert solution_version_for_submission(key, _paper("b")) == "B"


def test_a_version_printed_in_the_questions_is_used_when_none_was_read(key) -> None:
    match = match_version(key, _paper(None, prompt="What is 3 + 3? [B]"))

    assert (match.version, match.known) == ("B", True)


@pytest.mark.parametrize("read", [None, "", "C"])
def test_an_unreadable_or_unknown_version_is_never_guessed(key, read) -> None:
    paper = _paper(read)
    match = match_version(key, paper)

    assert match.known is False and match.version is None
    # Nothing to grade against…
    assert solution_entries_for_submission(key, paper) == {}
    assert solution_version_for_submission(key, paper) == ""
    # …but the questions can still be shown (every version has the same IDs).
    version, questions = questions_for_submission(key, paper)
    assert version is None and [q.id for q in questions] == ["q1"]
    assert ("'C'" in match.describe()) == (read == "C")


def test_two_different_printed_markers_are_not_a_version(key) -> None:
    paper = _paper(None, prompt="[A] … (B)")

    assert match_version(key, paper).known is False


def test_a_key_with_one_version_or_none_has_nothing_to_confuse() -> None:
    one = parse_qa_markdown(ONE_VERSION)
    plain = parse_qa_markdown(ONE_VERSION.replace("## Version A\n", ""))

    assert match_version(one, _paper("Z")).known
    assert match_version(plain, _paper(None)).known
    assert solution_entries_for_submission(one, _paper("Z"))


def test_the_review_queue_lists_a_paper_whose_version_is_unknown(key) -> None:
    flags = build_review_flags([_paper("C"), _paper("B")], qa_document=key)

    unknown = [flag for flag in flags if flag.reason is ReviewReason.VERSION_UNKNOWN]
    assert len(unknown) == 1 and unknown[0].severity == "high"
    # Without the answer key nothing can be said about versions.
    assert not any(
        flag.reason is ReviewReason.VERSION_UNKNOWN
        for flag in build_review_flags([_paper("C")])
    )


def test_auto_grading_leaves_an_unknown_version_ungraded_until_it_is_picked(
    key, tmp_path: Path, capsys
) -> None:
    path = tmp_path / "ada.submission.md"
    path.write_text(render_student_submission_markdown(_paper("C")), encoding="utf-8")

    grade_student_file(path, key)

    assert load_student_submission_markdown(path).questions[0].points_awarded is None
    assert "Skipped ada.submission.md" in capsys.readouterr().out

    set_assignment_version_file(path, version="B")
    grade_student_file(path, key)

    graded = load_student_submission_markdown(path)
    assert graded.assignment_version == "B"
    assert graded.questions[0].points_awarded == 10


def test_picking_a_version_changes_only_that_header_line(tmp_path: Path) -> None:
    path = tmp_path / "ada.submission.md"
    original = render_student_submission_markdown(replace(_paper("C"), section="Section 01"))
    path.write_text(original, encoding="utf-8")

    set_assignment_version_file(path, version="A")

    changed = [
        (before, after)
        for before, after in zip(original.splitlines(), path.read_text().splitlines())
        if before != after
    ]
    assert len(changed) == 1 and "assignment_version" in changed[0][1]
