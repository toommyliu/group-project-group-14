import asyncio
from pathlib import Path

import fitz
import pytest
from PIL import Image
from textual.widgets import Label

from opengrader.core.pdf_utils import REDACTION_RGB
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
  - Source segments: [{"page":1,"bbox":[0.1,0.6,0.7,0.2]}]
  - Response text:
  - Possible points:
  - Points:
  - Analysis:
"""

HEADER_ROW = 150 / 792
WORK_ROW = 600 / 792


def _ocr_available() -> bool:
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _ocr_available(), reason="requires a Tesseract binary"
)


@pytest.fixture
def workspace(tmp_path: Path):
    (tmp_path / "qa.md").write_text(QA)

    split_dir = tmp_path / "split-submissions"
    split_dir.mkdir()
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text(fitz.Point(72, 150), "Name: Ann Lee", fontsize=16)
    page.insert_text(fitz.Point(72, 180), "Student ID: 012345678", fontsize=16)
    page.insert_text(fitz.Point(72, 600), "the answer continues here", fontsize=16)
    doc.save(split_dir / "student.pdf")
    doc.close()

    submissions = tmp_path / "student-submissions"
    responses = submissions / "responses"
    responses.mkdir(parents=True)
    (submissions / "student.submission.md").write_text(SUBMISSION)
    Image.new("RGB", (120, 60), "white").save(responses / "q1.png")
    return tmp_path / "qa.md", submissions


def _app(workspace) -> TuiApp:
    qa_file, submissions = workspace
    return TuiApp(qa_file_path=qa_file, submissions_path=submissions)


def _viewer_path(app: TuiApp) -> Path:
    viewer = app.get_grade_screen().query_one("#response-viewer", ResponseViewer)
    return Path(viewer.image_path)


def _row_is_redacted(png: Path, y_fraction: float) -> bool:
    image = Image.open(png).convert("RGB")
    y = int(image.height * y_fraction)
    return all(
        image.getpixel((x, y)) == REDACTION_RGB
        for x in range(0, image.width, 17)
    )


def _caption(app: TuiApp) -> str:
    label = app.get_grade_screen().query_one("#question-page-indicator", Label)
    return str(label.render())


async def _test_anonymous_grading_redacts_the_header(workspace) -> None:
    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.workflow_state.teacher_mode is False

        await pilot.press("w")
        await pilot.pause()

        page = _viewer_path(app)
        assert page.name.endswith("-anon.png")
        assert _row_is_redacted(page, HEADER_ROW) is True
        assert _row_is_redacted(page, WORK_ROW) is False
        assert "identity redacted" in _caption(app)


async def _test_teacher_mode_reveals_the_header(workspace) -> None:
    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("w")
        await pilot.pause()
        anonymous_page = _viewer_path(app)

        await pilot.press("t")
        await pilot.pause()
        assert app.workflow_state.teacher_mode is True

        revealed = _viewer_path(app)
        assert revealed != anonymous_page
        assert not revealed.name.endswith("-anon.png")
        assert _row_is_redacted(revealed, HEADER_ROW) is False
        assert "identity redacted" not in _caption(app)

        await pilot.press("t")
        await pilot.pause()
        assert _row_is_redacted(_viewer_path(app), HEADER_ROW) is True


def test_anonymous_grading_redacts_the_header(workspace) -> None:
    asyncio.run(_test_anonymous_grading_redacts_the_header(workspace))


def test_teacher_mode_reveals_the_header(workspace) -> None:
    asyncio.run(_test_teacher_mode_reveals_the_header(workspace))
