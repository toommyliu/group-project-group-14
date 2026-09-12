import asyncio
from pathlib import Path

from textual.app import App
from textual.widgets import Input, Select

from opengrader.core.submissions import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    render_student_submission_markdown,
)
from opengrader.core.submissions.regrade import open_regrade_requests
from opengrader.tui.data.submission_files_provider import SubmissionFilesProvider
from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens import FileRegradeResult, FileRegradeScreen
from opengrader.tui.ui.screens.file_regrade_screen import _MANUAL_STUDENT

from .fakes import DEFAULT_IMAGE_PATH


def _write_qa(path: Path) -> None:
    path.write_text(
        "---\ntitle: Quiz\n---\n\n"
        "- (10 points) One.\n  - **Rubric**\n"
        "    - Correct (+0)\n    - Partially Correct (-5)\n    - Incorrect (-10)\n",
        encoding="utf-8",
    )


def _write_submission(path: Path, *, name: str, student_id: str) -> None:
    document = StudentSubmissionDocument(
        student_name=name,
        student_id=student_id,
        course_code="CS146",
        assignment_title="Quiz",
        assignment_version=None,
        source_pdf=f"{path.stem}.pdf",
        questions=[
            StudentSubmissionQuestion(
                question_id="q1",
                prompt="",
                possible_points=10,
                response_image_path=None,
                response_text="",
                points_awarded=10,
            )
        ],
    )
    path.write_text(render_student_submission_markdown(document), encoding="utf-8")


def _provider(tmp_path: Path) -> SubmissionFilesProvider:
    subs = tmp_path / "subs"
    subs.mkdir()
    qa = tmp_path / "qa.md"
    _write_qa(qa)
    _write_submission(subs / "a.submission.md", name="Tommy Liu", student_id="111")
    _write_submission(subs / "b.submission.md", name="Andy Cortes", student_id="222")
    return SubmissionFilesProvider(
        qa_file_path=qa,
        submissions_path=subs,
        default_image_path=DEFAULT_IMAGE_PATH,
    )


def test_file_regrade_persists_a_request(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    provider.file_regrade(student_id="111", reason="Method is valid", question_id="q1")

    requests = open_regrade_requests(provider.submissions_path)
    assert len(requests) == 1
    assert requests[0].student_id == "111"
    assert requests[0].question_id == "q1"
    assert requests[0].reason == "Method is valid"
    assert requests[0].source == "instructor"


def test_file_regrade_whole_assignment(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    provider.file_regrade(student_id="222", reason="Totals are off")
    requests = open_regrade_requests(provider.submissions_path)
    assert requests[0].question_id == ""


def test_regrade_student_choices_are_id_label_pairs_sorted_by_name(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    choices = provider.regrade_student_choices()
    assert choices == [
        ("222", "Andy Cortes · 222"),
        ("111", "Tommy Liu · 111"),
    ]


def test_regrade_question_choices_humanize_ids(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    assert provider.regrade_question_choices() == [("q1", "Question 1")]


def test_student_identity_for_item(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    assert provider.student_identity_for_item("s1") == ("111", "Tommy Liu")
    assert provider.student_identity_for_item("s2") == ("222", "Andy Cortes")
    assert provider.student_identity_for_item("s99") is None
    assert provider.student_identity_for_item(None) is None


_STUDENTS = [("111", "Tommy Liu · 111"), ("222", "Andy Cortes · 222")]
_QUESTIONS = [("q1", "Question 1"), ("q2", "Question 2")]


class _Host(App):
    def __init__(self, **screen_kwargs) -> None:
        super().__init__()
        self._screen_kwargs = screen_kwargs
        self.result: object = "unset"

    def on_mount(self) -> None:
        self.push_screen(FileRegradeScreen(**self._screen_kwargs), self._cb)

    def _cb(self, result: object) -> None:
        self.result = result


def test_screen_prefilled_from_grade_screen() -> None:
    async def run() -> None:
        app = _Host(
            student_choices=_STUDENTS,
            question_choices=_QUESTIONS,
            initial_student_id="222",
            initial_question_id="q2",
        )
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            screen = app.screen
            assert screen.query_one("#file-regrade-student", Select).value == "222"
            assert screen.query_one("#file-regrade-question", Select).value == "q2"
            screen.query_one("#file-regrade-reason", Input).value = "Valid method"
            await pilot.pause()
            await pilot.click("#file-regrade-submit")
            await pilot.pause()
            assert app.result == FileRegradeResult("222", "q2", "Valid method")

    asyncio.run(run())


def test_screen_qa_picker_manual_entry_and_whole_assignment() -> None:
    async def run() -> None:
        app = _Host(student_choices=_STUDENTS, question_choices=_QUESTIONS)
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            screen = app.screen
            assert "hidden" in screen.query_one("#file-regrade-manual-row").classes
            screen.query_one("#file-regrade-student", Select).value = _MANUAL_STUDENT
            await pilot.pause()
            assert "hidden" not in screen.query_one("#file-regrade-manual-row").classes
            screen.query_one("#file-regrade-manual-id", Input).value = "999"
            screen.query_one("#file-regrade-reason", Input).value = "Not on roster"
            await pilot.pause()
            await pilot.click("#file-regrade-submit")
            await pilot.pause()
            assert app.result == FileRegradeResult("999", "", "Not on roster")

    asyncio.run(run())


def test_screen_empty_reason_is_refused() -> None:
    async def run() -> None:
        app = _Host(
            student_choices=_STUDENTS,
            question_choices=_QUESTIONS,
            initial_student_id="111",
        )
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await pilot.click("#file-regrade-submit")
            await pilot.pause()
            assert app.result == "unset"
            assert isinstance(app.screen, FileRegradeScreen)

    asyncio.run(run())


def test_screen_manual_student_without_id_is_refused() -> None:
    async def run() -> None:
        app = _Host(student_choices=_STUDENTS, question_choices=_QUESTIONS)
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#file-regrade-student", Select).value = _MANUAL_STUDENT
            screen.query_one("#file-regrade-reason", Input).value = "Has a reason"
            await pilot.pause()
            await pilot.click("#file-regrade-submit")
            await pilot.pause()
            assert app.result == "unset"

    asyncio.run(run())


def test_f_key_is_teacher_gated_and_files_a_request(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH, data_provider=provider)

    async def run() -> None:
        async with app.run_test(size=(140, 44)) as pilot:
            await pilot.pause()
            await pilot.press("F")
            await pilot.pause()
            assert not isinstance(app.screen, FileRegradeScreen)

            await pilot.press("t")
            await pilot.pause()
            await pilot.press("F")
            await pilot.pause()
            assert isinstance(app.screen, FileRegradeScreen)

            screen = app.screen
            screen.query_one("#file-regrade-student", Select).value = "111"
            screen.query_one("#file-regrade-reason", Input).value = "Please re-check q1"
            await pilot.pause()
            await pilot.click("#file-regrade-submit")
            await pilot.pause()

    asyncio.run(run())

    requests = open_regrade_requests(provider.submissions_path)
    assert len(requests) == 1
    assert requests[0].student_id == "111"
    assert requests[0].reason == "Please re-check q1"


def test_screen_cancel_returns_none() -> None:
    async def run() -> None:
        app = _Host(
            student_choices=_STUDENTS,
            question_choices=_QUESTIONS,
            initial_student_id="111",
        )
        async with app.run_test(size=(120, 44)) as pilot:
            await pilot.pause()
            await pilot.click("#file-regrade-cancel")
            await pilot.pause()
            assert app.result is None

    asyncio.run(run())
