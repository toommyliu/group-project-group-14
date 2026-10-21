"""The AI assistant from a workspace's assignment list (A): the classic
dialogs pointed at a workspace assignment, merged back through its service."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from textual.app import App
from textual.widgets import Button, RichLog

from opengrader.core.agent import load_tasks, record_grade, suggest
from opengrader.core.agent.workspace import list_workspace_packets, prepare_workspace_packet
from opengrader.core.submissions import load_student_submission_markdown
from opengrader.core.workspace.intake import publish_submission
from opengrader.core.workspace.models import AssignmentInputs
from opengrader.core.workspace.store import Workspace
from opengrader.server.workspace.app import create_app
from opengrader.server.workspace.client import ServiceConnection, ServiceError, WorkspaceClient
from opengrader.tui.ui.screens.assistant_screen import (
    AssistantScreen,
    KeyFileChoiceScreen,
    SuggestionsScreen,
    WorkspaceAgentMergeScreen,
    WorkspaceAssistantMenu,
    WorkspaceTarget,
)
from opengrader.tui.workspace_home import WorkspaceHome

QA = """---
title: Quiz 1
---

- (10 points) Explain what a loop does.
  - **Correct**
    - repeats code
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
"""

SUBMISSION = """---
student_name: "Ada Lovelace"
student_id: "123456789"
assignment_title: "Quiz 1"
---


- q1:
  - Question type: Free Response
  - Prompt: (10 points) Explain what a loop does.
  - Response image: ![q1 response](responses/q1.png)
  - Response text: it runs code again
  - Possible points: 10
  - Points:
  - Rubric:
  - Analysis:
"""

_SIZE = (130, 46)


@pytest.fixture
def ws(tmp_path: Path):
    from PIL import Image

    root = tmp_path / "course"
    student = root / "quiz1" / "submissions" / "sub-0001"
    (student / "responses").mkdir(parents=True)
    (student / "sub-0001.submission.md").write_text(SUBMISSION, encoding="utf-8")
    Image.new("RGB", (300, 80), "white").save(student / "responses" / "q1.png")
    (root / "quiz1" / "qa.md").write_text(QA, encoding="utf-8")
    store = Workspace.create(root)
    assignment = store.create_assignment(QA)
    store.save_assignment_inputs(
        AssignmentInputs(id=assignment.id, folder="quiz1", scan="", output="", submissions="submissions", qa="qa.md")
    )
    publish_submission(
        store, assignment.id, load_student_submission_markdown(student / "sub-0001.submission.md"),
        asset_root=student, source_key="sub-0001",
    )
    with TestClient(create_app(root, process_jobs=False)) as http:

        class Client(WorkspaceClient):
            def request(self, method, path, body=None, **kwargs):  # noqa: ANN001, ANN003, ANN201
                response = http.request(method, path, content=body.model_dump_json() if body else None,
                                        headers={"Content-Type": "application/json"})
                if response.is_error:
                    raise ServiceError(response.status_code, str(response.json()["detail"]))
                return response.content

        client = Client(ServiceConnection(root=str(root), url="http://testserver", pid=0, workspace_id=store.info().id))
        client.observe = Mock(return_value=Mock())
        target = WorkspaceTarget(root.resolve(), assignment.id, "Quiz 1", client.request)
        yield {"store": store, "aid": assignment.id, "client": client, "target": target, "root": root}


class _Host(App):
    def __init__(self, screen) -> None:  # noqa: ANN001
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


async def _settle(pilot, predicate, limit: int = 300) -> None:  # noqa: ANN001
    for _ in range(limit):
        await pilot.pause()
        await asyncio.sleep(0.01)
        if predicate():
            return


def _text(screen) -> str:  # noqa: ANN001
    return "\n".join(line.text for line in screen.query_one(RichLog).lines)


def _copy(ws):  # noqa: ANN001, ANN202
    return prepare_workspace_packet(ws["root"], ws["aid"], check_crops=False).path


def test_a_on_the_assignment_list_offers_the_jobs_that_fit(ws) -> None:  # noqa: ANN001
    async def run():  # noqa: ANN202
        home = WorkspaceHome(ws["client"])
        async with home.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await pilot.press("a")
            await _settle(pilot, lambda: isinstance(home.screen, WorkspaceAssistantMenu))
            menu = home.screen
            shown = {b.id for b in menu.query(Button) if b.display}
            return shown, str(menu.query_one("#ws-assistant-state").content)

    shown, state = asyncio.run(run())

    assert {"ws-assistant-key", "ws-assistant-grade", "ws-assistant-rubrics"} <= shown
    assert "Quiz 1" in state and "1 submission(s)" in state


def test_an_account_that_joined_is_not_offered_the_assistant(ws) -> None:  # noqa: ANN001
    ws["client"].connection.account = "bob"
    ws["client"].connection.owner = "prof"

    async def run():  # noqa: ANN202
        home = WorkspaceHome(ws["client"])
        async with home.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            return type(home.screen).__name__

    assert asyncio.run(run()) != "WorkspaceAssistantMenu"


def test_a_copy_is_made_from_the_workspace_and_merged_back_through_its_service(ws) -> None:  # noqa: ANN001
    async def make():  # noqa: ANN202
        screen = AssistantScreen(workspace=ws["target"])
        app = _Host(screen)
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await pilot.click("#assistant-prepare")
            await _settle(pilot, lambda: "Open your AI assistant" in _text(screen))
            return _text(screen)

    text = asyncio.run(make())
    [packet] = list_workspace_packets(ws["root"], ws["aid"])
    assert packet.parent == ws["root"].resolve().parent / "course-ai-assistant"
    assert "1 answer(s) still to grade" in text
    [task] = load_tasks(packet)
    record_grade(packet, task.id, ["Correct"], confidence=0.9, model="claude")

    async def merge():  # noqa: ANN202
        screen = AssistantScreen(workspace=ws["target"])
        app = _Host(screen)
        async with app.run_test(size=_SIZE) as pilot:
            await _settle(pilot, lambda: not screen.query_one("#assistant-merge", Button).disabled)
            await pilot.click("#assistant-merge")
            await _settle(pilot, lambda: isinstance(app.screen, WorkspaceAgentMergeScreen))
            dialog = app.screen
            await _settle(pilot, lambda: not dialog.query_one("#ws-merge-apply", Button).disabled)
            preview = _text(dialog)
            saved_before = ws["store"].answer(ws["aid"], 1, "q1").points_awarded
            await pilot.click("#ws-merge-apply")
            await _settle(pilot, lambda: "Saved" in _text(dialog))
            return preview, saved_before, _text(dialog)

    preview, before, after = asyncio.run(merge())

    assert "Ada Lovelace · q1: Correct" in preview and before is None
    assert "Saved 1 AI grade" in after
    assert ws["store"].answer(ws["aid"], 1, "q1").points_awarded == 10


def test_accepting_a_fix_asks_whether_qa_md_follows(ws) -> None:  # noqa: ANN001
    packet = _copy(ws)
    made = suggest(packet, question="q1", kind="answer", message="Also right", correct=["runs it again"])
    qa_file = ws["root"] / "quiz1" / "qa.md"

    async def run():  # noqa: ANN202
        screen = SuggestionsScreen(packet=packet, workspace=ws["target"])
        app = _Host(screen)
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await pilot.click(f"#accept-{made.id}")
            await _settle(pilot, lambda: isinstance(app.screen, KeyFileChoiceScreen))
            asked = isinstance(app.screen, KeyFileChoiceScreen)
            await pilot.click("#key-file-leave")
            await _settle(pilot, lambda: not screen.query(f"#accept-{made.id}"))
            return asked, _text(screen)

    asked, log = asyncio.run(run())

    assert asked and "now accepts" in log
    assert "runs it again" in ws["store"].assignment_source(ws["aid"])
    assert qa_file.read_text() == QA
