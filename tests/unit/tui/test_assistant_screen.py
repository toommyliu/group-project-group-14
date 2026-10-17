from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Button, Input, RichLog, Select

from opengrader.core.agent import prepare_agent_packet, record_grade, load_tasks
from opengrader.core.submissions.student_file import load_student_submission_markdown
from opengrader.tui.ui.screens import assistant_screen
from opengrader.tui.ui.screens.assistant_screen import STARTER_PROMPT, AssistantScreen
from opengrader.tui.ui.screens.handoff_screen import MergeScreen

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
source_pdf: "../../split/Ada_Lovelace.pdf"
student_score: ""
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


def _prepare(cls: Path):  # noqa: ANN202
    # These tests are about the dialog; the crop check has its own tests.
    return prepare_agent_packet(cls / "submissions", cls / "qa.md", check_crops=False)


def _png(path: Path, text: str) -> None:
    """A real answer image: the packet reads every crop with Tesseract."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (600, 120), "white")
    ImageDraw.Draw(image).text((20, 40), text, fill="black")
    image.save(path)


@pytest.fixture()
def cls(tmp_path: Path) -> Path:
    root = tmp_path / "class"
    student = root / "submissions" / "sub-0001"
    (student / "responses").mkdir(parents=True)
    (student / "sub-0001.submission.md").write_text(SUBMISSION, encoding="utf-8")
    _png(student / "responses" / "q1.png", "a loop repeats")
    (root / "qa.md").write_text(QA, encoding="utf-8")
    return root


class _Host(App):
    def __init__(self, screen) -> None:  # noqa: ANN001
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


async def _settle(pilot, predicate, limit: int = 200) -> None:  # noqa: ANN001
    for _ in range(limit):
        await pilot.pause()
        await asyncio.sleep(0.01)
        if predicate():
            return


def _log(screen: AssistantScreen) -> str:
    return "\n".join(line.text for line in screen.query_one("#assistant-log", RichLog).lines)


def test_making_a_copy_says_where_to_open_the_assistant_and_what_to_say(
    cls: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied: list[str] = []
    monkeypatch.setattr(assistant_screen.clipboard, "copy_text", lambda _app, text: copied.append(text) or True)

    async def run() -> tuple[str, str, bool]:
        screen = AssistantScreen(submissions_path=str(cls / "submissions"), qa_file_path=str(cls / "qa.md"))
        app = _Host(screen)
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            merge_disabled_before = screen.query_one("#assistant-merge", Button).disabled
            await pilot.click("#assistant-prepare")
            await _settle(pilot, lambda: "Open your AI assistant" in _log(screen))
            await pilot.click("#assistant-copy-path")
            await pilot.click("#assistant-copy-prompt")
            return _log(screen), str(screen.query_one("#assistant-packet", Select).value), merge_disabled_before

    text, chosen, merge_disabled_before = asyncio.run(run())

    packet = Path(chosen)
    assert merge_disabled_before is True
    assert (packet / "AGENTS.md").is_file()
    assert packet.parent == cls.parent.resolve() / "class-ai-assistant"
    unwrapped = text.replace("\n", "")  # the log wraps long lines
    assert str(packet) in unwrapped and "claude" in text and STARTER_PROMPT in unwrapped
    assert "1 answer(s) still to grade" in text
    assert copied == [str(packet), STARTER_PROMPT]


def test_an_earlier_copy_is_found_and_merges_back_through_the_merge_dialog(cls: Path) -> None:
    packet = _prepare(cls).path
    [task] = load_tasks(packet)
    record_grade(packet, task.id, ["Correct"], confidence=0.9, model="claude")

    async def run() -> tuple[str, str]:
        screen = AssistantScreen(submissions_path=str(cls / "submissions"), qa_file_path=str(cls / "qa.md"))
        app = _Host(screen)
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await _settle(pilot, lambda: "suggested by your assistant" in _log(screen))
            text = _log(screen)
            await pilot.click("#assistant-merge")
            await _settle(pilot, lambda: isinstance(app.screen, MergeScreen))
            incoming = app.screen.query_one("#merge-incoming", Input).value
            return text, incoming

    text, incoming = asyncio.run(run())

    assert "1 suggested by your assistant" in text
    assert Path(incoming) == packet
    # Nothing is written until the teacher presses Merge in that dialog.
    master = load_student_submission_markdown(cls / "submissions" / "sub-0001" / "sub-0001.submission.md")
    assert master.questions[0].points_awarded is None


def test_the_teacher_accepts_a_suggested_fix_with_one_press(cls: Path) -> None:
    from opengrader.core.agent import list_suggestions, suggest
    from opengrader.tui.ui.screens.assistant_screen import SuggestionsScreen

    packet = _prepare(cls).path
    made = suggest(packet, question="q1", kind="answer", message="Also right", correct=["repeats code", "runs it again"])
    question = suggest(packet, question="q1", kind="question", message="Ambiguous")

    async def run() -> tuple[bool, str]:
        screen = SuggestionsScreen(packet=packet, qa_file_path=str(cls / "qa.md"), submissions_path=str(cls / "submissions"))
        app = _Host(screen)
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            no_fix_disabled = screen.query_one(f"#accept-{question.id}", Button).disabled
            await pilot.click(f"#accept-{made.id}")
            await _settle(pilot, lambda: not screen.query(f"#accept-{made.id}"))
            return no_fix_disabled, "\n".join(line.text for line in screen.query_one(RichLog).lines)

    no_fix_disabled, log = asyncio.run(run())

    assert no_fix_disabled is True
    assert "now accepts" in log
    assert "runs it again" in (cls / "qa.md").read_text()
    assert {s.id: s.decision for s in list_suggestions(packet)}[made.id] == "accepted"


def test_deleting_a_copy_takes_two_presses(cls: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    copied: list[str] = []
    monkeypatch.setattr(assistant_screen.clipboard, "copy_text", lambda _app, text: copied.append(text) or True)
    packet = _prepare(cls).path

    async def run() -> tuple[bool, bool]:
        screen = AssistantScreen(submissions_path=str(cls / "submissions"), qa_file_path=str(cls / "qa.md"))
        app = _Host(screen)
        async with app.run_test(size=_SIZE) as pilot:
            await _settle(pilot, lambda: not screen.query_one("#assistant-delete", Button).disabled)
            await pilot.click("#assistant-copy-mcp")
            await pilot.click("#assistant-delete")
            await pilot.pause()
            after_one = packet.exists()
            await _settle(pilot, lambda: not screen.query_one("#assistant-delete", Button).has_class("-active"))
            await pilot.click("#assistant-delete")
            await _settle(pilot, lambda: not packet.exists())
            return after_one, packet.exists()

    after_one, after_two = asyncio.run(run())

    assert after_one is True and after_two is False
    assert copied and "agent mcp --packet" in copied[0]


def test_the_answer_key_dialog_makes_its_folder_and_replaces_only_an_unchanged_key(tmp_path: Path) -> None:
    from opengrader.tui.ui.screens.assistant_screen import AnswerKeyAssistantScreen, key_folders

    cls = tmp_path / "class"
    cls.mkdir()
    (cls / "qa.md").write_text(QA)
    filled = QA.replace("    - repeats code\n", "    - repeats code\n    - runs it again\n")

    async def run(screen) -> tuple[str, str]:  # noqa: ANN001
        app = _Host(screen)
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            if screen._workspace is None:
                await pilot.click("#key-prepare")
                await _settle(pilot, lambda: screen._workspace is not None)
            (screen._workspace / "qa.md").write_text(filled)
            await pilot.click("#key-check")
            await pilot.pause()
            checked = "\n".join(line.text for line in screen.query_one(RichLog).lines)
            await pilot.click("#key-use")
            await pilot.pause()
            return checked, str(screen.query_one("#key-use", Button).label)

    checked, label = asyncio.run(run(AnswerKeyAssistantScreen(class_folder=str(cls), qa_file=str(cls / "qa.md"))))

    [folder] = key_folders(cls)
    assert folder.parent == tmp_path and folder.name == "class-ai-answer-key"
    assert (folder / "qa.md").is_file() and "fill" in (folder / "AGENTS.md").read_text().lower()
    assert "It loads." in checked
    # The key had not changed since the folder was made: replaced without asking.
    assert label == "Use this key" and (cls / "qa.md").read_text() == filled

    # A key the teacher changed meanwhile needs a second, deliberate press.
    edited = QA.replace("Explain what", "Describe what")
    (cls / "qa.md").write_text(edited)
    _checked, label = asyncio.run(run(AnswerKeyAssistantScreen(class_folder=str(cls), qa_file=str(cls / "qa.md"))))
    assert label == "Replace it?" and (cls / "qa.md").read_text() == edited


def test_a_rubric_dialog_has_no_merge_and_tells_the_assistant_to_draft(cls: Path) -> None:
    async def run() -> tuple[bool, str]:
        screen = AssistantScreen(
            submissions_path=str(cls / "submissions"), qa_file_path=str(cls / "qa.md"), purpose="rubrics"
        )
        app = _Host(screen)
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await pilot.click("#assistant-prepare")
            await _settle(pilot, lambda: "rubrics to draft" in _log(screen))
            return bool(screen.query_one("#assistant-merge", Button).display), _log(screen)

    merge_shown, text = asyncio.run(run())

    assert merge_shown is False
    assert "rubrics to draft for q1" in text and "draft partial-credit" in text.replace("\n", " ")
