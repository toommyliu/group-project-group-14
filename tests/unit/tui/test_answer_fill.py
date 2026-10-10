"""Adding the answers to an answer key from a form, and the draft offer on
Run Pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from textual.widgets import Button, Checkbox, Input, TextArea

import opengrader.tui.ui.screens.answer_fill_screen as answer_fill_screen
import opengrader.tui.ui.screens.setup_screen as setup_screen
from opengrader.core.qa_file import parse_qa_markdown
from opengrader.core.qa_file.draft_key import draft_from_text
from opengrader.tui import external_viewer

from .test_launch_prepare import (
    _add_submission,
    _blank_template,
    _buttons,
    _class_folder,
    _dialog,
    _drive,
    _fake_pipeline,
    _Session,
    _settle,
    _text,
)


@pytest.fixture
def pipeline_calls(monkeypatch) -> list:  # noqa: ANN001
    calls: list = []
    monkeypatch.setattr(setup_screen, "run_pipeline", _fake_pipeline(calls))
    return calls


def _form(app):  # noqa: ANN001, ANN202
    found = app.screen.query("#answers-dialog")
    return found.first() if found else None


async def _add_answers(session: _Session, folder: Path) -> None:
    """Choose the folder on Start grading, as Enter or Browse does, and press Add answers."""
    session.app.query_one("#tui-class-folder", Input).value = str(folder)
    session.screen._scan_launch_folder()
    await session.pilot.pause()
    button = session.app.query_one("#answers-button", Button)
    assert not button.has_class("hidden")
    button.press()
    await _form_open(session)


async def _form_open(session: _Session) -> None:
    await _settle(session.pilot, lambda: _form(session.app) is not None)


async def _form_closed(session: _Session) -> None:
    await _settle(session.pilot, lambda: _form(session.app) is None)


def _keyed_folder(tmp_path: Path) -> Path:
    """A class folder ready for grading, its answer key still without answers."""
    folder = _class_folder(tmp_path, key=False)
    draft = draft_from_text(
        "1. (5 points) Symbol for gold?\n2. Pick one. (4 points)\na) Sodium\nb) Chlorine\n", title="quiz"
    )
    (folder / "qa.md").write_text(draft.markdown, encoding="utf-8")
    _add_submission(folder)
    return folder


def test_the_drafted_key_is_followed_by_a_form_for_its_answers(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path, key=False)
    _blank_template(folder)

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        await session.choose("prepare")
        await _form_open(session)
        title = str(_form(session.app).border_title)
        boxes = list(session.app.screen.query(TextArea))
        boxes[0].text = "Au\ngold"
        session.app.screen.query_one("#answers-save", Button).press()
        await _form_closed(session)
        await session.finished()
        return title, len(boxes), session.notices

    title, boxes, notices = _drive(steps)

    assert "qa.md" in title and boxes == 2
    first, second = parse_qa_markdown((folder / "qa.md").read_text()).questions
    assert first.answers == ["Au", "gold"] and second.answers == []
    assert any(notice.startswith("Answers saved.") for notice in notices)


def test_add_answers_on_start_grading_opens_the_form_for_any_answer_key(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = _keyed_folder(tmp_path)

    async def steps(session: _Session):  # noqa: ANN202
        await _add_answers(session, folder)
        label = str(session.app.query_one("#answers-button", Button).label)
        session.app.screen.query_one(TextArea).text = "Au"
        session.app.screen.query_one("#answers-option-1-B", Checkbox).value = True
        await session.pilot.pause()
        session.app.screen.query_one("#answers-save", Button).press()
        await _form_closed(session)
        await session.pilot.pause()
        return label, session.app.query_one("#answers-button", Button).has_class("hidden"), session.notices

    label, hidden_after, notices = _drive(steps)

    assert label == "Add answers · 2 left"
    # Nothing left to add: the button goes away.
    assert hidden_after

    first, second = parse_qa_markdown((folder / "qa.md").read_text()).questions
    assert first.answers == ["Au"]
    assert second.correct_choice_label == "B" and second.prompt == "Pick one."
    # Already read: the answers are used once the grading is run again.
    assert any("Press Run Pipeline" in notice and "grades it again" in notice for notice in notices)
    assert pipeline_calls == []


def test_not_now_writes_nothing(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = _keyed_folder(tmp_path)
    before = (folder / "qa.md").read_bytes()

    async def steps(session: _Session):  # noqa: ANN202
        await _add_answers(session, folder)
        session.app.screen.query_one(TextArea).text = "Au"
        session.app.screen.query_one("#answers-cancel", Button).press()
        await _form_closed(session)

    _drive(steps)

    assert (folder / "qa.md").read_bytes() == before


def test_open_as_text_file_hands_the_key_to_the_text_editor_and_closes_the_form(
    tmp_path, monkeypatch, pipeline_calls  # noqa: ANN001
) -> None:
    folder = _keyed_folder(tmp_path)
    opened: list[Path] = []
    monkeypatch.setattr(answer_fill_screen, "open_in_text_editor", lambda path: opened.append(Path(path)))

    async def steps(session: _Session):  # noqa: ANN202
        await _add_answers(session, folder)
        session.app.screen.query_one("#answers-open-text", Button).press()
        await _form_closed(session)

    _drive(steps)

    assert opened == [folder / "qa.md"]


def test_a_key_changed_by_something_else_meanwhile_is_not_overwritten(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    import os

    folder = _keyed_folder(tmp_path)
    key = folder / "qa.md"

    async def steps(session: _Session):  # noqa: ANN202
        await _add_answers(session, folder)
        key.write_text(key.read_text() + "\n", encoding="utf-8")
        os.utime(key, (1_000_000_000, 1_000_000_000))
        session.app.screen.query_one(TextArea).text = "Au"
        session.app.screen.query_one("#answers-save", Button).press()
        await session.pilot.pause()
        return _form(session.app) is not None, _text(session.app, "#answers-problem")

    still_open, problem = _drive(steps)

    assert still_open and "nothing was saved" in problem
    assert parse_qa_markdown(key.read_text()).questions[0].answers == []


def test_add_answers_is_only_shown_when_there_are_answers_to_add(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    no_key = _class_folder(tmp_path, "no-key", key=False)
    complete = _class_folder(tmp_path, "complete")  # its one question has an answer
    _add_submission(complete)
    (tmp_path / "third").mkdir()
    needs_answers = _keyed_folder(tmp_path / "third")

    async def steps(session: _Session):  # noqa: ANN202
        button = session.app.query_one("#answers-button", Button)
        seen = [button.has_class("hidden")]  # before any folder is chosen
        for folder in (no_key, complete, needs_answers):
            session.app.query_one("#tui-class-folder", Input).value = str(folder)
            session.screen._scan_launch_folder()
            await session.pilot.pause()
            seen.append(button.has_class("hidden"))
        return seen

    assert _drive(steps) == [True, True, True, False]


def test_several_correct_options_are_ticked_like_any_other(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = _keyed_folder(tmp_path)

    async def steps(session: _Session):  # noqa: ANN202
        await _add_answers(session, folder)
        for letter in ("A", "B"):
            session.app.screen.query_one(f"#answers-option-1-{letter}", Checkbox).value = True
        session.app.screen.query_one("#answers-save", Button).press()
        await _form_closed(session)
        # Opened again, it shows both ticked and one can be taken back.
        await _add_answers(session, folder)
        ticked = [
            session.app.screen.query_one(f"#answers-option-1-{letter}", Checkbox).value
            for letter in ("A", "B")
        ]
        session.app.screen.query_one("#answers-option-1-A", Checkbox).value = False
        session.app.screen.query_one("#answers-save", Button).press()
        await _form_closed(session)
        return ticked

    key = folder / "qa.md"
    assert _drive(steps) == [True, True]
    question = parse_qa_markdown(key.read_text()).questions[1]
    assert question.correct_choice_label == "B" and question.correct_choice_labels == ()


# --- the same draft offer from Run Pipeline ----------------------------------


def _card_for(session: _Session, folder: Path) -> None:
    session.app.query_one("#pipe-out", Input).value = str(folder)
    session.screen._load_class_folder()


def test_run_pipeline_offers_the_draft_too_and_drafts_on_a_yes(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path, key=False)
    _blank_template(folder)

    async def steps(session: _Session):  # noqa: ANN202
        _card_for(session, folder)
        await session.pilot.pause()
        session.app.query_one("#pipe-run", Button).press()
        await session.asked()
        buttons, runs = _buttons(session.app), len(pipeline_calls)
        await session.choose("prepare")
        await _form_open(session)
        session.app.screen.query_one("#answers-cancel", Button).press()
        await _form_closed(session)
        await session.finished()
        return buttons, runs

    buttons, runs_before_yes = _drive(steps)

    assert buttons == ["cancel", "skip", "prepare"] and runs_before_yes == 0
    (request,) = pipeline_calls
    assert request.qa_file == folder / "qa.md"
    assert len(parse_qa_markdown((folder / "qa.md").read_text()).questions) == 2


def test_run_without_a_key_runs_as_before_and_writes_no_key(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path, key=False)
    _blank_template(folder)

    async def steps(session: _Session):  # noqa: ANN202
        _card_for(session, folder)
        await session.pilot.pause()
        session.app.query_one("#pipe-run", Button).press()
        await session.asked()
        await session.choose("skip")
        await session.finished()
        return _form(session.app) is None, _dialog(session.app) is None

    assert _drive(steps) == (True, True)
    (request,) = pipeline_calls
    assert request.qa_file is None and request.blank_template == folder / "blank-template.pdf"
    assert not (folder / "qa.md").exists()


def test_run_pipeline_asks_nothing_when_there_is_a_key_or_no_template(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    with_key = _class_folder(tmp_path, "with-key")
    _blank_template(with_key)
    no_template = _class_folder(tmp_path, "no-template", key=False)

    async def steps(session: _Session):  # noqa: ANN202
        for folder in (with_key, no_template):
            _card_for(session, folder)
            await session.pilot.pause()
            session.screen._run_pipeline()
            await session.finished()
            assert _dialog(session.app) is None, folder

    _drive(steps)

    assert [request.output_dir for request in pipeline_calls] == [with_key, no_template]


# --- the plain-text editor ---------------------------------------------------


@pytest.mark.parametrize(
    "platform, expected",
    [
        ("darwin", ["open", "-t", "/class/qa.md"]),
        ("win32", ["notepad", "/class/qa.md"]),
        ("linux", ["xdg-open", "/class/qa.md"]),
    ],
)
def test_an_answer_key_opens_in_the_plain_text_editor_on_every_system(monkeypatch, platform, expected) -> None:  # noqa: ANN001
    monkeypatch.setattr(sys, "platform", platform)

    assert external_viewer.text_editor_command("/class/qa.md") == expected


def test_opening_a_file_that_is_not_there_is_an_error_not_a_blank_editor(tmp_path) -> None:  # noqa: ANN001
    with pytest.raises(external_viewer.ExternalViewerError):
        external_viewer.open_in_text_editor(tmp_path / "qa.md")


# --- Suggest answers with AI -------------------------------------------------


def _ai(monkeypatch, *, ready: bool = True, reply=None, error: str = "", server=None) -> list[str]:  # noqa: ANN001
    """An AI that is (or is not) ready; returns the prompts it was sent."""
    import json

    prompts: list[str] = []

    def generate_for(_resolved, *, emit=None, started=None):  # noqa: ANN001, ANN202
        if started is not None and server is not None:
            started.append(server)
        def generate(prompt: str) -> str:
            prompts.append(prompt)
            if error:
                raise RuntimeError(error)
            return json.dumps({"answers": [{"question": q, "answers": a} for q, a in (reply or [])]})

        return generate

    monkeypatch.setattr(answer_fill_screen, "ai_suggest_ready", lambda: ready)
    monkeypatch.setattr(answer_fill_screen, "ai_generate", generate_for)
    return prompts


async def _suggest(session: _Session) -> None:
    button = session.app.screen.query_one("#answers-suggest", Button)
    await _settle(session.pilot, lambda: button.display)
    button.press()
    await session.app.workers.wait_for_complete()
    await _settle(session.pilot, lambda: not button.disabled)


def test_the_ai_button_is_only_there_when_an_ai_can_be_asked(tmp_path, monkeypatch, pipeline_calls) -> None:  # noqa: ANN001
    folder = _keyed_folder(tmp_path)
    _ai(monkeypatch, ready=False)

    async def steps(session: _Session):  # noqa: ANN202
        await _add_answers(session, folder)
        await session.app.workers.wait_for_complete()
        await session.pilot.pause()
        return session.app.screen.query_one("#answers-suggest", Button).display

    assert _drive(steps) is False


def test_ai_suggestions_fill_the_empty_questions_and_save_nothing(tmp_path, monkeypatch, pipeline_calls) -> None:  # noqa: ANN001
    folder = _keyed_folder(tmp_path)
    before = (folder / "qa.md").read_bytes()
    prompts = _ai(monkeypatch, reply=[("1", ["Au", "gold"]), ("2", ["B"])])

    async def steps(session: _Session):  # noqa: ANN202
        await _add_answers(session, folder)
        await _suggest(session)
        screen = session.app.screen
        seen = (
            screen.query_one("#answers-text-0", TextArea).text,
            [screen.query_one(f"#answers-option-1-{letter}", Checkbox).value for letter in "AB"],
            [screen.query_one(f"#answers-ai-note-{index}").display for index in (0, 1)],
            _text(session.app, "#answers-ai-status"),
            (folder / "qa.md").read_bytes() == before,
        )
        screen.query_one("#answers-save", Button).press()
        await _form_closed(session)
        return seen

    text, ticked, marked, status, untouched_before_save = _drive(steps)

    assert text == "Au\ngold" and ticked == [False, True]
    assert marked == [True, True]
    assert "suggested answers for 2 of 2" in status and "nothing is saved until" in status
    assert untouched_before_save
    # One request, holding the questions and nothing of any student's.
    (prompt,) = prompts
    assert "Symbol for gold?" in prompt and "submission" not in prompt.lower()
    first, second = parse_qa_markdown((folder / "qa.md").read_text()).questions
    assert first.answers == ["Au", "gold"] and second.correct_choice_label == "B"


def test_the_ai_is_only_asked_about_what_is_empty_and_never_overwrites(tmp_path, monkeypatch, pipeline_calls) -> None:  # noqa: ANN001
    folder = _keyed_folder(tmp_path)
    prompts = _ai(monkeypatch, reply=[("1", ["Ag"]), ("2", ["A"])])

    async def steps(session: _Session):  # noqa: ANN202
        await _add_answers(session, folder)
        session.app.screen.query_one("#answers-text-0", TextArea).text = "Au"
        await _suggest(session)
        screen = session.app.screen
        return (
            screen.query_one("#answers-text-0", TextArea).text,
            screen.query_one("#answers-ai-note-0").display,
            screen.query_one("#answers-option-1-A", Checkbox).value,
        )

    assert _drive(steps) == ("Au", False, True)
    (prompt,) = prompts
    assert "Symbol for gold?" not in prompt and "Pick one." in prompt


def test_an_ai_that_fails_or_has_nothing_to_say_changes_nothing(tmp_path, monkeypatch, pipeline_calls) -> None:  # noqa: ANN001
    folder = _keyed_folder(tmp_path)

    async def steps(session: _Session):  # noqa: ANN202
        await _add_answers(session, folder)
        await _suggest(session)
        screen = session.app.screen
        return _text(session.app, "#answers-ai-status"), screen.query_one("#answers-text-0", TextArea).text

    _ai(monkeypatch, error="the model is not answering.")
    status, text = _drive(steps)
    assert "could not be asked" in status and "the model is not answering." in status and text == ""

    _ai(monkeypatch, reply=[("1", []), ("2", [])])
    status, text = _drive(steps)
    assert "had no answer to suggest" in status and text == ""


def test_a_cloud_ai_is_asked_first_and_no_means_nothing_is_sent(tmp_path, monkeypatch, pipeline_calls) -> None:  # noqa: ANN001
    from types import SimpleNamespace

    folder = _keyed_folder(tmp_path)
    prompts = _ai(monkeypatch, reply=[("1", ["Au"])])
    cloud = SimpleNamespace(
        kind="cloud", host="https://api.example.test", provider=SimpleNamespace(label="Test Cloud", name="test")
    )

    async def steps(session: _Session):  # noqa: ANN202
        await _add_answers(session, folder)
        button = session.app.screen.query_one("#answers-suggest", Button)
        await _settle(session.pilot, lambda: button.display)
        # Only now, and only while the form is up: the rest of the app reads
        # the real settings.
        with monkeypatch.context() as patch:
            patch.setattr(answer_fill_screen.app_config, "resolve", lambda: cloud)
            patch.setattr(
                answer_fill_screen.app_config.consent, "egress_statement", lambda _r: "Sent to Test Cloud."
            )
            return await _ask_cloud(session, button)

    async def _ask_cloud(session: _Session, button):  # noqa: ANN001, ANN202
        button.press()
        await _settle(session.pilot, lambda: bool(session.app.screen.query("#consent-cancel")))
        statement = " ".join(str(w.render()) for w in session.app.screen.query("Static"))
        session.app.screen.query_one("#consent-cancel", Button).press()
        await _form_open(session)
        declined = (_text(session.app, "#answers-ai-status"), list(prompts))
        button.press()
        await _settle(session.pilot, lambda: bool(session.app.screen.query("#consent-accept")))
        session.app.screen.query_one("#consent-accept", Button).press()
        await _form_open(session)
        await session.app.workers.wait_for_complete()
        await _settle(session.pilot, lambda: not button.disabled)
        return statement, declined, session.app.screen.query_one("#answers-text-0", TextArea).text

    statement, (status, sent_after_no), text = _drive(steps)

    assert "2 question(s)" in statement and "no student" in statement
    assert "api.example.test" in statement
    assert "Not sent" in status and sent_after_no == []
    assert text == "Au" and len(prompts) == 1


# --- a local AI that is installed but not running ----------------------------


class _Local:
    """Stands in for `local_ai`: an Ollama that is, or is not, running."""

    LocalAIError = RuntimeError

    def __init__(self, *, listening=False, binary="/usr/local/bin/ollama", models=("grader:1",)) -> None:  # noqa: ANN001
        self.listening, self.binary, self.models = listening, binary, list(models)
        self.started = 0
        self.stopped = 0

    def server_listening(self, _host):  # noqa: ANN001, ANN201
        return self.listening

    def is_this_computer(self, _host):  # noqa: ANN001, ANN201
        return True

    def find_ollama(self):  # noqa: ANN201
        return self.binary

    def installed_models(self, _host):  # noqa: ANN001, ANN201
        return list(self.models)

    def missing_models(self, wanted, installed):  # noqa: ANN001, ANN201
        return tuple(model for model in wanted if model not in installed)

    def start_server(self, _host, *, emit):  # noqa: ANN001, ANN201
        self.started += 1
        self.listening = True
        return self

    def stop(self) -> None:
        self.stopped += 1


def _local_ai(monkeypatch, local: _Local, *, auto_start: bool):  # noqa: ANN001, ANN202
    from types import SimpleNamespace

    import opengrader.core.app_config as real_config

    resolved = SimpleNamespace(kind="local", host="http://127.0.0.1:11434", text_model="grader:1", auto_start=auto_start)
    monkeypatch.setattr(real_config, "local_ai", local, raising=False)
    monkeypatch.setattr(answer_fill_screen.app_config, "resolve", lambda: resolved)
    monkeypatch.setattr(answer_fill_screen.app_config, "ensure_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(answer_fill_screen.app_config, "text_generate", lambda *_a, **_k: "the model")
    return resolved


def test_the_ai_button_is_offered_for_a_local_ai_that_may_be_started(monkeypatch) -> None:  # noqa: ANN001
    stopped = _Local(listening=False)

    _local_ai(monkeypatch, stopped, auto_start=True)
    assert answer_fill_screen.ai_suggest_ready() is True
    # Checking is not starting.
    assert stopped.started == 0

    _local_ai(monkeypatch, stopped, auto_start=False)
    assert answer_fill_screen.ai_suggest_ready() is False
    _local_ai(monkeypatch, _Local(listening=False, binary=None), auto_start=True)
    assert answer_fill_screen.ai_suggest_ready() is False
    # Running, but without the grading model: nothing to ask.
    _local_ai(monkeypatch, _Local(listening=True, models=()), auto_start=True)
    assert answer_fill_screen.ai_suggest_ready() is False
    _local_ai(monkeypatch, _Local(listening=True), auto_start=False)
    assert answer_fill_screen.ai_suggest_ready() is True


def test_asking_starts_the_local_ai_and_hands_back_what_it_started(monkeypatch) -> None:  # noqa: ANN001
    local = _Local(listening=False)
    resolved = _local_ai(monkeypatch, local, auto_start=True)
    started: list = []
    said: list[str] = []

    generate = answer_fill_screen.ai_generate(resolved, emit=said.append, started=started)

    assert generate == "the model" and local.started == 1 and started == [local]
    assert said == ["Starting the local AI…"]
    # Already running: nothing is started, so nothing is handed back to stop.
    again: list = []
    answer_fill_screen.ai_generate(resolved, started=again)
    assert local.started == 1 and again == []


def test_a_missing_model_is_reported_and_never_downloaded(monkeypatch) -> None:  # noqa: ANN001
    local = _Local(listening=False, models=())
    resolved = _local_ai(monkeypatch, local, auto_start=True)

    with pytest.raises(RuntimeError, match="not downloaded yet"):
        answer_fill_screen.ai_generate(resolved, started=[])

    assert not hasattr(local, "pull_model")


def test_a_local_ai_that_may_not_be_started_is_left_alone(monkeypatch) -> None:  # noqa: ANN001
    local = _Local(listening=False)
    resolved = _local_ai(monkeypatch, local, auto_start=False)

    with pytest.raises(RuntimeError, match="not running"):
        answer_fill_screen.ai_generate(resolved)

    assert local.started == 0


def test_a_server_the_form_started_is_stopped_when_the_form_closes(tmp_path, monkeypatch, pipeline_calls) -> None:  # noqa: ANN001
    folder = _keyed_folder(tmp_path)
    server = _Local()
    _ai(monkeypatch, reply=[("1", ["Au"])], server=server)

    async def steps(session: _Session):  # noqa: ANN202
        await _add_answers(session, folder)
        await _suggest(session)
        running_while_open = server.stopped
        session.app.screen.query_one("#answers-cancel", Button).press()
        await _form_closed(session)
        await session.pilot.pause()
        return running_while_open

    assert _drive(steps) == 0
    assert server.stopped == 1
