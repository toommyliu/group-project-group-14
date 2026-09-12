import asyncio
from pathlib import Path

import fitz
import pytest
from PIL import Image
from textual.widgets import Static

from opengrader.core.claims import WorkSlice, claim_work, read_claims
from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens import ClaimScreen

QA = """---
title: Quiz
---

- (10 points) One.
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
- (10 points) Two.
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
- (10 points) Three.
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
- (10 points) Four.
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
"""


def _submission(name: str, student_id: str) -> str:
    blocks = "\n".join(
        f"""- q{n}:
  - Question type: Free Response
  - Prompt: Q{n}.
  - Response image: ![q{n} response](responses/q{n}.png)
  - Response text: answer {n}
  - Possible points: 10
  - Points:
  - Analysis:
"""
        for n in (1, 2, 3, 4)
    )
    return f"""---
student_name: "{name}"
student_id: "{student_id}"
course_code: "CMPE"
assignment_title: "Quiz"
assignment_version: ""
source_pdf: "scan.pdf"
student_score: ""
---

{blocks}"""


@pytest.fixture
def workspace(tmp_path: Path):
    (tmp_path / "qa.md").write_text(QA)
    submissions = tmp_path / "student-submissions"
    for index, (name, student_id) in enumerate(
        [("Ann Lee", "id01"), ("Bo Chen", "id02"), ("Cy Diaz", "id03")], start=1
    ):
        folder = submissions / f"student{index}"
        responses = folder / "responses"
        responses.mkdir(parents=True)
        (folder / f"student{index}.submission.md").write_text(
            _submission(name, student_id)
        )
        for question in ("q1", "q2", "q3", "q4"):
            Image.new("RGB", (120, 40), "white").save(responses / f"{question}.png")
        doc = fitz.open()
        doc.new_page(width=300, height=400)
        doc.save(folder / "scan.pdf")
        doc.close()
    return tmp_path / "qa.md", submissions


def _app(workspace, actor: str = "me") -> TuiApp:
    qa_file, submissions = workspace
    app = TuiApp(qa_file_path=qa_file, submissions_path=submissions)
    app.data_provider.grader_actor = lambda: actor  # type: ignore[method-assign]
    return app


async def _open_grade_screen(app: TuiApp, pilot) -> None:
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


def _student(app: TuiApp) -> str:
    return app.workflow_state.selection.student_item_id


async def _test_navigation_skips_another_graders_students(workspace) -> None:
    _qa, submissions = workspace
    claim_work(
        submissions, WorkSlice(student_ids=("id01", "id02")), actor="bob"
    )

    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        assert _student(app) == "s3"

        await pilot.press("ctrl+k")
        await pilot.pause()
        assert _student(app) == "s3"


async def _test_unclaimed_work_stays_available(workspace) -> None:
    _qa, submissions = workspace
    claim_work(submissions, WorkSlice(student_ids=("id01",)), actor="bob")

    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        seen = {_student(app)}
        for _ in range(3):
            await pilot.press("ctrl+j")
            await pilot.pause()
            seen.add(_student(app))
        assert seen == {"s2", "s3"}


async def _test_an_undivided_assignment_is_untouched(workspace) -> None:
    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        assert _student(app) == "s1"
        await pilot.press("ctrl+j")
        await pilot.pause()
        assert _student(app) == "s2"


async def _test_your_own_claim_never_filters_you_out(workspace) -> None:
    _qa, submissions = workspace
    claim_work(submissions, WorkSlice(student_ids=("id01",)), actor="me")

    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        assert _student(app) == "s1"


async def _test_the_filter_announces_itself(workspace) -> None:
    _qa, submissions = workspace
    claim_work(submissions, WorkSlice(student_ids=("id01", "id02")), actor="bob")

    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        app.grade_controller.refresh_after_claim_change()
        await pilot.pause()
        status = str(app.get_grade_screen().query_one("#db-status", Static).render())
        assert "Shift+D" in status


async def _test_the_picker_annotates_rather_than_hides(workspace) -> None:
    _qa, submissions = workspace
    claim_work(submissions, WorkSlice(question_ids=("q3", "q4")), actor="bob")

    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        items = app.workflow_state.question_items
        assert [item.item_id for item in items] == ["q1", "q2", "q3", "q4"]
        by_id = {item.item_id: item.meta for item in items}
        assert "bob" in by_id["q3"] and "bob" in by_id["q4"]
        assert "bob" not in by_id["q1"]


async def _test_a_split_question_names_nobody(workspace) -> None:
    _qa, submissions = workspace
    claim_work(
        submissions,
        WorkSlice(question_ids=("q1",), student_ids=("id01",)),
        actor="bob",
    )

    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        by_id = {item.item_id: item.meta for item in app.workflow_state.question_items}
        assert "bob" not in by_id["q1"]


async def _test_opening_another_graders_question_warns_but_allows(workspace) -> None:
    _qa, submissions = workspace
    claim_work(submissions, WorkSlice(question_ids=("q1",)), actor="bob")

    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        assert app.workflow_state.selection.question_id == "q1"
        status = str(app.get_grade_screen().query_one("#db-status", Static).render())
        assert "bob" in status
        assert "still save" in status


async def _test_shift_d_opens_the_claims_modal(workspace) -> None:
    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("D")
        await pilot.pause()
        assert isinstance(app.screen, ClaimScreen)


async def _test_claiming_from_the_modal_persists(workspace) -> None:
    _qa, submissions = workspace
    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("D")
        await pilot.pause()
        field = app.screen.query_one("#claim-new-questions")
        field.value = "q1-q2"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

    (claim,) = read_claims(submissions)
    assert claim.actor == "me"
    assert claim.work.question_ids == ("q1", "q2")


async def _test_the_modal_can_turn_the_filter_off(workspace) -> None:
    _qa, submissions = workspace
    claim_work(submissions, WorkSlice(student_ids=("id01", "id02")), actor="bob")

    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_grade_screen(app, pilot)
        assert _student(app) == "s3"

        await pilot.press("D")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert app.workflow_state.claim_filter_enabled is False

        await pilot.press("escape")
        await pilot.pause()
        app.grade_controller.first_student_for_question()
        await pilot.pause()
        assert _student(app) == "s1"


async def _test_releasing_frees_the_slice(workspace) -> None:
    _qa, submissions = workspace
    claim_work(submissions, WorkSlice(question_ids=("q1",)), actor="me")

    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("D")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()

    assert read_claims(submissions)[0].status == "released"


async def _test_another_graders_row_is_not_actionable(workspace) -> None:
    _qa, submissions = workspace
    claim_work(submissions, WorkSlice(question_ids=("q1",)), actor="bob")

    app = _app(workspace)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("D")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()

    assert read_claims(submissions)[0].status == "active"


def test_navigation_skips_another_graders_students(workspace) -> None:
    asyncio.run(_test_navigation_skips_another_graders_students(workspace))


def test_unclaimed_work_stays_available(workspace) -> None:
    asyncio.run(_test_unclaimed_work_stays_available(workspace))


def test_an_undivided_assignment_is_untouched(workspace) -> None:
    asyncio.run(_test_an_undivided_assignment_is_untouched(workspace))


def test_your_own_claim_never_filters_you_out(workspace) -> None:
    asyncio.run(_test_your_own_claim_never_filters_you_out(workspace))


def test_the_filter_announces_itself(workspace) -> None:
    asyncio.run(_test_the_filter_announces_itself(workspace))


def test_the_picker_annotates_rather_than_hides(workspace) -> None:
    asyncio.run(_test_the_picker_annotates_rather_than_hides(workspace))


def test_a_split_question_names_nobody(workspace) -> None:
    asyncio.run(_test_a_split_question_names_nobody(workspace))


def test_opening_another_graders_question_warns_but_allows(workspace) -> None:
    asyncio.run(_test_opening_another_graders_question_warns_but_allows(workspace))


def test_shift_d_opens_the_claims_modal(workspace) -> None:
    asyncio.run(_test_shift_d_opens_the_claims_modal(workspace))


def test_claiming_from_the_modal_persists(workspace) -> None:
    asyncio.run(_test_claiming_from_the_modal_persists(workspace))


def test_the_modal_can_turn_the_filter_off(workspace) -> None:
    asyncio.run(_test_the_modal_can_turn_the_filter_off(workspace))


def test_releasing_frees_the_slice(workspace) -> None:
    asyncio.run(_test_releasing_frees_the_slice(workspace))


def test_another_graders_row_is_not_actionable(workspace) -> None:
    asyncio.run(_test_another_graders_row_is_not_actionable(workspace))
