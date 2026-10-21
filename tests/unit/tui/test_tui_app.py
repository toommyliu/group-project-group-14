import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from textual.widgets import (
    Button,
    Checkbox,
    ContentSwitcher,
    Input,
    Label,
    ListView,
    Static,
    TextArea,
)

from opengrader.tui.controllers.input_controller import InputCommand
from opengrader.tui.data import RubricEditResult, RubricOptionSeed, StudentSummary
from opengrader.tui.data.stub_data_provider import StubDataProvider
from opengrader.tui.state import PAGE_GRADE, PAGE_QA_PICKER
from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens import HelpScreen, SetupScreen
from opengrader.tui.ui.widgets.sharp_image import SharpImage
from opengrader.tui.ui.widgets.status_bar import StatusBar
from opengrader.tui.ui.widgets.tabbed_list_pane import TabbedListPane

from .fakes import DEFAULT_IMAGE_PATH, FakeDataProvider


@pytest.fixture
def workspace_provider(tmp_path):
    from fastapi.testclient import TestClient

    from opengrader.core.workspace.intake import publish_pipeline
    from opengrader.core.workspace.models import PipelineOutput
    from opengrader.core.workspace.store import Workspace
    from opengrader.server.workspace.app import create_app
    from opengrader.server.workspace.client import ServiceConnection, WorkspaceClient
    from opengrader.tui.data.workspace_provider import WorkspaceProvider

    workspace = Workspace.create(tmp_path)
    sample = Path("samples/cs146-quiz1")
    publication = publish_pipeline(
        workspace,
        workspace.assignment_id_for_folder(sample),
        PipelineOutput(
            qa_markdown=(sample / "qa.md").read_text(),
            submissions_dir=str((sample / "student-submissions").resolve()),
        ),
    )
    with TestClient(create_app(tmp_path, process_jobs=False)) as http:

        class Client(WorkspaceClient):
            def request(self, method, path, body=None, **kwargs):
                response = http.request(
                    method, path, json=body.model_dump() if body else None
                )
                response.raise_for_status()
                return response.content

            def observe(self, after, callback):
                return lambda: None

        client = Client(
            ServiceConnection(
                root=str(tmp_path),
                url="http://testserver",
                pid=0,
                workspace_id=workspace.info().id,
            )
        )
        provider = WorkspaceProvider(client, publication.assignment)
        try:
            yield provider
        finally:
            provider.close()


def _seed(
    label: str,
    points_delta: float,
    selection_mode: str = "exclusive",
) -> RubricOptionSeed:
    kind = (
        "correct"
        if label == "Correct"
        else "incorrect"
        if label == "Incorrect"
        else "partial"
    )
    return RubricOptionSeed(label, points_delta, kind, selection_mode)


class RubricEditingFakeDataProvider(FakeDataProvider):
    def __init__(self) -> None:
        self.saved_options: list[RubricOptionSeed] | None = None

    def build_question_seeds(self, **kwargs):  # noqa: ANN001, ANN201
        seeds = super().build_question_seeds(**kwargs)
        rubric_options = [
            _seed("Correct", 0.0),
            _seed("Partially Correct", -5.0),
            _seed("Incorrect", -10.0),
        ]
        return [
            seed.__class__(
                question_id=seed.question_id,
                image_path=seed.image_path,
                rubric_options=list(rubric_options),
                points=10,
                prompt=seed.prompt,
                expected_answers=list(seed.expected_answers),
                response_text=seed.response_text,
                points_awarded=seed.points_awarded,
                selected_rubric_labels=seed.selected_rubric_labels,
                analysis=seed.analysis,
            )
            for seed in seeds
        ]

    def save_rubric_options(
        self,
        *,
        student_item_id: str,
        question_id: str,
        options: list[RubricOptionSeed],
        original_options: list[RubricOptionSeed],
    ) -> RubricEditResult:
        _ = (student_item_id, question_id, original_options)
        self.saved_options = list(options)
        return RubricEditResult(propagated_count=0)


class LongRubricFakeDataProvider(FakeDataProvider):
    LONG_LABEL = (
        "Partially Correct - Binary form attempted but value incorrect and "
        "conversion process mostly incorrect"
    )

    def build_question_seeds(self, **kwargs):  # noqa: ANN001, ANN201
        seeds = super().build_question_seeds(**kwargs)
        rubric_options = [
            _seed("Correct", 0.0),
            _seed(self.LONG_LABEL, -3.0),
            _seed("Incorrect", -10.0),
        ]
        return [
            seed.__class__(
                question_id=seed.question_id,
                image_path=seed.image_path,
                rubric_options=list(rubric_options),
                points=10,
                prompt=seed.prompt,
                expected_answers=list(seed.expected_answers),
                response_text=seed.response_text,
                points_awarded=seed.points_awarded,
                selected_rubric_labels=seed.selected_rubric_labels,
                analysis=seed.analysis,
            )
            for seed in seeds
        ]


class StatisticsFakeDataProvider(FakeDataProvider):
    def build_question_seeds(self, **kwargs):  # noqa: ANN001, ANN201
        student_item_id = kwargs.get("student_item_id")
        seeds = super().build_question_seeds(**kwargs)
        if student_item_id == "s1":
            return [
                replace(seed, points=10, points_awarded=10.0)
                if seed.question_id == "Q1"
                else replace(seed, points=10)
                for seed in seeds
            ]
        if student_item_id == "s2":
            return [
                replace(seed, points=10, points_awarded=5.0)
                if seed.question_id == "Q1"
                else replace(seed, points=10)
                for seed in seeds
            ]
        return [replace(seed, points=10) for seed in seeds]


class DuplicateStudentIdFakeDataProvider(FakeDataProvider):
    def list_students(self, course_id: int | None) -> list[StudentSummary]:
        if course_id is None:
            return []
        return [
            StudentSummary(1, "Alex One", student_id="123"),
            StudentSummary(2, "Alex Two", student_id="123"),
        ]


def _list_item_texts(list_view: ListView) -> list[str]:
    texts: list[str] = []
    for item in list_view.children:
        if not hasattr(item, "nav"):
            continue
        texts.append(str(item.query_one(Label).content))
    return texts


def _student_pane(app: TuiApp) -> TabbedListPane:
    return app.get_qa_picker_screen().query_one("#student-list", TabbedListPane)


def _active_student_list(app: TuiApp) -> ListView:
    active = _student_pane(app).active_list()
    assert active is not None
    return active


def test_app_launches_directly_to_qa_picker() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            switcher = app.query_one(ContentSwitcher)
            assert switcher.current == "page-qa-picker"
            assert app.workflow_state.selection.course_id == 1
            assert app.workflow_state.selection.assignment_id == 1
            assert app.workflow_state.selection_labels["course"] == "CMPE 195"
            assert app.workflow_state.selection_labels["assignment"] == "HW 1"
            assert [item.item_id for item in app.workflow_state.question_items] == [
                "Q1",
                "Q2",
            ]
            student_list = _active_student_list(app)
            assert _list_item_texts(student_list) == [
                "s1 [0/2 graded]",
                "s2 [0/2 graded]",
            ]

    asyncio.run(run())


def test_grade_screen_shows_source_pdf_name_when_provided() -> None:
    class PdfProvider(FakeDataProvider):
        def build_question_seeds(self, **kwargs):
            return [
                replace(seed, source_pdf_name="student-1.pdf")
                for seed in super().build_question_seeds(**kwargs)
            ]

    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=PdfProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            pdf_label = app.query_one("#grade-source-pdf", Label)
            assert pdf_label.display is True
            assert str(pdf_label.content) == "PDF: student-1.pdf"

    asyncio.run(run())


def test_student_pane_id_tab_falls_back_to_internal_student_keys() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            assert _student_pane(app).active_tab_index == 0
            assert _list_item_texts(_active_student_list(app)) == [
                "s1 [0/2 graded]",
                "s2 [0/2 graded]",
            ]

    asyncio.run(run())


def test_student_pane_name_id_hotkey_toggles_display_mode() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            pane = _student_pane(app)
            status = app.get_qa_picker_screen().query_one("#qa-status", Static)
            assert pane.active_tab_index == 0

            await pilot.press("d")
            await pilot.pause()

            assert pane.active_tab_index == 0
            assert "Enable teacher mode (t)" in str(status.content)

            await pilot.press("t")
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()

            assert pane.active_tab_index == 1
            assert _list_item_texts(_active_student_list(app)) == [
                "Tommy Liu [0/2 graded]",
                "Andy Cortes [0/2 graded]",
            ]
            assert "Showing student names." in str(status.content)

            await pilot.press("d")
            await pilot.pause()

            assert pane.active_tab_index == 0
            assert _list_item_texts(_active_student_list(app)) == [
                "s1 [0/2 graded]",
                "s2 [0/2 graded]",
            ]

            await pilot.press("d")
            await pilot.pause()
            assert pane.active_tab_index == 1

            await pilot.press("t")
            await pilot.pause()
            assert pane.active_tab_index == 0
            assert _list_item_texts(_active_student_list(app)) == [
                "s1 [0/2 graded]",
                "s2 [0/2 graded]",
            ]

    asyncio.run(run())


def test_student_pane_id_tab_disambiguates_duplicate_submitted_ids() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=DuplicateStudentIdFakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            assert _student_pane(app).active_tab_index == 0
            assert _list_item_texts(_active_student_list(app)) == [
                "123 (s1) [0/2 graded]",
                "123 (s2) [0/2 graded]",
            ]

    asyncio.run(run())


def test_student_pane_selection_is_preserved_when_switching_name_id_tabs() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            qa_screen = app.get_qa_picker_screen()
            app.workflow_state.teacher_mode = True
            qa_screen.apply_teacher_mode()
            qa_screen.focus_students()
            qa_screen.move_student_to_index(1)
            await pilot.pause()

            _student_pane(app).action_pane_activate_tab(1)
            await pilot.pause()

            student_list = _active_student_list(app)
            assert student_list.index == 1
            assert student_list.children[1].has_class("-highlight") is True
            assert qa_screen.selected_student().label == "Andy Cortes"

    asyncio.run(run())


def test_student_filter_matches_submitted_ids() -> None:
    class IdProvider(FakeDataProvider):
        def list_students(self, course_id: int | None) -> list[StudentSummary]:
            return [
                replace(student, student_id=student_id)
                for student, student_id in zip(
                    super().list_students(course_id), ("123456789", "987654321")
                )
            ]

    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=IdProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            qa_screen = app.get_qa_picker_screen()
            qa_screen.focus_students()
            prompt, initial = qa_screen.start_filter()
            assert (prompt, initial) == ("Search students:", "")

            status = qa_screen.update_filter("987654321")
            await pilot.pause()

            assert status == "Search students: 1/2 matches"
            assert _list_item_texts(_active_student_list(app)) == [
                "987654321 [0/2 graded]"
            ]

            app.workflow_state.teacher_mode = True
            qa_screen.apply_teacher_mode()
            _student_pane(app).action_pane_activate_tab(1)
            await pilot.pause()

            assert _list_item_texts(_active_student_list(app)) == [
                "Andy Cortes [0/2 graded]"
            ]

    asyncio.run(run())


def test_comment_library_inserts_snippet_into_comment_box(tmp_path) -> None:
    import shutil

    from opengrader.core.comments import (
        CommentLibrary,
        read_comments,
        write_comments,
    )
    from opengrader.tui.ui.screens import CommentLibraryScreen

    submissions = tmp_path / "student-submissions"
    shutil.copytree("samples/cs146-quiz1/student-submissions", submissions)
    write_comments(submissions, CommentLibrary().with_snippet("See Lecture 5"))

    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path=str(submissions),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            await pilot.press("l")
            await pilot.pause()
            assert isinstance(app.screen, CommentLibraryScreen)

            await pilot.press("1")
            await pilot.pause()

            comment = app.get_grade_screen().query_one("#question-comment", Input)
            assert "See Lecture 5" in comment.value

            reloaded = read_comments(submissions)
            assert reloaded.snippets[0].text == "See Lecture 5"
            assert reloaded.snippets[0].uses == 1
            assert reloaded.snippets[0].uses_on("q1") == 1

    asyncio.run(run())


def test_transcription_editor_opens_with_x_and_saves_correction(tmp_path) -> None:
    import shutil

    from opengrader.core.submissions import load_student_submission_markdown

    submissions = tmp_path / "student-submissions"
    shutil.copytree("samples/cs146-quiz1/student-submissions", submissions)

    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path=str(submissions),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            grade_screen = app.get_grade_screen()
            editor = grade_screen.query_one("#transcription-editor")
            assert not editor.has_class("editing")

            await pilot.press("x")
            await pilot.pause()
            assert editor.has_class("editing")

            text_area = grade_screen.query_one("#transcription-text", TextArea)
            text_area.text = "corrected reading via TUI"
            await pilot.click("#transcription-save")
            await pilot.pause()

            assert not editor.has_class("editing")

            first = load_student_submission_markdown(
                submissions / "submission-0001" / "submission-0001.submission.md"
            )
            q1 = next(q for q in first.questions if q.question_id == "q1")
            assert q1.response_text == "corrected reading via TUI"
            assert q1.response_confidence == 100.0

    asyncio.run(run())


def test_comment_library_scope_toggle_reorders_by_question(tmp_path) -> None:
    import shutil

    from opengrader.core.comments import CommentLibrary, write_comments
    from opengrader.tui.ui.screens import CommentLibraryScreen
    from opengrader.tui.ui.screens.comment_library_screen import (
        SCOPE_GLOBAL,
        SCOPE_QUESTION,
        _SnippetItem,
    )

    submissions = tmp_path / "student-submissions"
    shutil.copytree("samples/cs146-quiz1/student-submissions", submissions)
    library = (
        CommentLibrary()
        .with_snippet("global favorite")
        .with_snippet("loop note")
        .record_use("global favorite", "q2")
        .record_use("global favorite", "q3")
        .record_use("loop note", "q1")
    )
    write_comments(submissions, library)

    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path=str(submissions),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("l")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CommentLibraryScreen)

            def top_row_text() -> str:
                items = list(screen.query(_SnippetItem))
                return items[0].snippet.text

            assert screen._scope == SCOPE_GLOBAL
            assert top_row_text() == "global favorite"

            await pilot.press("g")
            await pilot.pause()
            assert screen._scope == SCOPE_QUESTION
            assert top_row_text() == "loop note"

            assert app._comment_library_scope == SCOPE_QUESTION

    asyncio.run(run())


def test_comment_library_saves_a_new_snippet_from_the_modal(tmp_path) -> None:
    import shutil

    from opengrader.core.comments import read_comments
    from opengrader.tui.ui.screens import CommentLibraryScreen

    submissions = tmp_path / "student-submissions"
    shutil.copytree("samples/cs146-quiz1/student-submissions", submissions)

    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path=str(submissions),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("l")
            await pilot.pause()
            assert isinstance(app.screen, CommentLibraryScreen)

            new_input = app.screen.query_one("#comment-library-new", Input)
            new_input.focus()
            new_input.value = "Off-by-one in loop bound"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert isinstance(app.screen, CommentLibraryScreen)
            assert [s.text for s in read_comments(submissions).snippets] == [
                "Off-by-one in loop bound"
            ]

    asyncio.run(run())


def test_grade_sidebar_can_be_resized_with_bracket_hotkeys() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            grade_screen = app.get_grade_screen()
            sidebar = grade_screen.query_one("#grade-sidebar")
            baseline_width = grade_screen.SIDEBAR_MIN_WIDTH + 8
            grade_screen.set_sidebar_width(baseline_width)
            await pilot.pause()

            await pilot.press("]")
            await pilot.pause()
            assert (
                int(sidebar.styles.width.value)
                == baseline_width + grade_screen.SIDEBAR_RESIZE_STEP
            )

            await pilot.press("[")
            await pilot.pause()
            assert int(sidebar.styles.width.value) == baseline_width

    asyncio.run(run())


def test_grade_sidebar_hotkeys_are_ignored_while_comment_input_is_focused() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            grade_screen = app.get_grade_screen()
            sidebar = grade_screen.query_one("#grade-sidebar")
            baseline_width = grade_screen.SIDEBAR_MIN_WIDTH + 8
            grade_screen.set_sidebar_width(baseline_width)
            grade_screen.query_one("#question-comment", Input).focus()
            await pilot.pause()

            await pilot.press("]")
            await pilot.pause()

            assert int(sidebar.styles.width.value) == baseline_width

    asyncio.run(run())


def test_happy_path_selects_qa_then_opens_grading() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            switcher = app.query_one(ContentSwitcher)
            assert switcher.current == "page-grade"
            assert app.workflow_state.selection_labels["student"] == "s1"
            assert app.workflow_state.selection_labels["question"] == "Question 1"

            rubric_list = app.get_grade_screen().query_one("#rubric-list", ListView)
            assert len(rubric_list.children) == 2

    asyncio.run(run())


def test_grade_screen_focuses_rubric_and_number_keys_select_items() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            rubric_list = app.get_grade_screen().query_one("#rubric-list", ListView)
            assert app.focused == rubric_list

            await pilot.press("2")
            await pilot.pause()

            current = app.workflow_state.grading_run.current_question
            assert current is not None
            assert current.selected_option_indices == {1}

    asyncio.run(run())


def test_grade_rubric_rows_wrap_without_checkbox_controls() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=LongRubricFakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            rubric_list = app.get_grade_screen().query_one("#rubric-list", ListView)
            long_item = rubric_list.children[1]

            assert list(rubric_list.query(Checkbox)) == []

            label = long_item.query_one(".rubric-row-label", Static)
            points = long_item.query_one(".rubric-row-points", Static)
            assert str(label.content) == LongRubricFakeDataProvider.LONG_LABEL
            assert str(points.content) == "-3.0"
            assert str(points.content) not in str(label.content)

            await pilot.press("2")
            await pilot.pause()

            current = app.workflow_state.grading_run.current_question
            assert current is not None
            assert current.selected_option_indices == {1}
            assert long_item.has_class("rubric-selected")
            assert str(long_item.query_one(".rubric-row-marker", Static).content) == ">"

    asyncio.run(run())


def test_grade_comment_shortcut_focuses_comment_and_done_keys_return_to_rubric() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            grade_screen = app.get_grade_screen()
            rubric_list = grade_screen.query_one("#rubric-list", ListView)
            assert app.focused == rubric_list

            await pilot.press("c")
            await pilot.pause()

            assert app.focused is not None
            assert app.focused.id == "question-comment"

            comment_text = "Needs clearer calculation"
            await pilot.press(*comment_text)
            await pilot.pause()

            current = app.workflow_state.grading_run.current_question
            assert current is not None
            assert comment_text in current.comment

            await pilot.press("enter")
            await pilot.pause()

            assert app.focused == rubric_list
            assert app.workflow_state.current_page == PAGE_GRADE

            await pilot.press("c")
            await pilot.pause()

            assert app.focused is not None
            assert app.focused.id == "question-comment"

            await pilot.press("escape")
            await pilot.pause()

            assert app.focused == rubric_list
            assert app.workflow_state.current_page == PAGE_GRADE

    asyncio.run(run())


def test_comment_input_e_key_does_not_open_rubric_editor() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            await pilot.press("c")
            await pilot.pause()

            grade_screen = app.get_grade_screen()
            comment_input = grade_screen.query_one("#question-comment", Input)
            assert app.focused == comment_input

            await pilot.press("e")
            await pilot.pause()

            assert app.focused == comment_input
            assert comment_input.value == "e"
            assert grade_screen.rubric_edit_active() is False

    asyncio.run(run())


def test_comment_input_supports_word_shortcuts_without_grade_navigation() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            app.workflow_state.active_student_index = 0
            comment_input = app.get_grade_screen().query_one("#question-comment", Input)
            comment_input.value = "alpha beta gamma"
            comment_input.cursor_position = len(comment_input.value)
            comment_input.focus()
            await pilot.pause()

            await pilot.press("alt+b")
            await pilot.pause()

            assert comment_input.cursor_position == 11
            assert app.workflow_state.active_student_index == 0
            assert app.workflow_state.current_page == PAGE_GRADE

            await pilot.press("alt+f")
            await pilot.pause()

            assert comment_input.cursor_position == len(comment_input.value)
            assert app.workflow_state.active_student_index == 0
            assert app.workflow_state.current_page == PAGE_GRADE

    asyncio.run(run())


def test_grade_rubric_editor_exposes_partial_credit_subitems() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path="samples/cs146-quiz1/student-submissions",
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            await pilot.press("e")
            await pilot.pause()

            grade_screen = app.get_grade_screen()
            subitems = grade_screen.query_one("#rubric-editor-subitems")
            labels = [
                widget.value
                for widget in subitems.query(".rubric-edit-label")
                if isinstance(widget, Input)
            ]
            deltas = [
                widget.value
                for widget in subitems.query(".rubric-edit-delta")
                if isinstance(widget, Input)
            ]

            current = app.workflow_state.grading_run.current_question
            assert current is not None
            expected_partials = [
                option.label.removeprefix("Partially Correct - ")
                for option in current.rubric_options
                if option.kind == "partial"
            ]
            expected_deltas = [
                f"{option.points_delta:+g}"
                for option in current.rubric_options
                if option.kind == "partial"
            ]
            assert labels == expected_partials
            assert deltas == expected_deltas

    asyncio.run(run())


def test_grade_rubric_editor_rejects_mixed_positive_and_negative_scoring() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path="samples/cs146-quiz1/student-submissions",
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()

            grade_screen = app.get_grade_screen()
            subitems = grade_screen.query_one("#rubric-editor-subitems")
            first_delta = next(
                widget
                for widget in subitems.query(".rubric-edit-delta")
                if isinstance(widget, Input)
            )
            first_delta.value = "-2"
            grade_screen.query_one("#rubric-edit-save", Button).press()
            await pilot.pause()

            status = grade_screen.query_one("#rubric-editor-status", Static)
            assert "Correct must use +0 points for deduction rubrics." in str(
                status.content
            )

    asyncio.run(run())


def test_rubric_editor_ctrl_s_saves() -> None:
    async def run() -> None:
        provider = RubricEditingFakeDataProvider()
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=provider,
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()

            grade_screen = app.get_grade_screen()
            subitems = grade_screen.query_one("#rubric-editor-subitems")
            partial_delta = next(
                widget
                for widget in subitems.query(".rubric-edit-delta")
                if isinstance(widget, Input)
            )
            partial_delta.value = "-4"

            await pilot.press("ctrl+s")
            await pilot.pause()

            assert provider.saved_options is not None
            assert provider.saved_options[1] == _seed("Partially Correct", -4.0)
            assert grade_screen.query_one("#rubric-list", ListView).display is True
            assert grade_screen.query_one("#rubric-editor-panel").display is False

    asyncio.run(run())


def test_rubric_editor_authors_default_comment() -> None:
    async def run() -> None:
        provider = RubricEditingFakeDataProvider()
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=provider,
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()

            grade_screen = app.get_grade_screen()
            correct_container = grade_screen.query_one("#rubric-editor-correct")
            comment_input = next(
                widget
                for widget in correct_container.query(".rubric-edit-comment")
                if isinstance(widget, Input)
            )
            comment_input.value = "Well reasoned throughout."

            await pilot.press("ctrl+s")
            await pilot.pause()

            assert provider.saved_options is not None
            correct = next(
                option
                for option in provider.saved_options
                if option.label == "Correct"
            )
            assert correct.default_comment == "Well reasoned throughout."
            incorrect = next(
                option
                for option in provider.saved_options
                if option.label == "Incorrect"
            )
            assert incorrect.default_comment is None

    asyncio.run(run())


def test_rubric_editor_escape_cancels_without_saving() -> None:
    async def run() -> None:
        provider = RubricEditingFakeDataProvider()
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=provider,
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            switcher = app.query_one(ContentSwitcher)
            assert switcher.current == "page-grade"

            await pilot.press("e")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

            grade_screen = app.get_grade_screen()
            assert provider.saved_options is None
            assert switcher.current == "page-grade"
            assert grade_screen.query_one("#rubric-list", ListView).display is True
            assert grade_screen.query_one("#rubric-editor-panel").display is False

    asyncio.run(run())


def test_rubric_editor_enter_on_add_button_adds_subitem() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path="samples/cs146-quiz1/student-submissions",
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()

            grade_screen = app.get_grade_screen()
            subitems = grade_screen.query_one("#rubric-editor-subitems")
            before_count = len(
                [
                    widget
                    for widget in subitems.query(".rubric-edit-label")
                    if isinstance(widget, Input)
                ]
            )
            add_button = grade_screen.query_one("#rubric-edit-add", Button)
            add_button.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            after_labels = [
                widget
                for widget in subitems.query(".rubric-edit-label")
                if isinstance(widget, Input)
            ]
            assert len(after_labels) == before_count + 1
            assert app.focused == after_labels[-1]

    asyncio.run(run())


def test_rubric_editor_tab_is_trapped_inside_editor() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path="samples/cs146-quiz1/student-submissions",
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()

            grade_screen = app.get_grade_screen()
            editor = grade_screen.query_one("#rubric-editor-panel")
            for _ in range(8):
                await pilot.press("tab")
                await pilot.pause()
                assert app.focused is not None
                assert editor in app.focused.ancestors

            await pilot.press("shift+tab")
            await pilot.pause()
            assert app.focused is not None
            assert editor in app.focused.ancestors
            assert app.focused.id != "question-comment"

    asyncio.run(run())


def test_rubric_editor_input_supports_word_shortcuts_inside_focus_trap() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path="samples/cs146-quiz1/student-submissions",
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()

            grade_screen = app.get_grade_screen()
            editor = grade_screen.query_one("#rubric-editor-panel")
            label_input = next(
                widget
                for widget in editor.query(".rubric-edit-label")
                if isinstance(widget, Input) and not widget.disabled
            )
            label_input.value = "alpha beta gamma"
            label_input.cursor_position = len(label_input.value)
            label_input.focus()
            await pilot.pause()

            await pilot.press("alt+b")
            await pilot.pause()

            assert label_input.cursor_position == 11
            assert app.focused == label_input
            assert editor in label_input.ancestors
            assert app.workflow_state.current_page == PAGE_GRADE

    asyncio.run(run())


def test_rubric_editor_validation_error_keeps_focus_trap() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path="samples/cs146-quiz1/student-submissions",
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()

            grade_screen = app.get_grade_screen()
            editor = grade_screen.query_one("#rubric-editor-panel")
            subitems = grade_screen.query_one("#rubric-editor-subitems")
            first_delta = next(
                widget
                for widget in subitems.query(".rubric-edit-delta")
                if isinstance(widget, Input)
            )
            first_delta.value = "-2"

            await pilot.press("ctrl+s")
            await pilot.pause()

            status = grade_screen.query_one("#rubric-editor-status", Static)
            assert "Correct must use +0 points for deduction rubrics." in str(
                status.content
            )
            assert grade_screen.query_one("#rubric-editor-panel").display is True

            await pilot.press("tab")
            await pilot.pause()
            assert app.focused is not None
            assert editor in app.focused.ancestors

    asyncio.run(run())


def test_rubric_editor_blocks_grade_shortcuts_while_editing() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path="samples/cs146-quiz1/student-submissions",
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            active_student_index = app.workflow_state.active_student_index

            await pilot.press("e")
            await pilot.pause()
            save_button = app.get_grade_screen().query_one("#rubric-edit-save", Button)
            save_button.focus()
            await pilot.pause()

            await pilot.press("j")
            await pilot.pause()

            assert app.workflow_state.active_student_index == active_student_index
            assert app.get_grade_screen().query_one(
                "#rubric-editor-panel"
            ).display is True

            await pilot.press("c")
            await pilot.pause()

            editor = app.get_grade_screen().query_one("#rubric-editor-panel")
            assert app.focused is not None
            assert editor in app.focused.ancestors
            assert app.focused.id != "question-comment"

    asyncio.run(run())


def test_escape_from_grade_returns_to_qa_picker() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            switcher = app.query_one(ContentSwitcher)
            assert switcher.current == "page-grade"

            await pilot.press("escape")
            await pilot.pause()

            assert switcher.current == "page-qa-picker"

    asyncio.run(run())


def test_returning_from_question_pane_restores_question_focus() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path="samples/cs146-quiz1/student-submissions",
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            app.get_qa_picker_screen().move_to_index(2)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert app.workflow_state.active_question_index == 2
            await pilot.press("right")
            await pilot.pause()
            assert app.workflow_state.active_student_index == 1

            await pilot.press("escape")
            await pilot.pause()

            qa_screen = app.get_qa_picker_screen()
            qa_list = qa_screen.query_one("#qa-list", ListView)
            student_pane = _student_pane(app)
            student_list = _active_student_list(app)
            preview = qa_screen.query_one("#qa-preview", Static)

            assert qa_list.index == 2
            assert qa_list.children[0].has_class("-highlight") is False
            assert qa_list.children[1].has_class("-highlight") is False
            assert qa_list.children[2].has_class("-highlight") is True
            assert student_list.index == 0
            assert qa_list.has_class("-pane-active") is True
            assert student_pane.has_class("-pane-active") is False
            assert app.focused is qa_list
            assert "most significant bit" in str(preview.content)

    asyncio.run(run())


def test_returning_from_student_pane_restores_student_focus() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path="samples/cs146-quiz1/student-submissions",
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            app.get_qa_picker_screen().move_to_index(2)
            await pilot.pause()
            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("j")
            await pilot.pause()

            qa_screen = app.get_qa_picker_screen()
            student_list = _active_student_list(app)
            assert student_list.index == 1

            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

            qa_list = qa_screen.query_one("#qa-list", ListView)
            student_pane = _student_pane(app)
            student_list = _active_student_list(app)
            preview = qa_screen.query_one("#qa-preview", Static)

            assert qa_list.index == 2
            assert qa_list.children[2].has_class("-highlight") is True
            assert student_list.index == 1
            assert student_list.children[0].has_class("-highlight") is False
            assert student_list.children[1].has_class("-highlight") is True
            assert qa_list.has_class("-pane-active") is False
            assert student_pane.has_class("-pane-active") is True
            assert app.focused is student_list
            assert "Question 3 status for this student" in str(preview.content)

    asyncio.run(run())


def test_finalize_loads_next_student() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert app.workflow_state.selection_labels["student"] == "s1"

            await pilot.press("ctrl+g")
            await pilot.pause()

            assert app.workflow_state.selection_labels["student"] == "s2"
            assert app.workflow_state.active_student_index == 1

    asyncio.run(run())


def test_student_list_shows_progress_after_finalize() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("ctrl+g")
            await pilot.pause()

            app.grade_controller.return_to_qa_picker()
            await pilot.pause()

            student_list = _active_student_list(app)
            assert _list_item_texts(student_list) == [
                "s1 [1/2 graded]",
                "s2 [0/2 graded]",
            ]

    asyncio.run(run())


def test_highlighting_student_updates_details() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            qa_screen = app.get_qa_picker_screen()
            student = qa_screen.selected_student()
            assert student is not None
            qa_screen.show_student_details(student)
            await pilot.pause()

            preview = qa_screen.query_one("#qa-preview", Static)
            assert "s1" in str(preview.content)
            assert "Tommy Liu" not in str(preview.content)
            assert "Progress: 0 / 2 questions graded" in str(preview.content)

    asyncio.run(run())


def test_teacher_mode_toggle_reveals_names_in_details_pane() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            qa_screen = app.get_qa_picker_screen()
            await pilot.press("tab")
            await pilot.pause()

            preview = qa_screen.query_one("#qa-preview", Static)
            assert "Tommy Liu" not in str(preview.content)

            await pilot.press("t")
            await pilot.pause()

            assert app.workflow_state.teacher_mode is True
            assert "Tommy Liu" in str(preview.content)
            status = qa_screen.query_one("#qa-status", Static)
            assert "Teacher mode on" in str(status.content)

            await pilot.press("t")
            await pilot.pause()

            assert app.workflow_state.teacher_mode is False
            assert "Tommy Liu" not in str(preview.content)

    asyncio.run(run())


def test_teacher_mode_toggle_reveals_name_on_grade_screen() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            name_label = app.get_grade_screen().query_one(
                "#grade-student-name", Label
            )
            assert app.workflow_state.selection_labels["student"] == "s1"
            assert "s1" in str(name_label.content)
            assert "Tommy Liu" not in str(name_label.content)

            await pilot.press("t")
            await pilot.pause()

            assert app.workflow_state.selection_labels["student"] == "Tommy Liu"
            assert "Tommy Liu" in str(name_label.content)

            await pilot.press("right")
            await pilot.pause()
            assert app.workflow_state.selection_labels["student"] == "Andy Cortes"

            await pilot.press("t")
            await pilot.pause()
            assert app.workflow_state.selection_labels["student"] == "s2"
            assert "s2" in str(name_label.content)

    asyncio.run(run())


def test_student_list_arrow_keys_move_student_focus_not_questions() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            qa_screen = app.get_qa_picker_screen()
            await pilot.click("#student-id-list", offset=(2, 0))
            await pilot.pause()

            await pilot.press("down")
            await pilot.pause()

            selected_question = qa_screen.selected_question()
            selected_student = qa_screen.selected_student()
            assert selected_question is not None
            assert selected_student is not None
            assert selected_question.item_id == "Q1"
            assert selected_student.label == "Andy Cortes"

            preview = qa_screen.query_one("#qa-preview", Static)
            assert "s2" in str(preview.content)
            assert "Andy Cortes" not in str(preview.content)

    asyncio.run(run())


def test_clicking_qa_pane_after_students_restores_question_navigation() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            qa_screen = app.get_qa_picker_screen()
            await pilot.click("#student-id-list", offset=(2, 0))
            await pilot.pause()
            await pilot.click("#qa-list", offset=(2, 4))
            await pilot.pause()

            await pilot.press("down")
            await pilot.pause()

            selected_question = qa_screen.selected_question()
            selected_student = qa_screen.selected_student()
            assert selected_question is not None
            assert selected_student is not None
            assert selected_question.item_id == "Q2"
            assert selected_student.label == "Tommy Liu"

    asyncio.run(run())


def test_tab_switches_between_question_and_student_panes() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            qa_screen = app.get_qa_picker_screen()
            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()

            selected_question = qa_screen.selected_question()
            selected_student = qa_screen.selected_student()
            assert selected_question is not None
            assert selected_student is not None
            assert selected_question.item_id == "Q1"
            assert selected_student.label == "Andy Cortes"

            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()

            selected_question = qa_screen.selected_question()
            selected_student = qa_screen.selected_student()
            assert selected_question is not None
            assert selected_student is not None
            assert selected_question.item_id == "Q2"
            assert selected_student.label == "Andy Cortes"

    asyncio.run(run())


def test_shift_tab_switches_between_question_and_student_panes() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            qa_screen = app.get_qa_picker_screen()
            qa_list = qa_screen.query_one("#qa-list", ListView)
            student_pane = _student_pane(app)
            assert qa_list.has_class("-pane-active")
            assert not student_pane.has_class("-pane-active")

            await pilot.press("shift+tab")
            await pilot.pause()

            assert not qa_list.has_class("-pane-active")
            assert student_pane.has_class("-pane-active")

            await pilot.press("down")
            await pilot.pause()

            selected_question = qa_screen.selected_question()
            selected_student = qa_screen.selected_student()
            assert selected_question is not None
            assert selected_student is not None
            assert selected_question.item_id == "Q1"
            assert selected_student.label == "Andy Cortes"

            await pilot.press("shift+tab")
            await pilot.pause()

            assert qa_list.has_class("-pane-active")
            assert not student_pane.has_class("-pane-active")

            await pilot.press("down")
            await pilot.pause()

            selected_question = qa_screen.selected_question()
            selected_student = qa_screen.selected_student()
            assert selected_question is not None
            assert selected_student is not None
            assert selected_question.item_id == "Q2"
            assert selected_student.label == "Andy Cortes"

    asyncio.run(run())


def test_qa_picker_question_search_uses_status_bar_filter_mode() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            status_bar = app.query_one(StatusBar)
            await pilot.press("/")
            await pilot.pause()

            search_input = app.query_one("#status-bar-search-input", Input)
            assert status_bar.search_active() is True
            assert app.focused is search_input
            assert (
                "Search questions:"
                in str(
                    status_bar.query_one(
                        "#status-bar-search-prompt",
                        Static,
                    ).content
                )
            )

            await pilot.press("Q", "2")
            await pilot.pause()

            qa_list = app.get_qa_picker_screen().query_one("#qa-list", ListView)
            assert _list_item_texts(qa_list) == ["Question 2 [0/2 graded]"]
            qa_status = app.get_qa_picker_screen().query_one("#qa-status", Static)
            assert (
                "Search questions: 1/2 matches"
                in str(qa_status.content)
            )

            await pilot.press("escape")
            await pilot.pause()

            switcher = app.query_one(ContentSwitcher)
            assert switcher.current == "page-qa-picker"
            assert status_bar.search_active() is False
            assert app.focused is qa_list
            assert _list_item_texts(qa_list) == ["Question 2 [0/2 graded]"]

            await pilot.press("/")
            await pilot.pause()
            await pilot.press("ctrl+u")
            await pilot.pause()

            assert search_input.value == ""
            assert _list_item_texts(qa_list) == [
                "Question 1 [0/2 graded]",
                "Question 2 [0/2 graded]",
            ]

            await pilot.press("Q", "2")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert switcher.current == "page-qa-picker"
            assert status_bar.search_active() is False
            assert app.focused is qa_list

            await pilot.press("enter")
            await pilot.pause()

            assert switcher.current == PAGE_GRADE

    asyncio.run(run())


def test_qa_picker_student_search_is_independent_and_handles_no_matches() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            status_bar = app.query_one(StatusBar)

            await pilot.press("/")
            await pilot.pause()
            await pilot.press("Q", "2")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

            qa_list = app.get_qa_picker_screen().query_one("#qa-list", ListView)
            assert _list_item_texts(qa_list) == ["Question 2 [0/2 graded]"]

            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("/")
            await pilot.pause()

            assert (
                "Search students:"
                in str(
                    status_bar.query_one(
                        "#status-bar-search-prompt",
                        Static,
                    ).content
                )
            )

            await pilot.press("A", "n", "d", "y")
            await pilot.pause()

            student_list = _active_student_list(app)
            assert _list_item_texts(student_list) == ["s2 [0/2 graded]"]
            assert _list_item_texts(qa_list) == ["Question 2 [0/2 graded]"]

            await pilot.press("ctrl+u")
            await pilot.press("z", "z", "z")
            await pilot.pause()

            assert _list_item_texts(student_list) == []
            qa_status = app.get_qa_picker_screen().query_one("#qa-status", Static)
            assert (
                "No matches for 'zzz'"
                in str(qa_status.content)
            )
            assert "No matches for 'zzz'" in str(qa_status.content)
            assert "Select a QA to grade for all students." not in str(
                qa_status.content
            )

            await pilot.press("escape")
            await pilot.pause()

            assert status_bar.search_active() is False
            assert _list_item_texts(student_list) == []

    asyncio.run(run())


def test_escape_on_qa_picker_is_not_a_back_action() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            assert not app.input_controller.command_available(InputCommand.APP_BACK)

            await pilot.press("escape")
            await pilot.pause()

            assert app.workflow_state.current_page == PAGE_QA_PICKER
            status = app.get_qa_picker_screen().query_one("#qa-status", Static)
            assert "Already viewing" not in str(status.content)

    asyncio.run(run())


def test_question_mark_opens_and_closes_help() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("question_mark")
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen)

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, HelpScreen)

    asyncio.run(run())


def test_setup_page_scopes_out_qa_picker_hotkeys() -> None:
    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)

        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.data_provider, StubDataProvider)

            visible = {
                binding.command
                for binding in app.input_controller.visible_bindings()
            }
            # No F2 (Tools *is* where another folder is chosen) and no
            # "Back to grading" (F3), since nothing is open.
            assert visible == {
                InputCommand.APP_QUIT,
                InputCommand.APP_HELP,
                InputCommand.OPEN_FORMAT_GUIDE,
            }

    asyncio.run(run())


def test_ctrl_digit_selects_and_finalizes_rubric() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert "graded" not in app.workflow_state.student_status.values()

            await pilot.press("ctrl+1")
            await pilot.pause()

            assert "graded" in app.workflow_state.student_status.values()

    asyncio.run(run())


def test_shifted_digit_selects_and_finalizes_rubric() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            await pilot.press("exclamation_mark")
            await pilot.pause()

            assert "graded" in app.workflow_state.student_status.values()

    asyncio.run(run())


def test_shifted_digit_beyond_rubric_count_does_nothing() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            await pilot.press("dollar_sign")
            await pilot.pause()

            assert "graded" not in app.workflow_state.student_status.values()

    asyncio.run(run())


def test_shifted_digit_types_into_comment_without_finalizing() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            await pilot.press("c")
            await pilot.pause()
            assert app.focused is not None
            assert app.focused.id == "question-comment"

            await pilot.press("exclamation_mark")
            await pilot.pause()

            comment_input = app.get_grade_screen().query_one("#question-comment", Input)
            assert comment_input.value == "!"
            assert "graded" not in app.workflow_state.student_status.values()

    asyncio.run(run())


def _copy_quiz_fixture(tmp_path):
    import shutil
    from pathlib import Path

    subs = tmp_path / "student-submissions"
    shutil.copytree(
        Path("samples/cs146-quiz1/student-submissions"), subs
    )
    return subs


def test_review_hotkey_opens_modal_and_row_jumps_to_grade(tmp_path) -> None:
    from opengrader.tui.ui.screens import ReviewQueueScreen

    async def run() -> None:
        subs = _copy_quiz_fixture(tmp_path)
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path=str(subs),
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("R")
            await pilot.pause()
            assert isinstance(app.screen, ReviewQueueScreen)

            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, ReviewQueueScreen)
            assert app.workflow_state.current_page == PAGE_GRADE
            assert app.workflow_state.selection.student_item_id in {"s1", "s2"}

    asyncio.run(run())


def test_history_hotkey_opens_modal_after_a_graded_change(tmp_path) -> None:
    from opengrader.tui.ui.screens import GradeHistoryScreen
    from textual.widgets import ListView

    async def run() -> None:
        subs = _copy_quiz_fixture(tmp_path)
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path=str(subs),
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.press("1")
            await pilot.pause()

            await pilot.press("H")
            await pilot.pause()
            assert not isinstance(app.screen, GradeHistoryScreen)

            await pilot.press("t")
            await pilot.press("H")
            await pilot.pause()
            assert isinstance(app.screen, GradeHistoryScreen)
            rows = app.screen.query_one("#history-list", ListView)
            assert len(rows.children) >= 1

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, GradeHistoryScreen)

    asyncio.run(run())


def test_pressing_o_opens_the_current_crop_in_an_external_viewer() -> None:
    import opengrader.tui.ui.widgets.response_viewer as viewer_module

    async def run() -> None:
        opened: list[str] = []
        original = viewer_module.open_in_external_viewer
        viewer_module.open_in_external_viewer = lambda path: (
            opened.append(str(path)) or "crop.png"
        )
        try:
            app = TuiApp(
                default_image_path=DEFAULT_IMAGE_PATH,
                data_provider=FakeDataProvider(),
            )
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                assert app.workflow_state.current_page == PAGE_GRADE

                await pilot.press("o")
                await pilot.pause()

                status = app.get_grade_screen().query_one("#db-status", Static)
                assert "crop.png" in str(status.render())
        finally:
            viewer_module.open_in_external_viewer = original

        assert opened == [DEFAULT_IMAGE_PATH]

    asyncio.run(run())


def test_external_viewer_failure_is_reported_not_raised() -> None:
    import opengrader.tui.ui.widgets.response_viewer as viewer_module
    from opengrader.tui.external_viewer import ExternalViewerError

    async def run() -> None:
        def explode(path):  # noqa: ANN001, ANN202
            raise ExternalViewerError("could not launch 'xdg-open'")

        original = viewer_module.open_in_external_viewer
        viewer_module.open_in_external_viewer = explode
        try:
            app = TuiApp(
                default_image_path=DEFAULT_IMAGE_PATH,
                data_provider=FakeDataProvider(),
            )
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()

                await pilot.press("o")
                await pilot.pause()

                status = app.get_grade_screen().query_one("#db-status", Static)
                assert "xdg-open" in str(status.render())
        finally:
            viewer_module.open_in_external_viewer = original

    asyncio.run(run())


def test_open_in_viewer_is_not_dispatched_while_typing_a_comment() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            app.get_grade_screen().query_one("#question-comment", Input).focus()
            await pilot.pause()

            assert not app.check_action(
                "dispatch_input", (InputCommand.GRADE_OPEN_EXTERNAL.value,)
            )

    asyncio.run(run())


def _student_count(app) -> int:  # noqa: ANN001
    return len(app.data_provider.list_students(course_id=1))


def _partially_extracted_folder(tmp_path):  # noqa: ANN001
    import shutil
    from pathlib import Path

    from opengrader.core.submissions.manifest import ExtractionRecord, append_record

    source = Path("samples/cs146-quiz1/student-submissions")
    folder = tmp_path / "student-submissions"
    folder.mkdir()
    shutil.copytree(source / "submission-0001", folder / "submission-0001")
    append_record(
        folder,
        ExtractionRecord(
            key="submission-0001",
            source="/scans/submission-0001.pdf",
            source_sha256="aaa",
            settings_id="sss",
            outputs=("submission-0001/submission-0001.submission.md",),
        ),
    )

    def arrive() -> None:
        shutil.copytree(source / "submission-0002", folder / "submission-0002")
        append_record(
            folder,
            ExtractionRecord(
                key="submission-0002",
                source="/scans/submission-0002.pdf",
                source_sha256="bbb",
                settings_id="sss",
                outputs=("submission-0002/submission-0002.submission.md",),
            ),
        )

    return folder, arrive


def test_a_submission_that_finishes_later_folds_into_an_open_session(tmp_path) -> None:  # noqa: ANN001

    async def run() -> tuple[int, int]:
        folder, arrive = _partially_extracted_folder(tmp_path)
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path=str(folder),
        )
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            before = _student_count(app)

            arrive()
            app._poll_extraction()
            await pilot.pause()

            return before, _student_count(app)

    before, after = asyncio.run(run())
    assert before == 1
    assert after == 2


def test_the_fold_in_watch_starts_on_a_folder_opened_directly(tmp_path) -> None:  # noqa: ANN001

    async def run() -> bool:
        folder, _arrive = _partially_extracted_folder(tmp_path)
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path=str(folder),
        )
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            return app._extraction_watcher is not None

    assert asyncio.run(run()) is True


def test_grade_while_extracting_opens_the_workspace_with_certainty(tmp_path) -> None:  # noqa: ANN001
    from opengrader.tui.ui.screens.setup_screen import SetupScreen

    async def run() -> tuple[bool, bool, int]:
        folder, _arrive = _partially_extracted_folder(tmp_path)
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            app.post_message(
                SetupScreen.GradeWhileExtractingRequested(
                    "samples/cs146-quiz1/qa.md", str(folder)
                )
            )
            await pilot.pause()
            await pilot.pause()

            watching = app._extraction_watcher is not None
            probe = app._extraction_known_active
            return watching, callable(probe), _student_count(app)

    watching, probe_is_callable, students = asyncio.run(run())
    assert watching is True
    assert probe_is_callable is True
    assert students == 1


def test_the_watch_stops_once_the_extractor_worker_reports_done(tmp_path) -> None:  # noqa: ANN001

    async def run() -> tuple[int, bool]:
        folder, arrive = _partially_extracted_folder(tmp_path)
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path=str(folder),
        )
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            app._extraction_known_active = lambda: False

            arrive()
            app._poll_extraction()
            await pilot.pause()

            return _student_count(app), (
                app._extraction_watcher is None
            )

    students, stopped = asyncio.run(run())
    assert students == 2
    assert stopped is True


#
# `tui` must work with no workspace service. A bare TuiApp opens the start page
# (pipeline steps plus helper tools); Launch Grading opens a folder in place;
# F3 moves between the tools and grading in either mode.


def _copy_sample(tmp_path: Path) -> tuple[Path, Path]:
    import shutil

    target = tmp_path / "cs146-quiz1"
    shutil.copytree("samples/cs146-quiz1", target, ignore=shutil.ignore_patterns(".opengrader"))
    return target / "qa.md", target / "student-submissions"


def test_a_bare_app_opens_tools_and_the_pipeline_can_open_a_folder(tmp_path) -> None:
    from opengrader.tui.data.submission_files_provider import SubmissionFilesProvider
    from opengrader.tui.state import PAGE_SETUP

    qa, subs = _copy_sample(tmp_path)

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            assert app.workflow_state.current_page == PAGE_SETUP
            await pilot.press("f3")  # nothing open: stays put
            await pilot.pause()
            assert app.workflow_state.current_page == PAGE_SETUP

            # What the pipeline card posts when a run leaves submissions.
            setup = app.get_setup_screen()
            setup.post_message(SetupScreen.LaunchRequested(str(qa), str(subs)))
            await pilot.pause()

            assert isinstance(app.data_provider, SubmissionFilesProvider)
            assert app.workflow_state.current_page == PAGE_QA_PICKER
            assert app.workflow_state.session_open
            assert len(app.workflow_state.question_items) == 11

    asyncio.run(run())


def test_f3_is_home_on_the_start_grading_card_and_f2_does_the_same_unlisted(
    tmp_path,
) -> None:
    from opengrader.tui.state import PAGE_SETUP

    qa, subs = _copy_sample(tmp_path)

    async def run() -> tuple[bool, str, str, str | None, str | None, str]:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path=qa,
            submissions_path=subs,
        )
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            assert app.workflow_state.current_page == PAGE_QA_PICKER
            footer = str(app.query_one("#status-bar-bindings", Static).render())
            await pilot.press("f3")
            await pilot.pause()
            assert app.workflow_state.current_page == PAGE_SETUP
            home_focus = app.focused.id if app.focused else None
            visible = {b.command for b in app.input_controller.visible_bindings()}
            assert InputCommand.OPEN_START_PAGE in visible  # "Back to grading"
            await pilot.press("f3")
            await pilot.pause()
            assert app.workflow_state.current_page == PAGE_QA_PICKER
            # Folder mode: F2 stays in the app, on the Start grading card.
            await pilot.press("f2")
            await pilot.pause()
            assert app.workflow_state.current_page == PAGE_SETUP
            return (
                app.is_running,
                app.query_one("#tui-qa-path", Input).value,
                app.query_one("#tui-sub-path", Input).value,
                home_focus,
                app.focused.id if app.focused else None,
                footer,
            )

    running, qa_field, subs_field, home_focus, focused, footer = asyncio.run(run())
    assert running
    assert (qa_field, subs_field) == (str(qa), str(subs))  # what is open
    # The folder just graded is now a recent one, so Home lands on that list.
    assert home_focus == focused == "launch-recent-list"
    assert "Home" in footer
    assert "Switch assignment" not in footer
    assert "Requests" not in footer  # change requests are a shared workspace's



def test_start_grading_finds_the_answer_key_and_submissions_in_a_class_folder(
    tmp_path,
) -> None:
    from opengrader.tui.data.submission_files_provider import SubmissionFilesProvider

    qa, subs = _copy_sample(tmp_path)

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            app.query_one("#tui-class-folder", Input).value = str(qa.parent)
            app.query_one("#launch-button", Button).press()
            await pilot.pause()
            assert isinstance(app.data_provider, SubmissionFilesProvider)
            assert Path(app.data_provider.qa_file_path) == qa
            assert Path(app.data_provider.submissions_path) == subs
            assert app.workflow_state.current_page == PAGE_QA_PICKER

    asyncio.run(run())


def _record_folders(tmp_path: Path, count: int) -> list[Path]:
    """`count` real class folders, recorded oldest first (so the last is newest)."""
    import shutil

    from opengrader.core.recents import record_recent

    folders = []
    for index in range(count):
        folder = tmp_path / f"class-{index}"
        shutil.copytree("samples/cs146-quiz1", folder, ignore=shutil.ignore_patterns(".opengrader"))
        record_recent(
            "folder",
            folder,
            title=f"Class {index}",
            qa_file=folder / "qa.md",
            submissions=folder / "student-submissions",
        )
        folders.append(folder)
    return folders


def test_classic_lists_the_five_newest_class_folders_and_starts_on_them(tmp_path) -> None:
    from opengrader.tui.ui.widgets.recent_list import RecentList

    _record_folders(tmp_path, 7)

    async def run() -> tuple[list[str], str | None, bool]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            await pilot.pause()
            recents = app.query_one("#launch-recent-list", RecentList)
            return (
                [item.title for item in recents.items],
                app.focused.id if app.focused else None,
                app.query_one("#launch-recents").has_class("hidden"),
            )

    titles, focused, hidden = asyncio.run(run())
    assert titles == ["Class 6", "Class 5", "Class 4", "Class 3", "Class 2"]
    assert focused == "launch-recent-list" and not hidden


def test_no_recent_folders_hides_the_list_and_focuses_the_folder_box() -> None:
    async def run() -> tuple[bool, str | None]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            await pilot.pause()
            return (
                app.query_one("#launch-recents").has_class("hidden"),
                app.focused.id if app.focused else None,
            )

    assert asyncio.run(run()) == (True, "tui-class-folder")


def test_choosing_a_recent_folder_opens_it_and_records_it_first(tmp_path) -> None:
    from opengrader.core.recents import load_recents
    from opengrader.tui.data.submission_files_provider import SubmissionFilesProvider

    older, _newer = _record_folders(tmp_path, 2)

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            await pilot.pause()
            await pilot.press("down", "enter")  # the older of the two
            await pilot.pause()
            assert isinstance(app.data_provider, SubmissionFilesProvider)
            assert Path(app.data_provider.submissions_path) == (
                older / "student-submissions"
            ).resolve()
            assert app.workflow_state.current_page == PAGE_QA_PICKER

    asyncio.run(run())
    newest = load_recents()[0]
    assert (newest.kind, newest.title) == ("folder", "CS146: Quiz 1")
    assert Path(newest.path) == older.resolve()


def test_d_forgets_a_recent_folder_and_the_next_one_moves_up(tmp_path) -> None:
    from opengrader.core.recents import load_recents
    from opengrader.tui.ui.widgets.recent_list import RecentList

    _record_folders(tmp_path, 6)

    async def run() -> list[str]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            await pilot.pause()
            await pilot.press("d")  # forgets the newest, "Class 5"
            await pilot.pause()
            await pilot.pause()
            return [item.title for item in app.query_one(RecentList).items]

    titles = asyncio.run(run())
    assert titles == ["Class 4", "Class 3", "Class 2", "Class 1", "Class 0"]
    assert "Class 5" not in [item.title for item in load_recents()]


def test_forgetting_the_last_recent_folder_hides_the_list(tmp_path) -> None:
    _record_folders(tmp_path, 1)

    async def run() -> tuple[bool, str | None]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            return (
                app.query_one("#launch-recents").has_class("hidden"),
                app.focused.id if app.focused else None,
            )

    assert asyncio.run(run()) == (True, "tui-class-folder")


def test_a_missing_recent_folder_is_marked_and_does_not_open(tmp_path) -> None:
    import shutil

    from opengrader.tui.ui.widgets.recent_list import RecentList

    [folder] = _record_folders(tmp_path, 1)
    shutil.rmtree(folder)

    async def run() -> tuple[bool, str]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            await pilot.pause()
            row = app.query_one(RecentList).children[0]
            await pilot.press("enter")
            await pilot.pause()
            return row.has_class("recent-missing"), app.workflow_state.current_page

    missing, page = asyncio.run(run())
    from opengrader.tui.state import PAGE_SETUP

    assert missing and page == PAGE_SETUP


def test_a_workspace_session_records_no_class_folder(workspace_provider) -> None:
    from opengrader.core.recents import load_recents

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH, data_provider=workspace_provider)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()

    asyncio.run(run())
    assert [item for item in load_recents() if item.kind == "folder"] == []


def test_a_workspace_session_is_grading_only_and_f2_exits_to_its_list(
    workspace_provider,
) -> None:
    async def run() -> object:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH, data_provider=workspace_provider)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            assert not app.has_tools_page
            assert not app.query(SetupScreen)  # never composed
            visible = {b.command for b in app.input_controller.visible_bindings()
                       if app._input_command_available(b.command)}
            assert InputCommand.OPEN_START_PAGE not in visible
            assert InputCommand.SWITCH_ASSIGNMENT in visible
            assert InputCommand.OPEN_CHANGE_REQUESTS in visible
            await pilot.press("f3")  # no Tools to go to
            await pilot.pause()
            assert app.workflow_state.current_page == PAGE_QA_PICKER
            await pilot.press("f2")
            await pilot.pause()
        return app.return_value

    assert asyncio.run(run()) == "switch_assignment"


def test_grade_screen_names_the_provider_and_undo_keeps_it(tmp_path) -> None:
    import shutil

    submissions = tmp_path / "student-submissions"
    shutil.copytree("samples/cs146-quiz1/student-submissions", submissions)
    first = submissions / "submission-0001" / "submission-0001.submission.md"
    original = first.read_text()
    # The sample carries no reading of its own; give q1 one, in the order the
    # file writer puts these fields.
    reading = "  - Read by: tesseract (Test Cloud)\n  - Confidence: 72\n  - Response text: "
    assert "  - Response text: " in original
    first.write_text(original.replace("  - Response text: ", reading, 1))

    async def run() -> tuple[str, str, str]:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/cs146-quiz1/qa.md",
            submissions_path=str(submissions),
        )
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            chip = str(app.get_grade_screen().query_one("#question-confidence").render())

            app.grade_controller.on_transcription_updated("corrected by a grader")
            await pilot.pause()
            after_edit = first.read_text()

            app.grade_controller.undo_last_grade_change()
            await pilot.pause()
            return chip, after_edit, first.read_text()

    chip, after_edit, after_undo = asyncio.run(run())
    assert "72% · read by Test Cloud" in chip
    assert "  - Read by: human\n" in after_edit
    assert "  - Read by: tesseract (Test Cloud)\n" in after_undo
