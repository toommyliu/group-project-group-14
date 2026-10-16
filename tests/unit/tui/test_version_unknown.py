"""A paper whose version cannot be told waits in the review queue (R) for a
person to pick it; until then the grading app will not save a grade on it."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from opengrader.core.grading.versions import VersionUnknownError
from opengrader.core.submissions import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    load_student_submission_markdown,
    render_student_submission_markdown,
)
from opengrader.tui.data.submission_files_provider import SubmissionFilesProvider
from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens import AssignVersionScreen

from .fakes import DEFAULT_IMAGE_PATH

KEY = """---
title: Quiz
---

## Version A

- (10 points) What is 2 + 2?
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)

## Version B

- (10 points) What is 3 + 3?
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
"""


def _write(folder: Path, name: str, version: str) -> Path:
    path = folder / name / f"{name}.submission.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        render_student_submission_markdown(
            StudentSubmissionDocument(
                student_name=name.title(),
                student_id="1234567",
                course_code="CS1",
                assignment_title="Quiz",
                assignment_version=version,
                source_pdf=f"{name}.pdf",
                questions=[
                    StudentSubmissionQuestion(
                        question_id="q1",
                        prompt="",
                        possible_points=10,
                        response_image_path=None,
                        response_text="6",
                    )
                ],
            )
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def provider(tmp_path: Path) -> SubmissionFilesProvider:
    (tmp_path / "qa.md").write_text(KEY, encoding="utf-8")
    submissions = tmp_path / "submissions"
    _write(submissions, "ada", "C")  # s1: no Version C in the key
    _write(submissions, "bo", "A")  # s2
    return SubmissionFilesProvider(
        qa_file_path=tmp_path / "qa.md",
        submissions_path=submissions,
        default_image_path=DEFAULT_IMAGE_PATH,
    )


def _save(provider: SubmissionFilesProvider, student: str) -> None:
    provider.save_question_grade(
        student_item_id=student,
        question_id="q1",
        possible_points=10,
        points_awarded=10,
        rubric_labels=("Correct (+0)",),
    )


def test_an_unknown_version_is_listed_first_and_cannot_be_graded(provider) -> None:
    items = provider.build_review_items()

    assert items[0].is_version_flag and items[0].student_item_id == "s1"
    assert items[0].severity == "high"
    with pytest.raises(VersionUnknownError, match="Press R"):
        _save(provider, "s1")
    _save(provider, "s2")  # a paper whose version is known grades as usual


def test_picking_the_version_records_it_and_lets_the_paper_be_graded(provider, tmp_path) -> None:
    assert provider.answer_key_versions() == ("A", "B")

    provider.assign_submission_version(student_item_id="s1", version="B")

    path = tmp_path / "submissions" / "ada" / "ada.submission.md"
    assert load_student_submission_markdown(path).assignment_version == "B"
    assert not any(item.is_version_flag for item in provider.build_review_items())
    _save(provider, "s1")
    assert any("set version B" in str(entry) for entry in provider.read_grade_history())
    with pytest.raises(ValueError, match="no version"):
        provider.assign_submission_version(student_item_id="s2", version="Z")


def test_the_review_queue_asks_which_version_and_sets_it(provider, tmp_path) -> None:
    async def run() -> tuple[tuple[str, ...], str | None]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH, data_provider=provider)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            item = next(i for i in provider.build_review_items() if i.is_version_flag)
            app._on_review_target_chosen(item)
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, AssignVersionScreen)
            offered = tuple(row.version for row in screen.query("#version-list ListItem"))
            screen.dismiss("B")
            await pilot.pause()
            await pilot.pause()
        path = tmp_path / "submissions" / "ada" / "ada.submission.md"
        return offered, load_student_submission_markdown(path).assignment_version

    offered, saved = asyncio.run(run())

    assert offered == ("A", "B")
    assert saved == "B"
