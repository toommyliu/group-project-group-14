"""The terminal grades through the same operation as the browser."""

import asyncio
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from textual.widgets import Button, Input, ListView, Static

from opengrader.core.grading.edits import GradeEdit
from opengrader.core.submissions import load_student_submission_markdown
from opengrader.core.workspace.intake import publish_submission
from opengrader.core.workspace.models import Answer
from opengrader.core.workspace.store import Workspace
from opengrader.server.workspace.app import create_app
from opengrader.server.workspace.client import (
    ServiceConnection,
    ServiceError,
    WorkspaceClient,
)
from opengrader.tui.data.workspace_provider import WorkspaceProvider
from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens.grade_conflict_screen import GradeConflictScreen
from opengrader.tui.workspace_home import PROCESS_WORKSPACE, WorkspaceHome


@pytest.fixture
def grading_workspace(tmp_path: Path):
    store = Workspace.create(tmp_path)
    assignment = store.create_assignment(
        "- (2 points) Two plus two?\n  - **Answer**\n    - 4\n"
    )
    extracted = tmp_path / "extraction.submission.md"
    extracted.write_text(
        "---\nstudent_name: Student\nstudent_id: '123'\n---\n- q1:\n  - Response text: 4\n  - Possible points: 2\n"
    )
    publish_submission(
        store,
        assignment.id,
        load_student_submission_markdown(extracted),
        asset_root=tmp_path,
        source_key="scan-1",
    )
    with TestClient(create_app(tmp_path, process_jobs=False)) as http:

        class Client(WorkspaceClient):
            def request(self, method, path, body=None, **kwargs):
                response = http.request(
                    method, path, json=body.model_dump(mode="json") if body else None
                )
                if response.is_error:
                    data = response.json()
                    raise ServiceError(
                        response.status_code,
                        data["detail"],
                        Answer.model_validate(data["current"])
                        if data.get("current")
                        else None,
                    )
                return response.content

        stop_events = Mock()
        client = Client(
            ServiceConnection(
                root=str(tmp_path),
                url="http://testserver",
                pid=0,
                workspace_id=store.info().id,
            )
        )
        client.observe = Mock(return_value=stop_events)
        provider = WorkspaceProvider(client, assignment)
        try:
            yield store, assignment, provider, stop_events
        finally:
            provider.close()


def test_terminal_saves_grade_and_pending_feedback_before_switching(grading_workspace):
    store, assignment, provider, stop_events = grading_workspace

    async def grade():
        app = TuiApp(data_provider=provider)
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            app.grade_controller.open_question_by_index(0)
            await pilot.pause()
            app.grade_controller.on_rubric_selected(0, finalize=False)
            await pilot.pause()
            saved = store.answer(assignment.id, 1, "q1")
            assert saved.points_awarded == 2
            assert saved.revision == "1"

            await pilot.press("c")
            app.get_grade_screen().query_one(
                "#question-comment", Input
            ).value = "Keep this feedback"
            await pilot.pause()
            assert store.answer(assignment.id, 1, "q1").analysis != "Keep this feedback"
            await pilot.press("f2")
        assert app.return_value == "switch_assignment"
        assert store.answer(assignment.id, 1, "q1").analysis == "Keep this feedback"
        assert provider.journal.value.answers == {}
        stop_events.assert_called_once_with()

    asyncio.run(grade())


def test_switch_assignment_stays_on_conflicting_feedback(grading_workspace):
    from uuid import uuid4

    store, assignment, provider, stop_events = grading_workspace

    async def grade():
        app = TuiApp(data_provider=provider)
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            app.grade_controller.open_question_by_index(0)
            await pilot.pause()
            await pilot.press("c")
            app.get_grade_screen().query_one(
                "#question-comment", Input
            ).value = "My draft"
            await pilot.pause()
            before = store.answer(assignment.id, 1, "q1")
            store.save_grade(
                assignment.id,
                1,
                "q1",
                GradeEdit(
                    request_id=uuid4(),
                    expected_revision=before.revision,
                    expected_context_revision=before.context_revision,
                    rubric_labels=[],
                    analysis="Another terminal saved",
                ),
            )
            await pilot.press("f2")
            await pilot.pause()
            assert isinstance(app.screen, GradeConflictScreen)
            assert app.return_value is None
            assert provider.journal.value.answers["1/q1"].edit.analysis == "My draft"
            assert (
                store.answer(assignment.id, 1, "q1").analysis
                == "Another terminal saved"
            )
            stop_events.assert_not_called()

    asyncio.run(grade())


def test_picker_can_switch_and_home_can_select_its_only_assignment(grading_workspace):
    _, assignment, provider, _ = grading_workspace

    async def choose():
        app = TuiApp(data_provider=provider)
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause()
            await pilot.press("f2")
        assert app.return_value == "switch_assignment"

        home = WorkspaceHome(provider.client)
        async with home.run_test() as pilot:
            await pilot.pause()
            assert len(home.query_one("#assignments", ListView).children) == 1
            await pilot.press("enter")
        assert home.return_value == assignment.id

    asyncio.run(choose())


def test_empty_workspace_can_process_its_folder(grading_workspace):
    store, assignment, provider, _ = grading_workspace
    store.delete_assignment(assignment.id)

    async def set_up():
        home = WorkspaceHome(provider.client)
        async with home.run_test(size=(100, 35)) as pilot:
            await pilot.pause()

            assert home.query_one("#assignment-picker").display is False
            assert home.query_one("#empty-workspace").display is True
            command = str(home.query_one("#cli-command", Static).content)
            assert command.startswith("opengrader run ")
            assert "uv run" not in command
            assert command.count(str(store.root)) == 2

            process = home.query_one("#process-folder", Button)
            assert str(process.label) == "Process this folder"
            await pilot.click("#process-folder")

        assert home.return_value == PROCESS_WORKSPACE

    asyncio.run(set_up())


def save_remote_grade(
    store, assignment, *, student=1, points=2, analysis="Remote feedback"
):
    before = store.answer(assignment.id, student, "q1")
    cursor = store.info().event_cursor
    store.save_grade(
        assignment.id,
        student,
        "q1",
        GradeEdit(
            request_id=uuid4(),
            expected_revision=before.revision,
            expected_context_revision=before.context_revision,
            rubric_labels=[],
            score_mode="manual",
            points_awarded=points,
            analysis=analysis,
        ),
    )
    return next(event for event in store.events(cursor) if event.kind == "grade")


def add_student(store, assignment):
    source = store.root / "second.submission.md"
    source.write_text(
        "---\nstudent_name: Second\nstudent_id: '456'\n---\n- q1:\n  - Response text: 5\n  - Possible points: 2\n"
    )
    publish_submission(
        store,
        assignment.id,
        load_student_submission_markdown(source),
        asset_root=store.root,
        source_key="scan-2",
    )


def test_live_grades_keep_up_without_moving_or_loading_the_whole_class(
    grading_workspace,
):
    store, assignment, provider, _ = grading_workspace
    add_student(store, assignment)
    provider.client.request = Mock(wraps=provider.client.request)

    async def grade():
        app = TuiApp(data_provider=provider)
        async with app.run_test(size=(120, 45)) as pilot:
            app.grade_controller.open_question_by_index(0, student_index=1)
            await pilot.press("c")
            comment = app.get_grade_screen().query_one("#question-comment", Input)
            receive = provider.client.observe.call_args.args[1]
            for points in (2, 1, 0, 2):
                provider.client.request.reset_mock()
                event = save_remote_grade(
                    store,
                    assignment,
                    student=2,
                    points=points,
                    analysis=f"Score {points}",
                )
                await asyncio.to_thread(receive, event)
                await pilot.pause(0.2)
                current = app.workflow_state.grading_run.current_question
                assert current.points_awarded == points
                assert comment.value == f"Score {points}"
                assert app.focused is comment
                assert app.workflow_state.selection.student_item_id == "s2"
                assert app.workflow_state.selection.question_id == "q1"
                assert provider.journal.value.answers == {}
                paths = [
                    call.args[1] for call in provider.client.request.call_args_list
                ]
                assert not any("/submissions/1/" in path for path in paths)

            await pilot.press("escape")
            app.grade_controller.on_rubric_selected(0, finalize=False)
            rubric = app.get_grade_screen().query_one("#rubric-list", ListView)
            rubric.index = 1
            event = save_remote_grade(store, assignment, student=2, points=1)
            await asyncio.to_thread(receive, event)
            await pilot.pause(0.2)
            assert app.workflow_state.grading_run.current_question.points_awarded == 1
            assert rubric.index == 1
            assert app.focused is rubric

    asyncio.run(grade())


def test_live_updates_preserve_unsaved_comment_and_original_conflict_revision(
    grading_workspace,
):
    store, assignment, provider, _ = grading_workspace
    add_student(store, assignment)

    async def grade():
        app = TuiApp(data_provider=provider)
        async with app.run_test(size=(120, 45)) as pilot:
            app.grade_controller.open_question_by_index(0, student_index=1)
            await pilot.press("c")
            comment = app.get_grade_screen().query_one("#question-comment", Input)
            original_revision = provider.loaded["2/q1"].revision
            comment.value = "My unsaved comment"
            # Refresh before Input.Changed is dispatched to exercise the typing race.
            event = save_remote_grade(store, assignment, student=2)
            app._workspace_changed(event)
            app._refresh_workspace()
            await pilot.pause()
            assert comment.value == "My unsaved comment"
            assert provider.loaded["2/q1"].revision == event.revision
            draft = provider.journal.value.answers["2/q1"]
            assert draft.edit.expected_revision == original_revision
            assert draft.edit.analysis == "My unsaved comment"
            await pilot.press("enter")
            assert isinstance(app.screen, GradeConflictScreen)
            assert store.answer(assignment.id, 2, "q1").analysis == "Remote feedback"
            await pilot.click("#saved")
            await pilot.pause()
            assert provider.journal.value.answers == {}
            assert app.workflow_state.selection.student_item_id == "s2"
            assert comment.value == "Remote feedback"

    asyncio.run(grade())


def test_updates_wait_for_dialog_and_retry_failed_refresh_without_another_event(
    grading_workspace,
):
    store, assignment, provider, _ = grading_workspace

    async def grade():
        app = TuiApp(data_provider=provider)
        async with app.run_test(size=(120, 45)) as pilot:
            app.grade_controller.open_question_by_index(0)
            await pilot.press("?")
            assert len(app.screen_stack) == 2
            dialog = app.screen
            event = save_remote_grade(store, assignment)
            app._workspace_changed(event)
            app._refresh_workspace()
            assert app.screen is dialog
            assert (
                app.workflow_state.grading_run.current_question.points_awarded is None
            )
            request = provider.client.request
            provider.client.request = Mock(
                side_effect=ServiceError(0, "Temporarily unavailable")
            )
            await pilot.press("escape")
            app._refresh_workspace()
            assert app._workspace_events
            assert app._workspace_retry_at > 0
            provider.client.request = request
            app._workspace_retry_at = 0
            app._refresh_workspace()
            await pilot.pause()
            assert app.workflow_state.grading_run.current_question.points_awarded == 2
            assert not app._workspace_events

    asyncio.run(grade())


def test_picker_refresh_preserves_filter_and_selection_and_shows_new_submissions(
    grading_workspace,
):
    store, assignment, provider, _ = grading_workspace

    async def grade():
        app = TuiApp(data_provider=provider)
        async with app.run_test(size=(120, 45)) as pilot:
            picker = app.get_qa_picker_screen()
            cursor = store.info().event_cursor
            add_student(store, assignment)
            for event in store.events(cursor):
                app._workspace_changed(event)
            app._refresh_workspace()
            await pilot.pause()
            assert len(picker._students) == 2
            picker.focus_students()
            picker.start_filter()
            picker.update_filter("456")
            await pilot.pause()
            focused = app.focused
            assert picker.selected_student().item_id == "s2"
            event = save_remote_grade(store, assignment, student=2)
            app._workspace_changed(event)
            app._refresh_workspace()
            await pilot.pause()
            assert picker.selected_student().item_id == "s2"
            assert picker._student_filter == "456"
            assert len(picker._students) == 1
            assert picker._question_statistics["q1"].graded_count == 1
            assert picker._question_statistics["q1"].average == 2
            assert app.focused is focused

    asyncio.run(grade())
