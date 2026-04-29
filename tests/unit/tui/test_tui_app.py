import asyncio

from textual.widgets import Button, ContentSwitcher, Input, ListView, Static

from opengrader.tui.controllers.input_controller import InputCommand
from opengrader.tui.data import RubricEditResult, RubricOptionSeed
from opengrader.tui.data.stub_data_provider import StubDataProvider
from opengrader.tui.tui import TuiApp

from .fakes import DEFAULT_IMAGE_PATH, FakeDataProvider


class RubricEditingFakeDataProvider(FakeDataProvider):
    def __init__(self) -> None:
        self.saved_options: list[RubricOptionSeed] | None = None

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
            assert _list_item_texts(student_list) == [
                "Tommy Liu [0/11 graded]",
                "submission-0002.submission [0/11 graded]",
            ]

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
            assert current.prompt == "Convert decimal 5 to binary [A]."
            assert current.response_text == "no"
            assert current.image_path.endswith("responses/submission-0001/q1.png")

            context = app.get_grade_screen().query_one("#question-context", Static)
            assert "Convert decimal 5 to binary [A]." in str(context.content)
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
            assert current.selected_option_index == 1

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
                "Incorrect",
            ]
            assert [input.value for input in delta_inputs] == ["+0", "-10"]

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

            assert labels == [
                "Binary form attempted but value incorrect",
                "Conversion process mostly incorrect",
            ]
            assert deltas == ["-3", "-7"]

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
            first_delta.value = "+2"
            grade_screen.query_one("#rubric-edit-save", Button).press()
            await pilot.pause()

            status = grade_screen.query_one("#rubric-editor-status", Static)
            assert "positive awards" in str(status.content)

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
