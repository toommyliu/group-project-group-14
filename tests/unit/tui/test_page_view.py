"""Grade-screen whole-page view: the `w`/`W` walk over a question's pages.

Driven against the real app + real `SubmissionFilesProvider` so the wiring from
`.submission.md` through the provider, controller and screen is covered end to
end — the bug this feature fixes (grading a fragment) lives in that seam, not in
any one layer.
"""

import asyncio
from pathlib import Path

import fitz
import pytest
from textual.widgets import Button, Label

from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.widgets.response_viewer import ResponseViewer

QA = """---
title: Quiz
---

- (10 points) Convert.
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
source_pdf: "../split-submissions/student.pdf"
student_score: ""
---

- q1:
  - Question type: Free Response
  - Prompt: Convert.
  - Response image: ![q1 response](responses/q1.png)
  - Source segments: [{"page":1,"bbox":[0.2,0.3,0.5,0.2]}]
  - Response text:
  - Possible points:
  - Points:
  - Analysis:
"""


@pytest.fixture
def workspace(tmp_path: Path):
    """A one-question submission whose 3-page scan has only page 1 cropped."""
    (tmp_path / "qa.md").write_text(QA)

    split_dir = tmp_path / "split-submissions"
    split_dir.mkdir()
    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=300, height=400)
    doc.save(split_dir / "student.pdf")
    doc.close()

    submissions = tmp_path / "student-submissions"
    responses = submissions / "responses"
    responses.mkdir(parents=True)
    (submissions / "student.submission.md").write_text(SUBMISSION)
    # A real crop file so the default view has something to show.
    from PIL import Image

    Image.new("RGB", (120, 60), "white").save(responses / "q1.png")
    return tmp_path / "qa.md", submissions


def _app(workspace) -> TuiApp:
    qa_file, submissions = workspace
    return TuiApp(qa_file_path=qa_file, submissions_path=submissions)


async def _open_grade_screen(app: TuiApp, pilot) -> None:
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


def _viewer_name(app: TuiApp) -> str:
    viewer = app.get_grade_screen().query_one("#response-viewer", ResponseViewer)
    return Path(viewer.image_path).name


async def _test_w_walks_crop_then_source_page_then_extra_pages(workspace) -> None:
    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        crop = _viewer_name(app)
        assert crop == "q1.png"

        # Page 1 (the sheet the crop came from), then pages 2-3 as extra work.
        # The `-anon` suffix is the anonymous-grading cache key: the app starts
        # outside teacher mode, so pages render with the identity header
        # redacted and must not share a cache slot with the teacher-mode render.
        seen = []
        for _ in range(3):
            await pilot.press("w")
            await pilot.pause()
            seen.append(_viewer_name(app))
        assert seen == [
            "student-q1-p1-anon.png",
            "student-q1-p2-anon.png",
            "student-q1-p3-anon.png",
        ]

        # The walk wraps back to the crop, so one key gets you home.
        await pilot.press("w")
        await pilot.pause()
        assert _viewer_name(app) == crop


async def _test_shift_w_walks_backwards(workspace) -> None:
    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        # Backwards from the crop lands on the last page in the sequence.
        await pilot.press("W")
        await pilot.pause()
        assert _viewer_name(app) == "student-q1-p3-anon.png"
        await pilot.press("W")
        await pilot.pause()
        assert _viewer_name(app) == "student-q1-p2-anon.png"


async def _test_indicator_distinguishes_source_and_extra_pages(workspace) -> None:
    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        screen = app.get_grade_screen()
        indicator = screen.query_one("#question-page-indicator", Label)
        assert indicator.display is False

        await pilot.press("w")
        await pilot.pause()
        assert indicator.display is True
        assert "answer region highlighted" in str(indicator.render())

        await pilot.press("w")
        await pilot.pause()
        assert "extra work page" in str(indicator.render())

        # Back on the crop the caption goes away entirely.
        await pilot.press("w")
        await pilot.press("w")
        await pilot.pause()
        assert indicator.display is False


async def _test_toggle_button_jumps_to_the_first_page_and_back(workspace) -> None:
    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        toggle = app.get_grade_screen().query_one("#question-page-toggle", Button)
        assert toggle.display is True

        await pilot.click("#question-page-toggle")
        await pilot.pause()
        assert _viewer_name(app) == "student-q1-p1-anon.png"
        assert "answer crop" in str(toggle.label)

        await pilot.click("#question-page-toggle")
        await pilot.pause()
        assert _viewer_name(app) == "q1.png"
        assert "whole page" in str(toggle.label)


async def _test_external_viewer_opens_the_page_that_is_showing(workspace) -> None:
    """`o` opens what's displayed — the page, not the crop it replaced."""
    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        await pilot.press("w")
        await pilot.pause()

        opened: list[str] = []
        viewer = app.get_grade_screen().query_one("#response-viewer", ResponseViewer)
        import opengrader.tui.ui.widgets.response_viewer as module

        original = module.open_in_external_viewer
        module.open_in_external_viewer = lambda path: opened.append(path) or Path(path).name
        try:
            viewer.open_externally()
        finally:
            module.open_in_external_viewer = original

        assert Path(opened[0]).name == "student-q1-p1-anon.png"


async def _test_changing_question_returns_to_the_crop(workspace, tmp_path: Path) -> None:
    # Add a second question so `k` has somewhere to go.
    qa_file, submissions = workspace
    qa_file.write_text(
        QA
        + "\n- (10 points) Explain.\n  - **Rubric**\n    - Correct (+0)\n"
        "    - Partially Correct (-5)\n    - Incorrect (-10)\n"
    )
    md = submissions / "student.submission.md"
    md.write_text(
        md.read_text()
        + "\n- q2:\n"
        "  - Question type: Free Response\n"
        "  - Prompt: Explain.\n"
        "  - Response image: \n"
        '  - Source segments: [{"page":2,"bbox":[0.1,0.1,0.4,0.2]}]\n'
        "  - Response text: \n"
        "  - Possible points: \n"
        "  - Points: \n"
        "  - Analysis: \n"
    )

    app = TuiApp(qa_file_path=qa_file, submissions_path=submissions)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        await pilot.press("w")
        await pilot.pause()
        assert _viewer_name(app) == "student-q1-p1-anon.png"

        await pilot.press("j")  # next question
        await pilot.pause()
        assert app.workflow_state.grading_run.current_question.question_id == "q2"
        # A fresh question always opens on its own crop, never on a stale page.
        assert app.get_grade_screen()._page_view_index is None


async def _test_toggle_hidden_when_there_is_no_source_pdf(tmp_path: Path) -> None:
    (tmp_path / "qa.md").write_text(QA)
    submissions = tmp_path / "student-submissions"
    submissions.mkdir()
    (submissions / "student.submission.md").write_text(
        SUBMISSION.replace("../split-submissions/student.pdf", "../missing.pdf")
    )

    app = TuiApp(qa_file_path=tmp_path / "qa.md", submissions_path=submissions)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        screen = app.get_grade_screen()
        assert screen.query_one("#question-page-toggle", Button).display is False

        # Pressing w says so rather than silently doing nothing.
        await pilot.press("w")
        await pilot.pause()
        assert screen._page_view_index is None


# Sync wrappers (the suite's convention: no async pytest plugin).
def test_w_walks_crop_then_source_page_then_extra_pages(workspace) -> None:
    asyncio.run(_test_w_walks_crop_then_source_page_then_extra_pages(workspace))


def test_shift_w_walks_backwards(workspace) -> None:
    asyncio.run(_test_shift_w_walks_backwards(workspace))


def test_indicator_distinguishes_source_and_extra_pages(workspace) -> None:
    asyncio.run(_test_indicator_distinguishes_source_and_extra_pages(workspace))


def test_toggle_button_jumps_to_the_first_page_and_back(workspace) -> None:
    asyncio.run(_test_toggle_button_jumps_to_the_first_page_and_back(workspace))


def test_external_viewer_opens_the_page_that_is_showing(workspace) -> None:
    asyncio.run(_test_external_viewer_opens_the_page_that_is_showing(workspace))


def test_changing_question_returns_to_the_crop(workspace, tmp_path: Path) -> None:
    asyncio.run(_test_changing_question_returns_to_the_crop(workspace, tmp_path))


def test_toggle_hidden_when_there_is_no_source_pdf(tmp_path: Path) -> None:
    asyncio.run(_test_toggle_hidden_when_there_is_no_source_pdf(tmp_path))
