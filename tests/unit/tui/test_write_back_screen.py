"""The write-back screen, over a real workspace and a copy of the sample class."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Button, Static

from opengrader.core.grading.edits import GradeEdit
from opengrader.core.submissions.student_file import (
    StudentSubmissionGradeUpdate,
    load_student_submission_markdown,
    update_student_submission_file,
)
from opengrader.core.workspace.intake import publish_pipeline
from opengrader.core.workspace.models import AssignmentInputs, PipelineOutput
from opengrader.core.workspace.store import Workspace
from opengrader.tui.ui.screens.write_back_screen import WriteBackScreen

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def cls(tmp_path: Path):
    root = tmp_path / "quiz"
    shutil.copytree(REPO / "samples" / "cs146-quiz1", root, ignore=shutil.ignore_patterns(".opengrader"))
    store = Workspace.create(root)
    published = publish_pipeline(
        store,
        store.assignment_id_for_folder(root),
        PipelineOutput(
            qa_markdown=(root / "qa.md").read_text(encoding="utf-8"),
            submissions_dir=str((root / "student-submissions").resolve()),
        ),
    )
    aid = published.assignment.id
    store.save_assignment_inputs(
        AssignmentInputs(
            id=aid,
            folder=".",
            scan="",
            output=".",
            submissions="student-submissions",
        )
    )
    sid = store.submissions(aid)[0].id
    file = sorted((root / "student-submissions").rglob("*.submission.md"))[0]

    def ws_grade(question: str, points: float) -> None:
        answer = store.answer(aid, sid, question)
        store.save_grade(
            aid, sid, question,
            GradeEdit(
                request_id=uuid.uuid4(),
                expected_context_revision=answer.context_revision,
                expected_revision=answer.revision,
                rubric_labels=[], analysis="", score_mode="manual", points_awarded=points,
            ),
        )

    return root, aid, file, ws_grade


class _Host(App):
    def __init__(self, screen: WriteBackScreen) -> None:
        super().__init__()
        self._screen = screen
        self.result = None

    def on_mount(self) -> None:
        self.push_screen(self._screen, lambda value: setattr(self, "result", value))


async def _settle(pilot, predicate, limit: int = 100) -> None:  # noqa: ANN001
    for _ in range(limit):
        await pilot.pause()
        await asyncio.sleep(0.01)
        if predicate():
            return


def _points(file: Path, question: str):
    return {q.question_id: q for q in load_student_submission_markdown(file).questions}[
        question
    ].points_awarded


def test_preview_then_write_with_a_conflict_decided_for_the_workspace(cls) -> None:
    root, aid, file, ws_grade = cls
    ws_grade("q1", 5)
    ws_grade("q3", 1)
    update_student_submission_file(
        file, StudentSubmissionGradeUpdate(question_id="q3", points_awarded=2.0, rubric_labels=())
    )

    async def run() -> tuple[str, float | None]:
        app = _Host(WriteBackScreen(workspace_root=root, assignment_id=aid, title="Quiz"))
        async with app.run_test(size=(130, 45)) as pilot:
            screen = app.screen
            await _settle(pilot, lambda: not screen.query_one("#wb-apply", Button).disabled)
            summary = str(screen.query_one("#wb-summary", Static).render())
            before = _points(file, "q1")  # a preview writes nothing
            await pilot.press("t")  # the focused conflict takes the workspace
            screen.query_one("#wb-apply", Button).press()
            await _settle(pilot, lambda: "Wrote" in _body(screen))
            await pilot.press("escape")
            await pilot.pause()
        return summary, before

    summary, before = asyncio.run(run())

    assert "1 to copy" in summary and "1 to decide" in summary
    assert before is None
    assert _points(file, "q1") == 5.0
    assert _points(file, "q3") == 1.0


def test_nothing_to_write_disables_the_button(cls) -> None:
    root, aid, _file, _ws = cls

    async def run() -> tuple[bool, str]:
        app = _Host(WriteBackScreen(workspace_root=root, assignment_id=aid))
        async with app.run_test(size=(130, 45)) as pilot:
            screen = app.screen
            await _settle(pilot, lambda: "agree" in str(screen.query_one("#wb-summary", Static).render()))
            return screen.query_one("#wb-apply", Button).disabled, _body(screen)

    disabled, body = asyncio.run(run())

    assert disabled and "Nothing to write" in body


def test_an_assignment_without_a_folder_explains_itself(cls) -> None:
    root, _aid, _file, _ws = cls
    web = Workspace(root).create_assignment("---\ntitle: Web\n---\n- (1 points) Q\n")

    async def run() -> str:
        app = _Host(WriteBackScreen(workspace_root=root, assignment_id=web.id))
        async with app.run_test(size=(130, 45)) as pilot:
            screen = app.screen
            await _settle(pilot, lambda: "Cannot" in str(screen.query_one("#wb-summary", Static).render()))
            return str(screen.query_one("#wb-summary", Static).render())

    assert "no available source submissions folder" in asyncio.run(run())


def _body(screen) -> str:  # noqa: ANN001
    return " ".join(str(w.render()) for w in screen.query("#wb-body Static, #wb-body Label"))
