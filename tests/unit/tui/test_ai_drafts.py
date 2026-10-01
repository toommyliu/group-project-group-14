"""Pilot-driven coverage of the two AI drafting features on the grade screen.

1. "Draft with AI" in the rubric editor fills the editor rows from the
   answers students gave; nothing is written until the editor's Save.
2. "AI draft grades" (shift+Y) grades every still-ungraded student on the
   question, one model call per distinct answer, tagged for AI review.

The model is always a fake injected by patching `app_config.text_generate`
(and `ensure_ready`, which would otherwise probe a real server), so no test
here can reach the network. Each test grades a folder written into tmp_path.
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from textual.widgets import Button, Input, Static

from opengrader.core import app_config
from opengrader.core.app_config import manifests as ai_manifests
from opengrader.core.grading import parse_ai_analysis
from opengrader.core.llm import LLMError
from opengrader.tui.data.submission_files_provider import SubmissionFilesProvider
from opengrader.tui.state import PAGE_GRADE
from opengrader.tui.tui import TuiApp

from .fakes import DEFAULT_IMAGE_PATH

_SIZE = (140, 45)

_LIFO = "last in first out"
_FIFO = "first in first out"
_PREGRADED = "a queue of plates"

# Students in file order: s1..s5.
_RESPONSES = [_LIFO, _LIFO, _FIFO, "", _PREGRADED]

_QA = """---
title: Data Structures Quiz
---

- (10 points) In what order does a stack remove items?
  - **Correct**
    - Last in, first out
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
"""

_DRAFT_JSON = json.dumps(
    {
        "partial_credit": [
            {
                "description": "Reversed the order",
                "deduction": 6,
                "comment": "A stack is LIFO, not FIFO.",
            },
            {
                "description": "No explanation of why",
                "deduction": 4,
                "comment": "Say why the newest item leaves first.",
            },
        ],
        "rationale": "Several students described a queue instead.",
    }
)


def _write_submission(path: Path, *, name: str, response: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                f'student_name: "{name}"',
                'student_id: "1"',
                'course_code: "CMPE"',
                'assignment_title: "Quiz"',
                'assignment_version: ""',
                'source_pdf: "student.pdf"',
                'student_score: ""',
                "---",
                "",
                "- q1:",
                "  - Question type: Free Response",
                "  - Prompt: In what order does a stack remove items?",
                "  - Response image: ",
                "  - Response text: |",
                f"    {response}",
                "  - Possible points: ",
                "  - Points: ",
                "  - Analysis: ",
                "",
            ]
        )
    )


@pytest.fixture
def course(tmp_path: Path) -> tuple[Path, Path]:
    qa_path = tmp_path / "qa.md"
    qa_path.write_text(_QA)
    submissions = tmp_path / "student-submissions"
    for index, response in enumerate(_RESPONSES, start=1):
        _write_submission(
            submissions / f"student-{index}" / f"student-{index}.submission.md",
            name=f"Student {index}",
            response=response,
        )
    # s5 already carries a human grade that must never be touched.
    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    provider.save_question_grade(
        student_item_id="s5",
        question_id="q1",
        possible_points=10.0,
        points_awarded=0.0,
        rubric_labels=("Incorrect",),
        analysis="That is a queue.",
    )
    return qa_path, submissions


def _fresh_seeds(qa_path: Path, submissions: Path) -> dict[str, object]:
    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    return {
        f"s{index}": provider.build_question_seeds(student_item_id=f"s{index}")[0]
        for index in range(1, len(_RESPONSES) + 1)
    }


class FakeModel:
    """Stands in for the model; records every prompt it is sent."""

    def __init__(self, reply=None, *, gate: threading.Event | None = None) -> None:  # noqa: ANN001
        self.prompts: list[str] = []
        self.formats: list[object] = []
        self._reply = reply
        self._gate = gate

    def factory(self, resolved, response_format=None):  # noqa: ANN001, ANN201
        self.formats.append(response_format)
        return self

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self._gate is not None:
            self._gate.wait(timeout=10)
        if callable(self._reply):
            return self._reply(prompt)
        return self._reply


def _grade_reply(prompt: str) -> str:
    student_part = prompt.split("Student's response:", 1)[1]
    if _LIFO in student_part:
        return json.dumps(
            {"rubric_labels": ["Correct"], "confidence": 0.95, "rationale": "LIFO."}
        )
    return json.dumps(
        {"rubric_labels": ["Incorrect"], "confidence": 0.3, "rationale": "Unsure."}
    )


@pytest.fixture
def local_ai(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """The default local provider, with readiness probing stubbed out."""
    real = app_config.resolve()
    assert real.kind == "local"
    monkeypatch.setattr(
        app_config, "resolve", lambda *a, **k: replace(real, min_confidence=0.7)
    )
    ready_calls: list[str] = []
    monkeypatch.setattr(
        app_config,
        "ensure_ready",
        lambda resolved=None, *, purpose="vision": ready_calls.append(purpose),
    )
    return ready_calls


def _use_model(monkeypatch: pytest.MonkeyPatch, model: FakeModel) -> None:
    monkeypatch.setattr(app_config, "text_generate", model.factory)


async def _settle(pilot, predicate=lambda: True, limit: int = 200) -> None:  # noqa: ANN001
    for _ in range(limit):
        await pilot.pause()
        await asyncio.sleep(0.01)
        if predicate():
            return


def _app(qa_path: Path, submissions: Path) -> TuiApp:
    return TuiApp(
        default_image_path=DEFAULT_IMAGE_PATH,
        qa_file_path=qa_path,
        submissions_path=submissions,
    )


async def _open_grade_screen(pilot, app: TuiApp) -> None:  # noqa: ANN001
    await pilot.pause()
    await pilot.press("enter")
    await _settle(pilot, lambda: app.workflow_state.current_page == PAGE_GRADE)
    assert app.workflow_state.current_page == PAGE_GRADE


async def _press(app: TuiApp, selector: str) -> None:
    """Press a rubric-editor button (the sidebar may be scrolled off-screen)."""
    app.get_grade_screen().query_one(selector, Button).press()


def _editor_rows(app: TuiApp, container: str) -> list[tuple[str, str, str]]:
    rows = app.get_grade_screen().query_one(container)
    labels = [w.value for w in rows.query(".rubric-edit-label") if isinstance(w, Input)]
    deltas = [w.value for w in rows.query(".rubric-edit-delta") if isinstance(w, Input)]
    comments = [
        w.value for w in rows.query(".rubric-edit-comment") if isinstance(w, Input)
    ]
    return list(zip(labels, deltas, comments))


def _editor_status(app: TuiApp) -> str:
    return str(
        app.get_grade_screen().query_one("#rubric-editor-status", Static).content
    )


def _grade_status(app: TuiApp) -> str:
    return str(app.get_grade_screen().query_one("#db-status", Static).content)


# ---------------------------------------------------------------------------
# Feature 1: Draft with AI in the rubric editor
# ---------------------------------------------------------------------------


def test_draft_with_ai_fills_the_editor_and_only_save_writes_it(
    course, local_ai, monkeypatch  # noqa: ANN001
) -> None:
    qa_path, submissions = course
    model = FakeModel(_DRAFT_JSON)
    _use_model(monkeypatch, model)
    qa_before = qa_path.read_text()

    async def run() -> dict[str, object]:
        app = _app(qa_path, submissions)
        async with app.run_test(size=_SIZE) as pilot:
            await _open_grade_screen(pilot, app)
            await pilot.press("e")
            await pilot.pause()
            assert app.get_grade_screen().rubric_edit_active()

            await _press(app, "#rubric-edit-draft")
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: "AI draft from" in _editor_status(app))

            result = {
                "correct": _editor_rows(app, "#rubric-editor-correct"),
                "partial": _editor_rows(app, "#rubric-editor-subitems"),
                "incorrect": _editor_rows(app, "#rubric-editor-incorrect"),
                "status": _editor_status(app),
                "qa_after_draft": qa_path.read_text(),
                "still_editing": app.get_grade_screen().rubric_edit_active(),
            }

            await _press(app, "#rubric-edit-save")
            await _settle(pilot, lambda: not app.get_grade_screen().rubric_edit_active())
            result["save_status"] = _grade_status(app)
            result["rubric_labels"] = [
                option.label
                for option in app.workflow_state.grading_run.current_question.rubric_options
            ]
        return result

    result = asyncio.run(run())

    assert len(model.prompts) == 1
    # Blank answers are never sampled; the three written answers are.
    assert _LIFO in model.prompts[0] and _FIFO in model.prompts[0]
    assert "given by 2 students" in model.prompts[0]
    assert result["correct"] == [("Correct", "+0", "")]
    assert result["partial"] == [
        ("Reversed the order", "-6", "A stack is LIFO, not FIFO."),
        ("No explanation of why", "-4", "Say why the newest item leaves first."),
    ]
    assert result["incorrect"] == [("Incorrect", "-10", "")]
    status = result["status"]
    assert "AI draft from 3 answers (4 students)" in status
    assert "Nothing is saved yet" in status
    assert "Several students described a queue instead." in status
    # Drafting wrote nothing and left the editor open.
    assert result["still_editing"] is True
    assert result["qa_after_draft"] == qa_before

    # Save persisted the draft, comments included, and hints at shift+Y.
    qa_after = qa_path.read_text()
    assert "Reversed the order (-6)" in qa_after
    assert "No explanation of why (-4)" in qa_after
    assert "A stack is LIFO, not FIFO." in qa_after
    assert result["rubric_labels"] == [
        "Correct",
        "Partially Correct - Reversed the order",
        "Partially Correct - No explanation of why",
        "Incorrect",
    ]
    assert "Rubric saved." in result["save_status"]
    assert "Press Y" in result["save_status"]


def test_a_draft_that_drops_an_option_in_use_says_so(
    course, local_ai, monkeypatch  # noqa: ANN001
) -> None:
    qa_path, submissions = course
    SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions,
        default_image_path=DEFAULT_IMAGE_PATH,
    ).save_question_grade(
        student_item_id="s3",
        question_id="q1",
        possible_points=10.0,
        points_awarded=5.0,
        rubric_labels=("Partially Correct",),
    )
    _use_model(monkeypatch, FakeModel(_DRAFT_JSON))

    async def run() -> str:
        app = _app(qa_path, submissions)
        async with app.run_test(size=_SIZE) as pilot:
            await _open_grade_screen(pilot, app)
            await pilot.press("e")
            await pilot.pause()
            await _press(app, "#rubric-edit-draft")
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: "AI draft from" in _editor_status(app))
            return _editor_status(app)

    status = asyncio.run(run())
    assert "saved grades use 'Partially Correct'" in status


def test_ctrl_r_in_the_editor_also_drafts(course, local_ai, monkeypatch) -> None:  # noqa: ANN001
    qa_path, submissions = course
    model = FakeModel(_DRAFT_JSON)
    _use_model(monkeypatch, model)

    async def run() -> list[tuple[str, str, str]]:
        app = _app(qa_path, submissions)
        async with app.run_test(size=_SIZE) as pilot:
            await _open_grade_screen(pilot, app)
            await pilot.press("e")
            await pilot.pause()
            await pilot.press("ctrl+r")
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: "AI draft from" in _editor_status(app))
            return _editor_rows(app, "#rubric-editor-subitems")

    rows = asyncio.run(run())
    assert [label for label, _, _ in rows] == [
        "Reversed the order",
        "No explanation of why",
    ]


def test_a_draft_arriving_after_the_editor_closed_is_not_applied(
    course, local_ai, monkeypatch  # noqa: ANN001
) -> None:
    qa_path, submissions = course
    gate = threading.Event()
    model = FakeModel(_DRAFT_JSON, gate=gate)
    _use_model(monkeypatch, model)
    qa_before = qa_path.read_text()

    async def run() -> tuple[bool, list, str, list]:
        app = _app(qa_path, submissions)
        async with app.run_test(size=_SIZE) as pilot:
            await _open_grade_screen(pilot, app)
            await pilot.press("e")
            await pilot.pause()
            await _press(app, "#rubric-edit-draft")
            await _settle(pilot, lambda: bool(model.prompts))
            # Leave the editor while the model is still "thinking".
            await pilot.press("escape")
            await pilot.pause()
            assert not app.get_grade_screen().rubric_edit_active()
            # Re-open it: a new session must not receive the old draft either.
            await pilot.press("e")
            await pilot.pause()
            gate.set()
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: "arrived after" in _grade_status(app))
            return (
                app.get_grade_screen().rubric_edit_active(),
                _editor_rows(app, "#rubric-editor-subitems"),
                _grade_status(app),
                [
                    o.label
                    for o in app.workflow_state.grading_run.current_question.rubric_options
                ],
            )

    editing, partial_rows, status, labels = asyncio.run(run())
    assert editing is True
    # The re-opened editor still shows the original single partial option.
    assert [label for label, _, _ in partial_rows] == ["Partially Correct"]
    assert "was not applied" in status
    assert labels == ["Correct", "Partially Correct", "Incorrect"]
    assert qa_path.read_text() == qa_before


def test_draft_with_ai_needs_a_written_answer(tmp_path, local_ai, monkeypatch) -> None:  # noqa: ANN001
    qa_path = tmp_path / "qa.md"
    qa_path.write_text(_QA)
    submissions = tmp_path / "student-submissions"
    for index in (1, 2):
        _write_submission(
            submissions / f"student-{index}" / f"student-{index}.submission.md",
            name=f"Student {index}",
            response="",
        )
    model = FakeModel(_DRAFT_JSON)
    _use_model(monkeypatch, model)

    async def run() -> str:
        app = _app(qa_path, submissions)
        async with app.run_test(size=_SIZE) as pilot:
            await _open_grade_screen(pilot, app)
            await pilot.press("e")
            await pilot.pause()
            await _press(app, "#rubric-edit-draft")
            await _settle(pilot)
            return _editor_status(app)

    status = asyncio.run(run())
    assert "at least one non-blank student answer" in status
    assert model.prompts == []
    assert local_ai == []


def test_a_model_failure_while_drafting_is_reported_and_the_app_keeps_working(
    course, local_ai, monkeypatch  # noqa: ANN001
) -> None:
    qa_path, submissions = course

    def boom(prompt: str) -> str:
        raise LLMError("connection refused")

    model = FakeModel(boom)
    _use_model(monkeypatch, model)

    async def run() -> tuple[str, list, bool]:
        app = _app(qa_path, submissions)
        async with app.run_test(size=_SIZE) as pilot:
            await _open_grade_screen(pilot, app)
            await pilot.press("e")
            await pilot.pause()
            await _press(app, "#rubric-edit-draft")
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: "failed" in _editor_status(app))
            status = _editor_status(app)
            rows = _editor_rows(app, "#rubric-editor-subitems")
            # Still usable: cancel the editor and grade by hand.
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("1")
            await pilot.pause()
            graded = (
                app.workflow_state.grading_run.current_question.points_awarded == 10
            )
            return status, rows, graded

    status, rows, graded = asyncio.run(run())
    assert "The AI call failed: connection refused" in status
    assert [label for label, _, _ in rows] == ["Partially Correct"]
    assert graded is True


# ---------------------------------------------------------------------------
# Feature 2: AI draft grades for the question
# ---------------------------------------------------------------------------


def test_draft_grades_grade_each_distinct_answer_once_and_await_review(
    course, local_ai, monkeypatch  # noqa: ANN001
) -> None:
    qa_path, submissions = course
    model = FakeModel(_grade_reply)
    _use_model(monkeypatch, model)
    before = _fresh_seeds(qa_path, submissions)

    async def run() -> dict[str, object]:
        app = _app(qa_path, submissions)
        async with app.run_test(size=_SIZE) as pilot:
            await _open_grade_screen(pilot, app)
            current_before = app.workflow_state.selection.student_item_id
            await pilot.press("Y")
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: "AI drafted grades" in _grade_status(app))
            controller = app.grade_controller
            result = {
                "status": _grade_status(app),
                "current_before": current_before,
                "current_points": (
                    app.workflow_state.grading_run.current_question.points_awarded
                ),
                "unconfirmed": [
                    app.workflow_state.student_items[i].item_id
                    for i in controller._unconfirmed_ai_indices("q1")
                ],
                "log": [
                    entry.summary
                    for entry in app.workflow_state.grading_log.entries_latest_first()
                ],
            }
            await pilot.press("v")
            await pilot.pause()
            result["review_mode"] = app.workflow_state.ai_review_mode
            result["review_status"] = _grade_status(app)
        return result

    result = asyncio.run(run())

    # One call per distinct written answer: s1+s2 share one; s3 is its own;
    # s4 is blank and s5 already graded, so neither is sent.
    assert len(model.prompts) == 2
    assert sum(_LIFO in p.split("Student's response:", 1)[1] for p in model.prompts) == 1
    assert not any(_PREGRADED in p for p in model.prompts)
    assert model.formats and all(fmt is not None for fmt in model.formats)
    assert local_ai == ["text"]

    after = _fresh_seeds(qa_path, submissions)
    for sid in ("s1", "s2"):
        seed = after[sid]
        assert seed.points_awarded == 10
        assert seed.selected_rubric_labels == ("Correct",)
        tag = parse_ai_analysis(seed.analysis)
        assert tag is not None and not tag.reviewed
    # Low confidence: left for the instructor, untouched.
    assert after["s3"].points_awarded is None
    assert after["s3"].selected_rubric_labels == ()
    assert after["s4"].points_awarded is None
    # The pre-existing human grade is exactly as it was.
    assert after["s5"].points_awarded == before["s5"].points_awarded == 0
    assert after["s5"].selected_rubric_labels == before["s5"].selected_rubric_labels
    assert after["s5"].analysis == before["s5"].analysis == "That is a queue."

    status = result["status"]
    assert "AI drafted grades for 2 students (1 distinct answer)" in status
    assert "1 left for you (low confidence)" in status
    assert "press v to confirm them" in status
    assert result["unconfirmed"] == ["s1", "s2"]
    assert result["log"][0] == "AI draft grades · 2 students · 1 answer · unconfirmed"
    # The student on screen was drafted, and the screen shows it.
    assert result["current_before"] == "s1"
    assert result["current_points"] == 10
    assert result["review_mode"] is True
    assert "Confirming AI grades: 2" in result["review_status"]


def test_draft_grades_never_overwrite_a_grade_given_while_the_model_ran(
    course, local_ai, monkeypatch  # noqa: ANN001
) -> None:
    qa_path, submissions = course
    gate = threading.Event()
    model = FakeModel(_grade_reply, gate=gate)
    _use_model(monkeypatch, model)

    async def run() -> str:
        app = _app(qa_path, submissions)
        async with app.run_test(size=_SIZE) as pilot:
            await _open_grade_screen(pilot, app)
            assert app.workflow_state.selection.student_item_id == "s1"
            await pilot.press("Y")
            await _settle(pilot, lambda: bool(model.prompts))
            # The grader gives s1 "Partially Correct" by hand meanwhile.
            await pilot.press("2")
            await pilot.pause()
            gate.set()
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: "AI drafted grades" in _grade_status(app))
            return _grade_status(app)

    status = asyncio.run(run())
    after = _fresh_seeds(qa_path, submissions)
    assert after["s1"].selected_rubric_labels == ("Partially Correct",)
    assert after["s1"].points_awarded == 5
    assert parse_ai_analysis(after["s1"].analysis or "") is None
    assert after["s2"].selected_rubric_labels == ("Correct",)
    assert "AI drafted grades for 1 student" in status
    assert "1 skipped (graded meanwhile)" in status


def test_a_second_ai_request_while_one_runs_is_refused(
    course, local_ai, monkeypatch  # noqa: ANN001
) -> None:
    qa_path, submissions = course
    gate = threading.Event()
    model = FakeModel(_grade_reply, gate=gate)
    _use_model(monkeypatch, model)

    async def run() -> str:
        app = _app(qa_path, submissions)
        async with app.run_test(size=_SIZE) as pilot:
            await _open_grade_screen(pilot, app)
            await pilot.press("Y")
            await _settle(pilot, lambda: bool(model.prompts))
            await pilot.press("Y")
            await pilot.pause()
            refused = _grade_status(app)
            gate.set()
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: "AI drafted grades" in _grade_status(app))
            return refused

    refused = asyncio.run(run())
    assert "still working on draft grades for" in refused
    # Only the first run's two distinct answers were ever sent.
    assert len(model.prompts) == 2


def test_draft_grades_need_a_rubric(course, local_ai, monkeypatch) -> None:  # noqa: ANN001
    qa_path, submissions = course
    model = FakeModel(_grade_reply)
    _use_model(monkeypatch, model)

    async def run() -> str:
        app = _app(qa_path, submissions)
        async with app.run_test(size=_SIZE) as pilot:
            await _open_grade_screen(pilot, app)
            # A QA file always yields at least Correct/Incorrect, so strip the
            # loaded rubric to reach the guard a rubric-less question hits.
            app.workflow_state.grading_run.current_question.rubric_options = []
            await pilot.press("Y")
            await _settle(pilot)
            return _grade_status(app)

    status = asyncio.run(run())
    assert "has no rubric yet" in status
    assert model.prompts == []


def test_a_model_failure_while_grading_saves_nothing_and_says_so(
    course, local_ai, monkeypatch  # noqa: ANN001
) -> None:
    qa_path, submissions = course
    monkeypatch.setattr(
        app_config,
        "ensure_ready",
        lambda resolved=None, *, purpose="vision": (_ for _ in ()).throw(
            app_config.AIUnavailableError("Ollama is not running.")
        ),
    )
    model = FakeModel(_grade_reply)
    _use_model(monkeypatch, model)

    async def run() -> tuple[str, bool]:
        app = _app(qa_path, submissions)
        async with app.run_test(size=_SIZE) as pilot:
            await _open_grade_screen(pilot, app)
            await pilot.press("Y")
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: "No grades were saved" in _grade_status(app))
            status = _grade_status(app)
            await pilot.press("1")
            await pilot.pause()
            return status, (
                app.workflow_state.grading_run.current_question.points_awarded == 10
            )

    status, still_works = asyncio.run(run())
    assert "Ollama is not running." in status
    assert model.prompts == []
    assert still_works is True
    after = _fresh_seeds(qa_path, submissions)
    assert after["s2"].points_awarded is None


def test_per_answer_llm_errors_count_as_failed(course, local_ai, monkeypatch) -> None:  # noqa: ANN001
    qa_path, submissions = course

    def flaky(prompt: str) -> str:
        if _FIFO in prompt.split("Student's response:", 1)[1]:
            raise LLMError("timeout")
        return _grade_reply(prompt)

    model = FakeModel(flaky)
    _use_model(monkeypatch, model)

    async def run() -> str:
        app = _app(qa_path, submissions)
        async with app.run_test(size=_SIZE) as pilot:
            await _open_grade_screen(pilot, app)
            await pilot.press("Y")
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: "AI drafted grades" in _grade_status(app))
            return _grade_status(app)

    status = asyncio.run(run())
    assert "AI drafted grades for 2 students" in status
    assert "1 failed" in status


# ---------------------------------------------------------------------------
# Cloud providers: confirm the scale first; never auto-consent
# ---------------------------------------------------------------------------


@pytest.fixture
def cloud_ai(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    app_config.reload_manifests()
    text = ai_manifests.render_manifest(
        name="draft-cloud-test",
        label="Draft Cloud Test",
        dialect="openai-chat",
        host="https://api.draft-cloud-test.example/v1",
        vision_model="test-vision-1",
        text_model="test-text-1",
    )
    ai_manifests.install_manifest(text)
    app_config.reload_manifests()
    settings = replace(app_config.load_settings(), provider="draft-cloud-test")
    app_config.save_settings(settings)
    resolved = app_config.resolve()
    assert resolved.kind == "cloud"
    monkeypatch.setattr(
        app_config,
        "ensure_ready",
        lambda resolved=None, *, purpose="vision": None,
    )
    try:
        yield resolved
    finally:
        for path in app_config.manifests_dir().glob("*.toml"):
            path.unlink()
        app_config.reload_manifests()


def _grant_consent(resolved) -> None:  # noqa: ANN001
    settings = app_config.consent.grant_cloud_consent(
        app_config.load_settings(), resolved
    )
    app_config.save_settings(settings)
    assert app_config.resolve().cloud_consent_given


def _has(app: TuiApp, selector: str) -> bool:
    try:
        app.screen.query_one(selector)
        return True
    except Exception:  # noqa: BLE001
        return False


def test_cloud_draft_grades_cancel_on_preflight_sends_nothing(
    course, cloud_ai, monkeypatch  # noqa: ANN001
) -> None:
    qa_path, submissions = course
    _grant_consent(cloud_ai)
    model = FakeModel(_grade_reply)
    _use_model(monkeypatch, model)

    async def run() -> tuple[str, str]:
        app = _app(qa_path, submissions)
        async with app.run_test(size=_SIZE) as pilot:
            await _open_grade_screen(pilot, app)
            await pilot.press("Y")
            await _settle(pilot, lambda: _has(app, "#cloud-preflight-dialog"))
            statement = str(
                app.screen.query_one("#cloud-preflight-statement", Static).content
            )
            await pilot.click("#consent-cancel")
            await _settle(pilot, lambda: "Not sent" in _grade_status(app))
            await app.workers.wait_for_complete()
            return statement, _grade_status(app)

    statement, status = asyncio.run(run())
    # The real count: 2 distinct answers for 3 ungraded students.
    assert "2 graded-text calls from 3 students" in statement
    assert "api.draft-cloud-test.example" in statement
    assert "Nothing left this computer" in status
    assert model.prompts == []
    assert model.formats == []
    after = _fresh_seeds(qa_path, submissions)
    assert all(after[sid].points_awarded is None for sid in ("s1", "s2", "s3"))


def test_cloud_rubric_draft_cancel_on_preflight_sends_nothing(
    course, cloud_ai, monkeypatch  # noqa: ANN001
) -> None:
    qa_path, submissions = course
    _grant_consent(cloud_ai)
    model = FakeModel(_DRAFT_JSON)
    _use_model(monkeypatch, model)

    async def run() -> tuple[str, str, list]:
        app = _app(qa_path, submissions)
        async with app.run_test(size=_SIZE) as pilot:
            await _open_grade_screen(pilot, app)
            await pilot.press("e")
            await pilot.pause()
            await _press(app, "#rubric-edit-draft")
            await _settle(pilot, lambda: _has(app, "#cloud-preflight-dialog"))
            statement = str(
                app.screen.query_one("#cloud-preflight-statement", Static).content
            )
            await pilot.click("#consent-cancel")
            await _settle(pilot, lambda: "Not sent" in _editor_status(app))
            return (
                statement,
                _editor_status(app),
                _editor_rows(app, "#rubric-editor-subitems"),
            )

    statement, status, rows = asyncio.run(run())
    assert "1 text call carrying up to 3 sample answers" in statement
    assert "1 graded-text call from 4 students" in statement
    assert "Not sent" in status
    assert model.prompts == []
    assert [label for label, _, _ in rows] == ["Partially Correct"]


def test_cloud_draft_grades_confirmed_are_sent_and_tagged_with_the_provider(
    course, cloud_ai, monkeypatch  # noqa: ANN001
) -> None:
    qa_path, submissions = course
    _grant_consent(cloud_ai)
    model = FakeModel(_grade_reply)
    _use_model(monkeypatch, model)

    async def run() -> str:
        app = _app(qa_path, submissions)
        async with app.run_test(size=_SIZE) as pilot:
            await _open_grade_screen(pilot, app)
            await pilot.press("Y")
            await _settle(pilot, lambda: _has(app, "#cloud-preflight-dialog"))
            await pilot.click("#consent-accept")
            await _settle(pilot, lambda: bool(app.workers._workers) or bool(model.prompts))
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: "AI drafted grades" in _grade_status(app))
            return _grade_status(app)

    status = asyncio.run(run())
    assert len(model.prompts) == 2
    assert "AI drafted grades for 2 students" in status
    tag = parse_ai_analysis(_fresh_seeds(qa_path, submissions)["s1"].analysis)
    assert tag is not None
    assert tag.provider == "draft-cloud-test"
    assert tag.model == "test-text-1"


@pytest.mark.parametrize("feature", ["grades", "rubric"])
def test_cloud_without_consent_points_to_ai_setup_and_sends_nothing(
    course, cloud_ai, monkeypatch, feature  # noqa: ANN001
) -> None:
    qa_path, submissions = course
    model = FakeModel(_grade_reply if feature == "grades" else _DRAFT_JSON)
    _use_model(monkeypatch, model)

    async def run() -> tuple[str, bool]:
        app = _app(qa_path, submissions)
        async with app.run_test(size=_SIZE) as pilot:
            await _open_grade_screen(pilot, app)
            if feature == "grades":
                await pilot.press("Y")
                await _settle(pilot, lambda: "Nothing was sent" in _grade_status(app))
                status = _grade_status(app)
            else:
                await pilot.press("e")
                await pilot.pause()
                await _press(app, "#rubric-edit-draft")
                await _settle(pilot, lambda: "Nothing was sent" in _editor_status(app))
                status = _editor_status(app)
            shown = _has(app, "#cloud-preflight-dialog") or _has(
                app, "#cloud-consent-dialog"
            )
            return status, shown

    status, dialog_shown = asyncio.run(run())
    assert "have not agreed to send student work" in status
    assert "AI setup" in status
    assert dialog_shown is False
    assert model.prompts == []
    assert model.formats == []
    # And no consent was recorded on the instructor's behalf.
    assert not app_config.load_settings().consents
