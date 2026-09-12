import asyncio
from pathlib import Path

import fitz
import pytest
from PIL import Image
from textual.widgets import Input

from opengrader.tui.tui import TuiApp

QA = """---
title: Quiz
---

- (10 points) First question.
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
- (10 points) Second question.
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
"""

SUBMISSION = """---
student_name: "Ann Lee"
student_id: "1"
course_code: "CMPE"
assignment_title: "Quiz"
assignment_version: ""
source_pdf: "scan.pdf"
student_score: ""
---

- q1:
  - Question type: Free Response
  - Prompt: First question.
  - Response image: ![q1 response](responses/q1.png)
  - Response text:
  - Possible points:
  - Points:
  - Analysis:

- q2:
  - Question type: Free Response
  - Prompt: Second question.
  - Response image: ![q2 response](responses/q2.png)
  - Response text:
  - Possible points:
  - Points:
  - Analysis:
"""

TYPED = "a nice, apt job."
_KEY_NAMES = {" ": "space", ",": "comma", ".": "full_stop"}


@pytest.fixture
def workspace(tmp_path: Path):
    (tmp_path / "qa.md").write_text(QA)
    submissions = tmp_path / "student-submissions"
    responses = submissions / "responses"
    responses.mkdir(parents=True)
    (submissions / "student.submission.md").write_text(SUBMISSION)
    for question in ("q1", "q2"):
        Image.new("RGB", (160, 40), "white").save(responses / f"{question}.png")
    doc = fitz.open()
    doc.new_page(width=300, height=400)
    doc.save(submissions / "scan.pdf")
    doc.close()
    return tmp_path / "qa.md", submissions


async def _open_comment(app: TuiApp, pilot) -> Input:
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()
    await pilot.press("c")
    await pilot.pause()
    comment = app.get_grade_screen().query_one("#question-comment", Input)
    assert app.focused is comment
    return comment


async def _type(pilot, text: str) -> None:
    for character in text:
        await pilot.press(_KEY_NAMES.get(character, character))
    await pilot.pause()


async def _test_a_full_sentence_types_into_the_comment(workspace) -> None:
    qa_file, submissions = workspace
    app = TuiApp(qa_file_path=qa_file, submissions_path=submissions)
    async with app.run_test(size=(140, 45)) as pilot:
        comment = await _open_comment(app, pilot)
        await _type(pilot, TYPED)

        assert comment.value == TYPED
        assert app.focused is comment


async def _test_typing_triggers_no_hotkeys(workspace) -> None:
    qa_file, submissions = workspace
    app = TuiApp(qa_file_path=qa_file, submissions_path=submissions)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_comment(app, pilot)
        screen = app.get_grade_screen()
        viewer = screen.query_one("#response-viewer")
        await _type(pilot, TYPED)

        assert app.workflow_state.grading_run.current_question.question_id == "q1"
        assert app.workflow_state.selection.student_item_id == "s1"
        assert viewer.rotation == 0


async def _test_arrow_keys_move_the_cursor(workspace) -> None:
    qa_file, submissions = workspace
    app = TuiApp(qa_file_path=qa_file, submissions_path=submissions)
    async with app.run_test(size=(140, 45)) as pilot:
        comment = await _open_comment(app, pilot)
        await _type(pilot, "abcd")

        await pilot.press("left", "left")
        await pilot.pause()
        await pilot.press("X")
        await pilot.pause()

        assert comment.value == "abXcd"
        assert app.workflow_state.selection.student_item_id == "s1"


async def _test_a_deliberate_focus_move_still_works(workspace) -> None:
    qa_file, submissions = workspace
    app = TuiApp(qa_file_path=qa_file, submissions_path=submissions)
    async with app.run_test(size=(140, 45)) as pilot:
        comment = await _open_comment(app, pilot)
        await _type(pilot, "done")

        await pilot.press("enter")
        await pilot.pause()

        assert app.focused is not comment
        assert comment.value == "done"


def test_a_full_sentence_types_into_the_comment(workspace) -> None:
    asyncio.run(_test_a_full_sentence_types_into_the_comment(workspace))


def test_typing_triggers_no_hotkeys(workspace) -> None:
    asyncio.run(_test_typing_triggers_no_hotkeys(workspace))


def test_arrow_keys_move_the_cursor(workspace) -> None:
    asyncio.run(_test_arrow_keys_move_the_cursor(workspace))


def test_a_deliberate_focus_move_still_works(workspace) -> None:
    asyncio.run(_test_a_deliberate_focus_move_still_works(workspace))
