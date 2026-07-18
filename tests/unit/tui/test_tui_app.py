import asyncio
from dataclasses import replace

from textual.widgets import (
    Button,
    Checkbox,
    ContentSwitcher,
    Footer,
    Input,
    Label,
    ListView,
    Static,
)

from opengrader.tui.controllers.input_controller import InputCommand
from opengrader.tui.data import RubricEditResult, RubricOptionSeed, StudentSummary
from opengrader.tui.data.stub_data_provider import StubDataProvider
from opengrader.tui.state import PAGE_GRADE
from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.widgets.sharp_image import SharpImage
from opengrader.tui.ui.widgets.status_bar import StatusBar
from opengrader.tui.ui.widgets.tabbed_list_pane import TabbedListPane

from .fakes import DEFAULT_IMAGE_PATH, FakeDataProvider


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


def _status_bar_text(app: TuiApp) -> str:
    status_bar = app.query_one(StatusBar)
    return str(status_bar.query_one("#status-bar-bindings", Static).content)


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


def test_stub_data_provider_reads_roster_sid_as_student_id() -> None:
    provider = StubDataProvider(DEFAULT_IMAGE_PATH)

    students = provider.list_students(course_id=1)

    assert [student.student_id for student in students[:2]] == ["12345", "67890"]


def test_app_uses_custom_status_bar_instead_of_textual_footer() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            assert not list(app.query(Footer))
            assert app.query_one("#app-status-bar", StatusBar) is not None

            status_text = _status_bar_text(app)
            assert "j/down Next QA" in status_text
            assert "/ Filter" in status_text
            assert "^\\ palette" not in status_text

            await pilot.press("enter")
            await pilot.pause()

            grade_status_text = _status_bar_text(app)
            assert "ctrl+k/right Next Student" in grade_status_text
            assert "c Comment" in grade_status_text

    asyncio.run(run())


def test_app_loads_file_backed_grading_queue() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/qa-multi.md",
            submissions_path="samples/cs146-quiz1/student-submissions",
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            assert app.workflow_state.selection_labels["course"] == "CS146"
            assert app.workflow_state.selection_labels["assignment"] == "CS146: Quiz 1"
            assert [item.item_id for item in app.workflow_state.question_items] == [
                "q1",
                "q2",
                "q3",
                "q4",
                "q5a",
                "q5b",
                "q5c",
                "q6a",
                "q6b",
                "q6c",
                "q6d",
            ]
            student_list = _active_student_list(app)
            student_labels = _list_item_texts(student_list)
            assert len(student_labels) == 2
            assert student_labels[0].startswith("123456789 [")
            assert student_labels[0].endswith("/11 graded]")
            assert student_labels[1].startswith("987654321 [")
            assert student_labels[1].endswith("/11 graded]")

    asyncio.run(run())


def test_grade_screen_shows_source_pdf_name_for_file_backed_runs() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/qa-multi.md",
            submissions_path="samples/cs146-quiz1/student-submissions",
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            pdf_label = app.query_one("#grade-source-pdf", Label)
            assert pdf_label.display is True
            assert str(pdf_label.content).startswith("PDF: submission-000")
            assert str(pdf_label.content).endswith(".pdf")

    asyncio.run(run())


def test_grade_screen_hides_source_pdf_label_without_source_pdf() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            pdf_label = app.query_one("#grade-source-pdf", Label)
            assert pdf_label.display is False

    asyncio.run(run())


def test_student_pane_id_tab_displays_submitted_ids() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/qa-multi.md",
            submissions_path="samples/cs146-quiz1/student-submissions",
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            pane = _student_pane(app)
            assert pane.active_tab_index == 0
            id_labels = _list_item_texts(_active_student_list(app))
            assert id_labels[0].startswith("123456789 [")
            assert id_labels[0].endswith("/11 graded]")
            assert id_labels[1].startswith("987654321 [")
            assert id_labels[1].endswith("/11 graded]")

            # The Name tab is hidden while grading anonymously.
            pane.action_pane_activate_tab(1)
            await pilot.pause()
            assert pane.active_tab_index == 0

            await pilot.press("t")
            await pilot.pause()
            pane.action_pane_activate_tab(1)
            await pilot.pause()

            name_labels = _list_item_texts(_active_student_list(app))
            assert name_labels[0].startswith("Tommy Liu [")
            assert name_labels[0].endswith("/11 graded]")
            assert name_labels[1].startswith("submission-0002.submission [")
            assert name_labels[1].endswith("/11 graded]")

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

            # Anonymous grading: names are unavailable, d only hints at t.
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

            # Turning teacher mode off while on the Name tab snaps back to IDs.
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
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/qa-multi.md",
            submissions_path="samples/cs146-quiz1/student-submissions",
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
                "987654321 [0/11 graded]"
            ]

            app.workflow_state.teacher_mode = True
            qa_screen.apply_teacher_mode()
            _student_pane(app).action_pane_activate_tab(1)
            await pilot.pause()

            assert _list_item_texts(_active_student_list(app)) == [
                "submission-0002.submission [0/11 graded]"
            ]

    asyncio.run(run())


def test_qa_preview_shows_question_statistics() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=StatisticsFakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            preview = app.get_qa_picker_screen().query_one("#qa-preview", Static)
            content = str(preview.content)
            assert "Avg:" in content
            assert "Median:" in content
            assert "Std dev:" in content
            assert "Distribution" not in content
            chart = app.get_qa_picker_screen().query_one(
                "#qa-statistics-chart",
                SharpImage,
            )
            assert chart.display is True

    asyncio.run(run())


def test_file_backed_app_opens_student_specific_grading_context() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/qa-multi.md",
            submissions_path="samples/cs146-quiz1/student-submissions",
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            current = app.workflow_state.grading_run.current_question
            assert current is not None
            assert app.workflow_state.grading_run.max_points == 85
            assert current.question_id == "q1"
            assert current.prompt.startswith("Convert decimal ")
            # The regenerated samples carry no OCR transcription, so the
            # student-specific context is asserted via the image crops.
            assert "/student-submissions/submission-" in current.image_path
            assert current.image_path.endswith("/responses/q1.png")

            # The section title names the question; the context below starts
            # with the score so "Question" never appears twice.
            title = app.get_grade_screen().query_one(
                "#question-section-title", Label
            )
            assert str(title.content) == "Question 1"
            context = app.get_grade_screen().query_one("#question-context", Static)
            assert str(context.content).startswith("Score: ")
            assert "Question" not in str(context.content)
            assert current.prompt in str(context.content)

    asyncio.run(run())


def test_grade_sidebar_width_is_bounded_when_resized() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/qa-multi.md",
            submissions_path="samples/cs146-quiz1/student-submissions",
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            grade_screen = app.get_grade_screen()
            sidebar = grade_screen.query_one("#grade-sidebar")

            grade_screen.set_sidebar_width(1)
            await pilot.pause()
            assert int(sidebar.styles.width.value) == grade_screen.SIDEBAR_MIN_WIDTH

            grade_screen.set_sidebar_width(10_000)
            await pilot.pause()
            assert int(sidebar.styles.width.value) <= grade_screen.SIDEBAR_MAX_WIDTH

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


def test_grade_rubric_editor_is_inline_and_structured() -> None:
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
            assert grade_screen.query_one("#rubric-list", ListView).display is False
            assert grade_screen.query_one("#rubric-editor-panel").display is True
            label_inputs = [
                widget
                for widget in grade_screen.query(".rubric-edit-label")
                if isinstance(widget, Input)
            ]
            delta_inputs = [
                widget
                for widget in grade_screen.query(".rubric-edit-delta")
                if isinstance(widget, Input)
            ]
            assert [input.value for input in label_inputs] == [
                "Correct",
                "Partially Correct",
                "Incorrect",
            ]
            assert [input.value for input in delta_inputs] == ["+0", "-5", "-10"]

    asyncio.run(run())


def test_grade_rubric_editor_exposes_partial_credit_subitems() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/qa-multi.md",
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
            qa_file_path="samples/qa-multi.md",
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
            # A negative delta flips the rubric to deduction mode, so the
            # positive Correct row is rejected with a mode-specific message.
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


def test_rubric_editor_enter_on_save_button_saves() -> None:
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
            save_button = grade_screen.query_one("#rubric-edit-save", Button)
            save_button.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert provider.saved_options is not None
            assert grade_screen.query_one("#rubric-list", ListView).display is True
            assert grade_screen.query_one("#rubric-editor-panel").display is False

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


def test_rubric_editor_space_on_cancel_button_cancels_without_saving() -> None:
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
            cancel_button = grade_screen.query_one("#rubric-edit-cancel", Button)
            cancel_button.focus()
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()

            assert provider.saved_options is None
            assert grade_screen.query_one("#rubric-list", ListView).display is True
            assert grade_screen.query_one("#rubric-editor-panel").display is False

    asyncio.run(run())


def test_rubric_editor_enter_on_add_button_adds_subitem() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/qa-multi.md",
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
            qa_file_path="samples/qa-multi.md",
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
            qa_file_path="samples/qa-multi.md",
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
            qa_file_path="samples/qa-multi.md",
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
            qa_file_path="samples/qa-multi.md",
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
            qa_file_path="samples/qa-multi.md",
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
            # The preview shows the highlighted question's prompt (q3).
            assert "most significant bit" in str(preview.content)

    asyncio.run(run())


def test_returning_from_student_pane_restores_student_focus() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            qa_file_path="samples/qa-multi.md",
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

            # Anonymous grading by default: the details pane shows the
            # student ID, never the name.
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
            await pilot.press("tab")  # focus the students pane
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

            # The revealed name follows student navigation until toggled off.
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


def test_qa_picker_move_binding_label_tracks_active_pane() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            assert (
                app.check_action(
                    "dispatch_input",
                    (InputCommand.QA_MOVE_DOWN.value,),
                )
                is True
            )
            assert (
                app.check_action(
                    "dispatch_input",
                    (InputCommand.QA_MOVE_STUDENT_DOWN.value,),
                )
                is False
            )
            assert app.screen.active_bindings["j"].binding.description == "Next QA"
            assert "j/down Next QA" in _status_bar_text(app)
            assert "j/down Next Student" not in _status_bar_text(app)

            await pilot.press("tab")
            await pilot.pause()

            assert (
                app.check_action(
                    "dispatch_input",
                    (InputCommand.QA_MOVE_DOWN.value,),
                )
                is False
            )
            assert (
                app.check_action(
                    "dispatch_input",
                    (InputCommand.QA_MOVE_STUDENT_DOWN.value,),
                )
                is True
            )
            assert app.screen.active_bindings["j"].binding.description == "Next Student"
            assert "j/down Next Student" in _status_bar_text(app)
            assert "j/down Next QA" not in _status_bar_text(app)

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


def test_escape_on_qa_picker_shows_status_message() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

            status = app.get_qa_picker_screen().query_one("#qa-status", Static)
            assert "Already viewing the grading queue." in str(status.content)

    asyncio.run(run())


def test_default_app_uses_stub_data_provider() -> None:
    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)

        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.data_provider, StubDataProvider)

    asyncio.run(run())


def test_textual_command_palette_is_disabled() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            assert app.use_command_palette is False
            assert "ctrl+backslash" not in app.screen.active_bindings

    asyncio.run(run())


def test_dispatch_input_check_action_uses_single_command_parameter() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            assert (
                app.check_action(
                    "dispatch_input",
                    (InputCommand.QA_MOVE_DOWN.value,),
                )
                is True
            )
            assert (
                app.check_action(
                    "dispatch_input",
                    ("standard", InputCommand.QA_MOVE_DOWN.value),
                )
                is False
            )

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


def test_alt_digit_selects_and_finalizes_rubric() -> None:
    async def run() -> None:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH,
            data_provider=FakeDataProvider(),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            await pilot.press("alt+1")
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

            # shift+1 on a US layout: the protocol-independent ctrl+1 fallback.
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

            # shift+4 targets rubric slot 4, but only 2 options exist.
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
