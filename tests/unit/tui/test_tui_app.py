import asyncio
from dataclasses import replace

from textual.widgets import Button, Checkbox, ContentSwitcher, Input, ListView, Static

from opengrader.tui.controllers.input_controller import InputCommand
from opengrader.tui.data import RubricEditResult, RubricOptionSeed
from opengrader.tui.data.stub_data_provider import StubDataProvider
from opengrader.tui.state import PAGE_GRADE
from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.widgets.sharp_image import SharpImage

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


def _list_item_texts(list_view: ListView) -> list[str]:
    return [
        f"{item.nav.label} [{item.nav.meta}]" if item.nav.meta else item.nav.label
        for item in list_view.children
        if hasattr(item, "nav")
    ]


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
            student_list = app.get_qa_picker_screen().query_one(
                "#student-list",
                ListView,
            )
            assert _list_item_texts(student_list) == [
                "Tommy Liu [0/2 graded]",
                "Andy Cortes [0/2 graded]",
            ]

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
            student_list = app.get_qa_picker_screen().query_one(
                "#student-list",
                ListView,
            )
            student_labels = _list_item_texts(student_list)
            assert len(student_labels) == 2
            assert student_labels[0].startswith("Tommy Liu [")
            assert student_labels[0].endswith("/11 graded]")
            assert student_labels[1].startswith("submission-0002.submission [")
            assert student_labels[1].endswith("/11 graded]")

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
            assert current.response_text
            assert "/student-submissions/submission-" in current.image_path
            assert current.image_path.endswith("/responses/q1.png")

            context = app.get_grade_screen().query_one("#question-context", Static)
            assert current.prompt in str(context.content)
            assert "Response text:" in str(context.content)

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
            assert app.workflow_state.selection_labels["student"] == "Tommy Liu"
            assert app.workflow_state.selection_labels["question"] == "Q1"

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
            assert "positive awards" in str(status.content)

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
            assert "positive awards" in str(status.content)
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
            await pilot.press("j")
            await pilot.pause()
            assert app.workflow_state.active_student_index == 1

            await pilot.press("escape")
            await pilot.pause()

            qa_screen = app.get_qa_picker_screen()
            qa_list = qa_screen.query_one("#qa-list", ListView)
            student_list = qa_screen.query_one("#student-list", ListView)
            preview = qa_screen.query_one("#qa-preview", Static)

            assert qa_list.index == 2
            assert qa_list.children[0].has_class("-highlight") is False
            assert qa_list.children[1].has_class("-highlight") is False
            assert qa_list.children[2].has_class("-highlight") is True
            assert student_list.index == 0
            assert qa_list.has_class("-pane-active") is True
            assert student_list.has_class("-pane-active") is False
            assert app.focused is qa_list
            assert "q3" in str(preview.content)

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
            student_list = qa_screen.query_one("#student-list", ListView)
            assert student_list.index == 1

            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

            qa_list = qa_screen.query_one("#qa-list", ListView)
            student_list = qa_screen.query_one("#student-list", ListView)
            preview = qa_screen.query_one("#qa-preview", Static)

            assert qa_list.index == 2
            assert qa_list.children[2].has_class("-highlight") is True
            assert student_list.index == 1
            assert student_list.children[0].has_class("-highlight") is False
            assert student_list.children[1].has_class("-highlight") is True
            assert qa_list.has_class("-pane-active") is False
            assert student_list.has_class("-pane-active") is True
            assert app.focused is student_list
            assert "Highlighted QA: q3" in str(preview.content)

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

            assert app.workflow_state.selection_labels["student"] == "Tommy Liu"

            await pilot.press("ctrl+g")
            await pilot.pause()

            assert app.workflow_state.selection_labels["student"] == "Andy Cortes"
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

            student_list = app.get_qa_picker_screen().query_one(
                "#student-list",
                ListView,
            )
            assert _list_item_texts(student_list) == [
                "Tommy Liu [1/2 graded]",
                "Andy Cortes [0/2 graded]",
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
            assert "Tommy Liu" in str(preview.content)
            assert "Progress: 0 / 2 questions graded" in str(preview.content)

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
            await pilot.click("#student-list", offset=(2, 1))
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
            assert "Andy Cortes" in str(preview.content)

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
            await pilot.click("#student-list", offset=(2, 1))
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
            student_list = qa_screen.query_one("#student-list", ListView)
            assert qa_list.has_class("-pane-active")
            assert not student_list.has_class("-pane-active")

            await pilot.press("shift+tab")
            await pilot.pause()

            assert not qa_list.has_class("-pane-active")
            assert student_list.has_class("-pane-active")

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
            assert not student_list.has_class("-pane-active")

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
