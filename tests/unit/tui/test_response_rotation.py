"""Grade-screen response rotation: the `,`/`.` quarter turns.

Rotation is a *display* transform for an answer the student wrote sideways in
the box. The properties worth pinning down are that it turns the pixels, that it
re-shapes the widget (a quarter turn swaps the aspect ratio, and getting that
wrong squashes the image), that it never touches a file, and that it clears with
the student rather than lingering invisibly onto someone else's paper.

A whole stack scanned the wrong way round is *not* what this fixes — that is the
scan pre-flight's job, before extraction. See ``test_scan_check.py``.
"""

import asyncio
from pathlib import Path

import fitz
import pytest
from PIL import Image

from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.widgets.response_viewer import ResponseViewer
from opengrader.tui.ui.widgets.sharp_image import SharpImage

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


def _submission(name: str, student_id: str) -> str:
    return f"""---
student_name: "{name}"
student_id: "{student_id}"
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


@pytest.fixture
def workspace(tmp_path: Path):
    """Two students, two questions each, with wide (landscape) crops."""
    (tmp_path / "qa.md").write_text(QA)
    submissions = tmp_path / "student-submissions"
    for index, (name, student_id) in enumerate(
        [("Ann Lee", "1"), ("Bo Chen", "2")], start=1
    ):
        folder = submissions / f"student{index}"
        responses = folder / "responses"
        responses.mkdir(parents=True)
        (folder / f"student{index}.submission.md").write_text(
            _submission(name, student_id)
        )
        for question in ("q1", "q2"):
            # Deliberately not square, so a quarter turn is observable in the
            # widget's reported shape.
            Image.new("RGB", (160, 40), "white").save(responses / f"{question}.png")
        # A real (if empty) scan beside each submission: an absent source PDF is
        # its own code path, and not the one under test here.
        doc = fitz.open()
        doc.new_page(width=300, height=400)
        doc.save(folder / "scan.pdf")
        doc.close()
    return tmp_path / "qa.md", submissions


def _app(workspace) -> TuiApp:
    qa_file, submissions = workspace
    return TuiApp(qa_file_path=qa_file, submissions_path=submissions)


def _viewer(app: TuiApp) -> ResponseViewer:
    return app.get_grade_screen().query_one("#response-viewer", ResponseViewer)


async def _open_grade_screen(app: TuiApp, pilot) -> None:
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


# --------------------------------------------------------------------------- #
# The widget
# --------------------------------------------------------------------------- #


def test_rotation_turns_the_prepared_pixels(tmp_path: Path) -> None:
    # A red stripe down the left edge should land along the top after a
    # clockwise quarter turn. This is the check that the transpose direction is
    # right — a 90° error looks plausible until you compare a landmark.
    source = Image.new("RGB", (40, 20), "white")
    for y in range(20):
        source.putpixel((0, y), (255, 0, 0))
    path = tmp_path / "crop.png"
    source.save(path)

    widget = SharpImage(str(path))
    upright = widget._prepare_image(40, 20)
    assert upright.getpixel((0, 0)) == (255, 0, 0)
    assert upright.getpixel((39, 0)) != (255, 0, 0)

    widget.rotation = 90
    turned = widget._prepare_image(20, 40)
    # The left edge is now the top edge.
    assert turned.getpixel((0, 0)) == (255, 0, 0)
    assert turned.getpixel((19, 0)) == (255, 0, 0)
    assert turned.getpixel((0, 39)) != (255, 0, 0)


def test_a_quarter_turn_swaps_the_display_shape(tmp_path: Path) -> None:
    # Layout is driven from the *displayed* size; without the swap a rotated
    # landscape crop would be measured into a landscape box and squashed.
    path = tmp_path / "crop.png"
    Image.new("RGB", (160, 40), "white").save(path)
    widget = SharpImage(str(path))

    assert widget._display_size == (160, 40)
    widget.rotation = 90
    assert widget._display_size == (40, 160)
    widget.rotation = 180
    assert widget._display_size == (160, 40)
    widget.rotation = 270
    assert widget._display_size == (40, 160)


def test_rotation_is_part_of_the_cache_identity(tmp_path: Path) -> None:
    # Both this widget's prepared-image cache and the inline pane's encoded
    # escape cache key off the token; without rotation in it a turned crop
    # would keep serving the previous orientation.
    path = tmp_path / "crop.png"
    Image.new("RGB", (40, 20), "white").save(path)
    widget = SharpImage(str(path))

    upright_token = widget._image_cache_token()
    widget.rotation = 90
    assert widget._image_cache_token() != upright_token


# --------------------------------------------------------------------------- #
# The grade screen
# --------------------------------------------------------------------------- #


async def _test_keys_turn_the_view_both_ways(workspace) -> None:
    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        assert _viewer(app).rotation == 0

        await pilot.press(".")
        await pilot.pause()
        assert _viewer(app).rotation == 90

        await pilot.press(".")
        await pilot.pause()
        assert _viewer(app).rotation == 180

        await pilot.press(",")
        await pilot.pause()
        assert _viewer(app).rotation == 90

        # Anticlockwise past zero wraps rather than going negative.
        await pilot.press(",")
        await pilot.press(",")
        await pilot.pause()
        assert _viewer(app).rotation == 270


async def _test_rotation_is_announced(workspace) -> None:
    # An invisible mode would be a correctness hazard: a grader who forgets a
    # rotation is on misreads the next answer.
    from textual.widgets import Label

    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        label = app.get_grade_screen().query_one("#grade-zoom-level", Label)
        assert "Rotated" not in str(label.render())

        await pilot.press(".")
        await pilot.pause()
        assert "Rotated 90°" in str(label.render())


async def _test_rotation_survives_a_question_change(workspace) -> None:
    # A sideways sheet is sideways for every question on it, so re-turning the
    # paper per question would be busywork.
    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        await pilot.press(".")
        await pilot.pause()
        assert _viewer(app).rotation == 90

        await pilot.press("j")  # next question, same student
        await pilot.pause()
        assert app.workflow_state.grading_run.current_question.question_id == "q2"
        assert _viewer(app).rotation == 90


async def _test_rotation_clears_on_a_new_student(workspace) -> None:
    # The opposite side of the same decision: a rotation must never carry
    # silently onto someone else's paper.
    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        await pilot.press(".")
        await pilot.pause()
        assert _viewer(app).rotation == 90

        await pilot.press("ctrl+j")  # next student
        await pilot.pause()
        assert _viewer(app).rotation == 0


async def _test_rotation_never_touches_the_crop_file(workspace) -> None:
    qa_file, submissions = workspace
    crop = submissions / "student1" / "responses" / "q1.png"
    before = crop.read_bytes()

    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        await pilot.press(".")
        await pilot.press(".")
        await pilot.pause()

    assert crop.read_bytes() == before


async def _test_rotation_yields_to_a_focused_input(workspace) -> None:
    """`,` and `.` are printable, so a focused text box must win.

    A grader typing "well argued, but..." is not asking to rotate anything.
    Asserted through the app's own availability guard rather than by typing:
    the rotate keys are deliberately kept *out* of the Input-focus bypass list
    that zoom and student navigation sit in, and that list is the thing worth
    pinning down.
    """
    from textual.widgets import Input

    from opengrader.tui.controllers.input_controller import InputCommand

    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        # Nothing focused in a text box: the keys are live.
        assert app._input_command_available(InputCommand.GRADE_ROTATE_LEFT)
        assert app._input_command_available(InputCommand.GRADE_ROTATE_RIGHT)

        await pilot.press("c")  # focus the comment box
        await pilot.pause()
        assert isinstance(app.focused, Input)

        assert not app._input_command_available(InputCommand.GRADE_ROTATE_LEFT)
        assert not app._input_command_available(InputCommand.GRADE_ROTATE_RIGHT)
        # Zoom is blocked too, for the same reason: "+" belongs to the sentence
        # being typed. Only chords a text box can never receive survive.
        assert not app._input_command_available(InputCommand.GRADE_ZOOM_IN)
        assert app._input_command_available(InputCommand.GRADE_FINALIZE_STUDENT)


async def _test_external_viewer_opens_the_turned_image(workspace) -> None:
    # `o` opens *what is displayed*; on a terminal with no graphics protocol it
    # is the only way to read the answer, so it must not hand back the un-turned
    # original.
    from opengrader.tui.ui.widgets import response_viewer as viewer_module

    opened: list[str] = []

    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        viewer = _viewer(app)
        original = viewer.image_path

        def _capture(path: str) -> str:
            opened.append(path)
            return Path(path).name

        viewer_module.open_in_external_viewer = _capture  # type: ignore[assignment]
        try:
            await pilot.press("o")
            await pilot.pause()
            assert opened[-1] == original  # unrotated: the source file itself

            await pilot.press(".")
            await pilot.pause()
            await pilot.press("o")
            await pilot.pause()
        finally:
            viewer_module.open_in_external_viewer = (  # type: ignore[assignment]
                viewer_module.open_in_external_viewer
            )

    turned = Path(opened[-1])
    assert turned != Path(original)
    assert turned.exists()
    # The copy really is turned: the source crop is 160x40.
    with Image.open(turned) as image:
        assert image.size == (40, 160)
    # ...and the original is still on disk, untouched.
    with Image.open(original) as image:
        assert image.size == (160, 40)


# Sync wrappers (the suite's convention: no async pytest plugin).
def test_keys_turn_the_view_both_ways(workspace) -> None:
    asyncio.run(_test_keys_turn_the_view_both_ways(workspace))


def test_rotation_is_announced(workspace) -> None:
    asyncio.run(_test_rotation_is_announced(workspace))


def test_rotation_survives_a_question_change(workspace) -> None:
    asyncio.run(_test_rotation_survives_a_question_change(workspace))


def test_rotation_clears_on_a_new_student(workspace) -> None:
    asyncio.run(_test_rotation_clears_on_a_new_student(workspace))


def test_rotation_never_touches_the_crop_file(workspace) -> None:
    asyncio.run(_test_rotation_never_touches_the_crop_file(workspace))


def test_rotation_yields_to_a_focused_input(workspace) -> None:
    asyncio.run(_test_rotation_yields_to_a_focused_input(workspace))


def test_external_viewer_opens_the_turned_image(workspace) -> None:
    asyncio.run(_test_external_viewer_opens_the_turned_image(workspace))
