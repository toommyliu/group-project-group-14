"""Launch Grading on a class folder that is not ready: it asks, prepares the
folder with the settings saved for it, shows the progress on its own card and
opens grading — and says what to do when there is no answer key."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Button, Checkbox, Collapsible, Input, Static

import opengrader.tui.ui.screens.setup_screen as setup_screen
from opengrader.core.pipeline import PipelineReport, StageOutcome, StageStatus
from opengrader.core.pipeline_settings import (
    FolderPipelineSettings,
    load_folder_settings,
    save_folder_settings,
)
from opengrader.core.submissions.manifest import (
    RUN_LEDGER_FILENAME,
    ExtractionRecord,
    append_record,
)
from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens.setup_screen import SetupScreen

from .fakes import DEFAULT_IMAGE_PATH

_QA_TEXT = """---
title: Quiz
---

- (10 points) What is 2 + 2?
  - **Correct**
    - 4
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
"""


def _class_folder(tmp_path: Path, name: str = "quiz1", *, key: bool = True) -> Path:
    folder = tmp_path / name
    folder.mkdir()
    (folder / "scans.pdf").write_bytes(b"%PDF-1.4\n")
    if key:
        (folder / "qa.md").write_text(_QA_TEXT, encoding="utf-8")
    return folder


def _add_submission(folder: Path) -> Path:
    student = folder / "submissions" / "submission-0001"
    student.mkdir(parents=True, exist_ok=True)
    (student / "submission-0001.submission.md").write_text("---\n---\n")
    return folder / "submissions"


def _record_stage(folder: Path, stage: str) -> None:
    append_record(
        folder,
        ExtractionRecord(key=stage, source="scans.pdf", source_sha256="x", settings_id="y"),
        filename=RUN_LEDGER_FILENAME,
    )


def _fake_pipeline(calls: list):
    """Stands in for the run: one submission appears, as extraction leaves it."""

    def run(request, *, progress=None):  # noqa: ANN001, ANN202
        calls.append(request)
        if progress is not None:
            progress.stage_start("extract")
        _add_submission(request.output_dir)
        return PipelineReport(
            stages=(StageOutcome(stage="extract", status=StageStatus.RAN, message="ok"),),
            submissions_dir=request.submissions_dir,
            qa_file=request.qa_file,
        )

    return run


async def _settle(pilot, predicate, limit: int = 200) -> None:  # noqa: ANN001
    for _ in range(limit):
        await pilot.pause()
        await asyncio.sleep(0.01)
        if predicate():
            return
    raise AssertionError("the app never got there")


def _dialog(app):  # noqa: ANN001, ANN202
    found = app.screen.query("#prepare-folder-dialog")
    return found.first() if found else None


def _buttons(app) -> list[str]:  # noqa: ANN001
    return [
        (button.id or "").removeprefix("prepare-folder-")
        for button in app.screen.query("#prepare-folder-buttons Button").results(Button)
    ]


def _text(app, selector: str) -> str:  # noqa: ANN001
    return str(app.screen.query_one(selector, Static).render())


class _Session:
    """One headless run of `opengrader classic`, with what it launched and said."""

    def __init__(self, app, pilot) -> None:  # noqa: ANN001
        self.app = app
        self.pilot = pilot
        self.screen = app.query_one(SetupScreen)
        self.launched: list[tuple[str, str]] = []
        self.notices: list[str] = []

        original = self.screen.post_message

        def spy(message):  # noqa: ANN001, ANN202
            if isinstance(message, SetupScreen.LaunchRequested):
                self.launched.append((message.qa_path, message.submissions_path))
                return True
            return original(message)

        self.screen.post_message = spy  # type: ignore[method-assign]
        app.notify = lambda message, **_kwargs: self.notices.append(str(message))  # type: ignore[method-assign]

    async def launch(self, folder: Path) -> None:
        self.app.query_one("#tui-class-folder", Input).value = str(folder)
        self.app.query_one("#launch-button", Button).press()
        await self.pilot.pause()

    async def asked(self) -> None:
        await _settle(self.pilot, lambda: _dialog(self.app) is not None)

    async def choose(self, action: str) -> None:
        self.app.screen.query_one(f"#prepare-folder-{action}", Button).press()
        await _settle(self.pilot, lambda: _dialog(self.app) is None)

    async def finished(self) -> None:
        await self.app.workers.wait_for_complete()
        await _settle(self.pilot, lambda: self.screen.extraction_in_progress is False)
        await self.pilot.pause()


def _drive(steps):  # noqa: ANN001, ANN202
    async def run():  # noqa: ANN202
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            session = _Session(app, pilot)
            return await steps(session)

    return asyncio.run(run())


@pytest.fixture
def pipeline_calls(monkeypatch) -> list:  # noqa: ANN001
    calls: list = []
    monkeypatch.setattr(setup_screen, "run_pipeline", _fake_pipeline(calls))
    return calls


def test_an_unprepared_folder_is_prepared_only_after_a_yes(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path)
    pipeline_calls: list = []
    during: dict[str, bool] = {}
    fake = _fake_pipeline(pipeline_calls)

    async def steps(session: _Session):  # noqa: ANN202
        def run(request, *, progress=None):  # noqa: ANN001, ANN202
            card = session.app.query_one("#launch-progress")
            during["shown"] = not card.has_class("hidden")
            return fake(request, progress=progress)

        monkeypatch.setattr(setup_screen, "run_pipeline", run)
        await session.launch(folder)
        await session.asked()
        before = (len(pipeline_calls), list(session.launched), _buttons(session.app))
        found = _text(session.app, "#prepare-folder-found")
        await session.choose("prepare")
        await session.finished()
        return before, found, during["shown"]

    (runs_before, launched_before, buttons), found, shown = _drive(steps)

    assert (runs_before, launched_before) == (0, [])
    assert buttons == ["cancel", "settings", "prepare"]
    assert "scans.pdf" in found and "qa.md" in found
    assert "nothing is saved for this folder yet" in found
    # The run's progress is on the card whose button was pressed.
    assert shown
    (request,) = pipeline_calls
    assert (request.scan, request.qa_file, request.output_dir) == (
        folder / "scans.pdf",
        folder / "qa.md",
        folder,
    )


def test_a_prepared_folder_opens_when_the_run_finishes(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path)

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        await session.choose("prepare")
        await session.finished()
        return session.launched, session.app.query_one("#launch-progress").has_class("hidden")

    launched, progress_hidden = _drive(steps)

    assert launched == [(str(folder / "qa.md"), str(folder / "submissions"))]
    assert progress_hidden


def test_cancel_runs_nothing_and_writes_nothing(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path)

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        await session.choose("cancel")
        return session.launched

    assert _drive(steps) == []
    assert pipeline_calls == []
    assert sorted(path.name for path in folder.iterdir()) == ["qa.md", "scans.pdf"]


def test_the_run_uses_the_settings_saved_with_the_folder(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path)
    save_folder_settings(
        folder, FolderPipelineSettings(pages_per_submission=6, reorder_pages=True)
    )

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        found = _text(session.app, "#prepare-folder-found")
        await session.choose("prepare")
        await session.finished()
        return found

    found = _drive(steps)

    (request,) = pipeline_calls
    assert (request.pages_per_submission, request.reorder_pages) == (6, True)
    assert "saved with this folder" in found
    assert "6" in found


def test_a_saved_region_map_survives_a_blank_template_in_the_folder(
    tmp_path, pipeline_calls  # noqa: ANN001
) -> None:
    folder = _class_folder(tmp_path)
    (folder / "blank-template.pdf").write_bytes(b"%PDF-1.4\n")
    region_map = folder / "quiz.regions.json"
    region_map.write_text("{}", encoding="utf-8")
    save_folder_settings(
        folder,
        FolderPipelineSettings(use_region_map=True, region_map=str(region_map)),
    )

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        await session.choose("prepare")
        await session.finished()

    _drive(steps)

    (request,) = pipeline_calls
    assert (request.region_map, request.blank_template) == (region_map, None)


def test_one_folders_settings_never_run_against_another(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    first = _class_folder(tmp_path, "quiz1")
    (first / "roster.tsv").write_text("1\tAda\n", encoding="utf-8")
    save_folder_settings(
        first,
        FolderPipelineSettings(
            pages_per_submission=6, reorder_pages=True, draft_rubrics=True
        ),
    )
    second = _class_folder(tmp_path, "quiz2")

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(first)
        await session.asked()
        await session.choose("cancel")
        session.app.query_one("#pipe-fresh", Checkbox).value = True
        await session.launch(second)
        await session.asked()
        await session.choose("prepare")
        await session.finished()

    _drive(steps)

    (request,) = pipeline_calls
    assert request.output_dir == second
    assert request.scan == second / "scans.pdf"
    assert (
        request.pages_per_submission,
        request.reorder_pages,
        request.draft_rubrics,
        request.roster,
        request.fresh,
    ) == (None, False, False, None, False)
    # Nothing of the first folder's was written into the second's settings.
    assert load_folder_settings(second) == FolderPipelineSettings()


def test_a_folder_typed_over_the_last_one_does_not_open_the_last_one(
    tmp_path, pipeline_calls  # noqa: ANN001
) -> None:
    ready = _class_folder(tmp_path, "ready")
    _add_submission(ready)
    fresh = _class_folder(tmp_path, "fresh")

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(ready)
        opened = list(session.launched)
        # What the app does once a folder is open for grading.
        session.screen.show_session(ready / "qa.md", ready / "submissions")
        await session.pilot.pause()
        await session.launch(fresh)
        await session.asked()
        return opened, session.launched, _text(session.app, "#prepare-folder-found")

    opened, launched, found = _drive(steps)

    assert opened == [(str(ready / "qa.md"), str(ready / "submissions"))]
    assert launched == opened
    assert str(fresh) in found


def test_a_folder_with_no_scan_is_sent_to_the_pipeline_card(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = tmp_path / "empty"
    folder.mkdir()
    (folder / "qa.md").write_text(_QA_TEXT, encoding="utf-8")

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.pilot.pause()
        return (
            _dialog(session.app) is None,
            session.app.query_one("#section-pipeline", Collapsible).collapsed,
            session.app.query_one("#pipe-out", Input).value,
            session.notices,
        )

    no_dialog, collapsed, pipe_folder, notices = _drive(steps)

    assert no_dialog and not collapsed
    assert pipe_folder == str(folder)
    assert any("no scanned PDF" in notice for notice in notices)
    assert pipeline_calls == []


def test_two_candidate_scans_are_chosen_by_hand_not_guessed(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path)
    # Names that say nothing; ones naming a section each are section scans.
    (folder / "afternoon.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "scans.pdf").rename(folder / "morning.pdf")

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.pilot.pause()
        return _dialog(session.app) is None, session.notices

    no_dialog, notices = _drive(steps)

    assert no_dialog
    assert any("More than one PDF" in notice for notice in notices)
    assert pipeline_calls == []


def test_change_settings_opens_the_pipeline_card_on_that_folder(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path)

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        await session.choose("settings")
        return (
            session.app.query_one("#section-pipeline", Collapsible).collapsed,
            session.app.query_one("#pipe-settings", Collapsible).collapsed,
            session.app.query_one("#pipe-out", Input).value,
        )

    assert _drive(steps) == (False, False, str(folder))
    assert pipeline_calls == []


def test_a_failed_run_stays_on_the_tools_page_and_says_why(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path)

    def blocked(request, *, progress=None):  # noqa: ANN001, ANN202
        return PipelineReport(
            stages=(
                StageOutcome(
                    stage="scan-check",
                    status=StageStatus.BLOCKED,
                    message="2 high-severity finding(s)",
                    detail="page 3: upside down",
                ),
            ),
            submissions_dir=request.submissions_dir,
            qa_file=request.qa_file,
        )

    monkeypatch.setattr(setup_screen, "run_pipeline", blocked)

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        await session.choose("prepare")
        await session.finished()
        return (
            session.launched,
            _text(session.app, "#launch-progress-status"),
            session.app.query_one("#launch-progress").has_class("hidden"),
            session.app.query_one("#launch-progress-bar").has_class("hidden"),
            session.app.query_one("#section-pipeline", Collapsible).collapsed,
        )

    launched, status, block_hidden, bar_hidden, card_collapsed = _drive(steps)

    assert launched == []
    assert status.startswith("Stopped: 2 high-severity")
    # The message stays; the bar does not keep moving beside it.
    assert (block_hidden, bar_hidden) == (False, True)
    assert card_collapsed is False


def test_the_progress_line_counts_only_the_steps_this_run_has(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path, key=False)
    seen: list[str] = []

    def run(request, *, progress=None):  # noqa: ANN001, ANN202
        return PipelineReport(stages=(), submissions_dir=request.submissions_dir, qa_file=None)

    monkeypatch.setattr(setup_screen, "run_pipeline", run)

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        await session.choose("prepare")
        await session.finished()
        for stage in ("scan-check", "split", "extract", "auto-grade"):
            session.screen._pipe_stage_start(stage)
            seen.append(_text(session.app, "#launch-progress-status"))
        session.screen._pipe_response("submission-0001", "q2")
        seen.append(_text(session.app, "#launch-progress-status"))

    _drive(steps)

    assert seen[:3] == [
        "Step 1 of 3 · Checking the scan…",
        "Step 2 of 3 · Splitting it into one PDF per student…",
        "Step 3 of 3 · Reading the answers…",
    ]
    # With no answer key there is no auto-grading step to announce.
    assert seen[3] == seen[2]
    assert seen[4] == "Step 3 of 3 · Reading the answers — submission-0001 · q2"


# --- no answer key -----------------------------------------------------------


def test_no_answer_key_offers_to_make_one_or_read_the_scans_meanwhile(
    tmp_path, pipeline_calls  # noqa: ANN001
) -> None:
    folder = _class_folder(tmp_path, key=False)

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        return (
            _buttons(session.app),
            _text(session.app, "#prepare-folder-found"),
            session.app.screen.focused.id,
        )

    buttons, found, focused = _drive(steps)

    assert buttons == ["cancel", "settings", "create-key", "prepare"]
    assert "none found" in found
    assert focused == "prepare-folder-prepare"
    assert pipeline_calls == []


def test_create_answer_key_is_filled_in_from_the_folder(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path, key=False)
    (folder / "quiz1-solutions.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "blank-template.pdf").write_bytes(b"%PDF-1.4\n")

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        await session.choose("create-key")
        await session.pilot.pause()
        app = session.app
        return (
            app.query_one("#section-answer-key", Collapsible).collapsed,
            app.query_one("#ak-input", Input).value,
            app.query_one("#ak-output", Input).value,
            app.query_one("#ak-use-template", Checkbox).value,
            app.query_one("#ak-template-path", Input).value,
        )

    assert _drive(steps) == (
        False,
        str(folder / "quiz1-solutions.pdf"),
        str(folder / "qa.md"),
        True,
        str(folder / "blank-template.pdf"),
    )
    assert pipeline_calls == []


def test_a_file_already_called_qa_md_is_never_the_default_output(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path, key=False)
    (folder / "qa.md").write_text("notes, not an answer key\n", encoding="utf-8")

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        await session.choose("create-key")
        return session.app.query_one("#ak-output", Input).value

    assert _drive(steps) == str(folder / "answer-key.md")


def test_a_new_answer_key_carries_straight_on_to_preparing(tmp_path, monkeypatch, pipeline_calls) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path, key=False)
    (folder / "solutions.txt").write_text("1. What is 2 + 2?\n", encoding="utf-8")

    def write_key(argv, **_hooks):  # noqa: ANN001, ANN202
        Path(argv[argv.index("--output") + 1]).write_text(_QA_TEXT, encoding="utf-8")

    monkeypatch.setattr(setup_screen, "run_answer_key_cli", write_key)

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        await session.choose("create-key")
        session.app.query_one("#run-answer-key", Button).press()
        await session.app.workers.wait_for_complete()
        # Asked again, now with a key: nothing ran in between.
        await session.asked()
        buttons, runs = _buttons(session.app), len(pipeline_calls)
        await session.choose("prepare")
        await session.finished()
        return buttons, runs, session.launched

    buttons, runs_before_yes, launched = _drive(steps)

    assert buttons == ["cancel", "settings", "prepare"]
    assert runs_before_yes == 0
    assert launched == [(str(folder / "qa.md"), str(folder / "submissions"))]


def test_an_answer_key_made_for_another_folder_does_not_resume(tmp_path, monkeypatch, pipeline_calls) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path, key=False)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    monkeypatch.setattr(setup_screen, "run_answer_key_cli", lambda argv, **_hooks: None)

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        await session.choose("create-key")
        app = session.app
        app.query_one("#ak-input", Input).value = str(tmp_path / "source.txt")
        app.query_one("#ak-output", Input).value = str(elsewhere / "qa.md")
        app.query_one("#run-answer-key", Button).press()
        await app.workers.wait_for_complete()
        await session.pilot.pause()
        await session.pilot.pause()
        return _dialog(app) is None

    assert _drive(steps)


def test_reading_the_scans_without_a_key_waits_for_one(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path, key=False)

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        await session.choose("prepare")
        await session.finished()
        app = session.app
        return (
            session.launched,
            _text(app, "#launch-progress-status"),
            # The key can be made while the scans are read.
            app.query_one("#section-answer-key", Collapsible).collapsed,
            app.query_one("#ak-output", Input).value,
        )

    launched, status, key_tool_collapsed, key_output = _drive(steps)

    (request,) = pipeline_calls
    assert request.qa_file is None
    assert launched == []
    assert "waiting for an answer key" in status
    assert key_tool_collapsed is False
    assert key_output == str(folder / "qa.md")


def test_submissions_without_a_key_ask_only_for_the_key(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path, key=False)
    _add_submission(folder)

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        return _buttons(session.app), _text(session.app, "#prepare-folder-found")

    buttons, found = _drive(steps)

    assert buttons == ["cancel", "create-key"]
    assert "1 submission(s) are read" in found
    assert pipeline_calls == []


# --- submissions read before there was an answer key -------------------------


def _read_without_a_key(tmp_path: Path) -> Path:
    folder = _class_folder(tmp_path)
    _add_submission(folder)
    _record_stage(folder, "extract")
    return folder


def test_a_folder_read_before_its_key_is_offered_auto_grading(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = _read_without_a_key(tmp_path)

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        buttons, launched = _buttons(session.app), list(session.launched)
        await session.choose("auto-grade")
        await session.finished()
        return buttons, launched, session.launched

    buttons, launched_before, launched = _drive(steps)

    assert buttons == ["cancel", "open", "auto-grade"]
    assert launched_before == []
    (request,) = pipeline_calls
    assert request.qa_file == folder / "qa.md"
    assert launched == [(str(folder / "qa.md"), str(folder / "submissions"))]


def test_declining_auto_grading_opens_the_folder_and_is_not_asked_twice(
    tmp_path, pipeline_calls  # noqa: ANN001
) -> None:
    folder = _read_without_a_key(tmp_path)

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        await session.choose("open")
        await session.launch(folder)
        await session.pilot.pause()
        return session.launched, _dialog(session.app) is None

    launched, no_second_dialog = _drive(steps)

    pair = (str(folder / "qa.md"), str(folder / "submissions"))
    assert launched == [pair, pair]
    assert no_second_dialog
    assert pipeline_calls == []


def test_an_auto_graded_folder_just_opens(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = _read_without_a_key(tmp_path)
    _record_stage(folder, "auto-grade")

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.pilot.pause()
        return session.launched, _dialog(session.app) is None

    launched, no_dialog = _drive(steps)

    assert launched == [(str(folder / "qa.md"), str(folder / "submissions"))]
    assert no_dialog
    assert pipeline_calls == []


# --- scans that wait for the answer key, and a key drafted from the template --


def _waits_for_key_until_there_is_one(calls: list):
    """Stands in for the run: no answer key, so it stops before reading."""
    from opengrader.core.pipeline import ANSWER_KEY_DETAIL

    def run(request, *, progress=None):  # noqa: ANN001, ANN202
        calls.append(request)
        if request.qa_file is None and not request.read_without_key:
            outcome = StageOutcome(
                stage="extract",
                status=StageStatus.PAUSED,
                message="the scans are split and waiting for an answer key",
                detail=f"{ANSWER_KEY_DETAIL}\n…",
            )
            return PipelineReport(stages=(outcome,), submissions_dir=request.submissions_dir, qa_file=None)
        _add_submission(request.output_dir)
        return PipelineReport(
            stages=(StageOutcome(stage="extract", status=StageStatus.RAN, message="ok"),),
            submissions_dir=request.submissions_dir,
            qa_file=request.qa_file,
        )

    return run


def test_a_run_waiting_for_the_key_carries_on_by_itself_once_it_is_made(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path, key=False)
    (folder / "solutions.txt").write_text("1. What is 2 + 2?\n", encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(setup_screen, "run_pipeline", _waits_for_key_until_there_is_one(calls))

    def write_key(argv, **_hooks):  # noqa: ANN001, ANN202
        Path(argv[argv.index("--output") + 1]).write_text(_QA_TEXT, encoding="utf-8")

    monkeypatch.setattr(setup_screen, "run_answer_key_cli", write_key)

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        buttons = _buttons(session.app)
        label = str(session.app.screen.query_one("#prepare-folder-prepare", Button).label)
        await session.choose("prepare")
        await session.finished()
        waiting = (
            _text(session.app, "#launch-progress-status"),
            session.app.query_one("#section-answer-key", Collapsible).collapsed,
            list(session.launched),
        )
        session.app.query_one("#run-answer-key", Button).press()
        await session.app.workers.wait_for_complete()
        await _settle(session.pilot, lambda: len(calls) == 2)
        await session.finished()
        return buttons, label, waiting, session.launched, _dialog(session.app) is None

    buttons, label, (status, key_tool_collapsed, launched_while_waiting), launched, no_second_ask = _drive(steps)

    assert buttons == ["cancel", "settings", "create-key", "prepare"]
    # No blank template: the key is what finds the questions, so this run stops after the split.
    assert label == "Check and split the scans now"
    assert "waits for an answer key" in status
    assert key_tool_collapsed is False and launched_while_waiting == []
    # The yes was already given: the key being written is all it took.
    assert [request.qa_file for request in calls] == [None, folder / "qa.md"]
    assert no_second_ask
    assert launched == [(str(folder / "qa.md"), str(folder / "submissions"))]


def test_reading_without_a_key_is_a_one_time_choice_on_the_pipeline_card(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path, key=False)
    calls: list = []
    monkeypatch.setattr(setup_screen, "run_pipeline", _waits_for_key_until_there_is_one(calls))

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        await session.choose("settings")
        session.app.query_one("#pipe-no-key", Checkbox).value = True
        session.screen._run_pipeline()
        await session.finished()

    _drive(steps)

    (request,) = calls
    assert request.read_without_key is True and request.qa_file is None
    # Not remembered with the folder: it belonged to that run.
    assert load_folder_settings(folder) == FolderPipelineSettings()


def _blank_template(folder: Path) -> Path:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    for y, line in (
        (80, "Name: ____________   ID: ________"),
        (140, "1. (5 points) What is the chemical symbol for gold?"),
        (260, "2) Explain why ice floats on water."),
    ):
        page.insert_text((72, y), line, fontsize=12)
    path = folder / "blank-template.pdf"
    doc.save(path)
    doc.close()
    return path


def test_with_a_blank_template_the_answer_key_is_drafted_and_grading_can_open(
    tmp_path, pipeline_calls  # noqa: ANN001
) -> None:
    from opengrader.core.qa_file import parse_qa_markdown

    folder = _class_folder(tmp_path, key=False)
    _blank_template(folder)

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        note = _text(session.app, "#prepare-folder-note")
        label = str(session.app.screen.query_one("#prepare-folder-prepare", Button).label)
        written_before_yes = (folder / "qa.md").exists()
        await session.choose("prepare")
        await session.finished()
        return note, label, written_before_yes, session.launched, session.notices

    note, label, written_before_yes, launched, notices = _drive(steps)

    assert label == "Draft the key and read the scans"
    assert "2 question(s)" in note and "1 with no points printed" in note
    assert written_before_yes is False
    first, second = parse_qa_markdown((folder / "qa.md").read_text()).questions
    assert (first.points, first.prompt, first.answers) == (5, "What is the chemical symbol for gold?", [])
    # No points printed: the standard ones. No answer is ever made up.
    assert (second.points, second.answers) == (10, [])
    (request,) = pipeline_calls
    assert (request.qa_file, request.blank_template) == (folder / "qa.md", folder / "blank-template.pdf")
    assert launched == [(str(folder / "qa.md"), str(folder / "submissions"))]
    assert any("answers are blank" in notice for notice in notices)


def test_the_draft_is_only_an_offer_create_it_myself_writes_nothing(tmp_path, pipeline_calls) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path, key=False)
    _blank_template(folder)

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        await session.choose("create-key")
        return session.app.query_one("#section-answer-key", Collapsible).collapsed

    assert _drive(steps) is False
    assert not (folder / "qa.md").exists()
    assert pipeline_calls == []


# --- Open in browser takes the same path -------------------------------------


@pytest.fixture
def browser_opens(monkeypatch) -> list[tuple[str, str]]:  # noqa: ANN001
    opened: list[tuple[str, str]] = []
    monkeypatch.setattr(
        setup_screen.BrowserLauncher,
        "open_folder",
        lambda _self, qa, subs: opened.append((str(qa), str(subs))),
    )
    return opened


async def _open_in_browser(session: _Session, folder: Path) -> None:
    session.app.query_one("#tui-class-folder", Input).value = str(folder)
    session.screen._open_in_browser()
    await session.pilot.pause()


def test_open_in_browser_opens_a_ready_folder_in_the_browser(tmp_path, pipeline_calls, browser_opens) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path)
    _add_submission(folder)

    async def steps(session: _Session):  # noqa: ANN202
        await _open_in_browser(session, folder)
        return session.launched, _dialog(session.app) is None

    launched, no_dialog = _drive(steps)

    assert browser_opens == [(str(folder / "qa.md"), str(folder / "submissions"))]
    # The grade screen is not opened as well.
    assert launched == [] and no_dialog and pipeline_calls == []


def test_open_in_browser_prepares_an_unprepared_folder_then_opens_the_browser(
    tmp_path, pipeline_calls, browser_opens  # noqa: ANN001
) -> None:
    folder = _class_folder(tmp_path)

    async def steps(session: _Session):  # noqa: ANN202
        await _open_in_browser(session, folder)
        await session.asked()
        note = _text(session.app, "#prepare-folder-note")
        opened_before_yes = list(browser_opens)
        await session.choose("prepare")
        await session.finished()
        return note, opened_before_yes, session.launched

    note, opened_before_yes, launched = _drive(steps)

    assert "The browser opens when it finishes." in note
    assert opened_before_yes == []
    (request,) = pipeline_calls
    assert request.output_dir == folder
    assert browser_opens == [(str(folder / "qa.md"), str(folder / "submissions"))]
    assert launched == []


def test_open_in_browser_with_no_answer_key_drafts_it_and_still_ends_in_the_browser(
    tmp_path, pipeline_calls, browser_opens  # noqa: ANN001
) -> None:
    folder = _class_folder(tmp_path, key=False)
    _blank_template(folder)

    async def steps(session: _Session):  # noqa: ANN202
        await _open_in_browser(session, folder)
        await session.asked()
        label = str(session.app.screen.query_one("#prepare-folder-prepare", Button).label)
        await session.choose("prepare")
        # The answers form opens in the app while the scans are read.
        await _settle(session.pilot, lambda: bool(session.app.screen.query("#answers-dialog")))
        session.app.screen.query_one("#answers-cancel", Button).press()
        await session.finished()
        return label, session.launched

    label, launched = _drive(steps)

    assert label == "Draft the key and read the scans"
    assert (folder / "qa.md").exists()
    assert browser_opens == [(str(folder / "qa.md"), str(folder / "submissions"))]
    assert launched == []


def test_a_key_made_after_open_in_browser_carries_on_to_the_browser(
    tmp_path, monkeypatch, browser_opens  # noqa: ANN001
) -> None:
    folder = _class_folder(tmp_path, key=False)
    (folder / "solutions.txt").write_text("1. What is 2 + 2?\n", encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(setup_screen, "run_pipeline", _waits_for_key_until_there_is_one(calls))

    def write_key(argv, **_hooks):  # noqa: ANN001, ANN202
        Path(argv[argv.index("--output") + 1]).write_text(_QA_TEXT, encoding="utf-8")

    monkeypatch.setattr(setup_screen, "run_answer_key_cli", write_key)

    async def steps(session: _Session):  # noqa: ANN202
        await _open_in_browser(session, folder)
        await session.asked()
        await session.choose("prepare")
        await session.finished()
        session.app.query_one("#run-answer-key", Button).press()
        await session.app.workers.wait_for_complete()
        await _settle(session.pilot, lambda: len(calls) == 2)
        await session.finished()
        return session.launched

    assert _drive(steps) == []
    assert browser_opens == [(str(folder / "qa.md"), str(folder / "submissions"))]


def test_launch_grading_after_open_in_browser_goes_back_to_the_grade_screen(
    tmp_path, pipeline_calls, browser_opens  # noqa: ANN001
) -> None:
    folder = _class_folder(tmp_path)
    _add_submission(folder)

    async def steps(session: _Session):  # noqa: ANN202
        await _open_in_browser(session, folder)
        await session.launch(folder)
        return session.launched

    assert _drive(steps) == [(str(folder / "qa.md"), str(folder / "submissions"))]
    assert len(browser_opens) == 1


# One scan per section, and a folder of assignments ---------------------------


def test_a_folder_of_section_scans_is_prepared_as_one_assignment(
    tmp_path, pipeline_calls
) -> None:  # noqa: ANN001
    folder = tmp_path / "quiz1"
    folder.mkdir()
    (folder / "qa.md").write_text(_QA_TEXT, encoding="utf-8")
    for name in ("section-01.pdf", "section-02.pdf"):
        (folder / name).write_bytes(b"%PDF-1.4\n")

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(folder)
        await session.asked()
        summary = _text(session.app, "#prepare-folder-found")
        await session.choose("prepare")
        await session.finished()
        return summary

    summary = _drive(steps)

    assert "one per section, 2 sections" in summary
    # Found by the folder scan, so found again next time rather than frozen.
    saved = load_folder_settings(folder)
    assert saved is not None and saved.use_section_scans and saved.section_scans == ()
    (request,) = pipeline_calls
    assert request.scan == folder.resolve()
    assert request.section_scans == (
        f"{folder / 'section-01.pdf'}=Section 01",
        f"{folder / 'section-02.pdf'}=Section 02",
    )


def _assignment(folder: Path, title: str) -> Path:
    folder.mkdir(parents=True)
    (folder / "qa.md").write_text(
        _QA_TEXT.replace("title: Quiz", f"title: {title}"), encoding="utf-8"
    )
    _add_submission(folder)
    return folder


def test_a_folder_of_assignments_is_offered_as_a_workspace(tmp_path) -> None:  # noqa: ANN001
    from opengrader.tui.make_workspace import MakeWorkspaceRequest

    root = tmp_path / "Fall 2026"
    quiz = _assignment(root / "Quiz 1", "Quiz 1")
    midterm = _assignment(root / "Midterm", "Midterm")
    (root / "Final").mkdir()
    (root / "Final" / "qa.md").write_text(
        _QA_TEXT.replace("title: Quiz", "title: Final"), encoding="utf-8"
    )
    (root / "Final" / "scans.pdf").write_bytes(b"%PDF-1.4\n")

    async def steps(session: _Session):  # noqa: ANN202
        session.app.query_one("#tui-class-folder", Input).value = str(root)
        session.screen._scan_launch_folder()
        await session.pilot.pause()
        shown = not session.app.query_one("#workspace-button").has_class("hidden")
        card = _text(session.app, "#tui-folder-summary")
        session.app.query_one("#launch-button", Button).press()
        await session.asked()
        buttons = _buttons(session.app)
        found = _text(session.app, "#prepare-folder-found")
        # The Workspace card's question: this window closes classic.
        session.app.screen.query_one("#prepare-folder-terminal", Button).press()
        await _settle(session.pilot, lambda: session.app.return_value is not None)
        return shown, card, buttons, found, session.app.return_value

    shown, card, buttons, found, result = _drive(steps)

    assert shown
    assert "holds 3 assignment(s)" in card and "Final/" in card
    assert "terminal" in buttons and "browser" in buttons
    assert "Left out for now" in found and "Final" in found  # not prepared yet
    assert isinstance(result, MakeWorkspaceRequest)
    assert result.root == root
    assert sorted(result.assignments) == sorted(
        [
            (quiz / "qa.md", quiz / "submissions"),
            (midterm / "qa.md", midterm / "submissions"),
        ]
    )


def test_sections_kept_as_separate_folders_are_not_offered_as_a_workspace(
    tmp_path,
) -> None:  # noqa: ANN001
    root = tmp_path / "Fall 2026"
    _assignment(root / "Quiz 1 Sec 1", "Quiz 1")
    _assignment(root / "Quiz 1 Sec 2", "Quiz 1")

    async def steps(session: _Session):  # noqa: ANN202
        session.app.query_one("#tui-class-folder", Input).value = str(root)
        session.screen._scan_launch_folder()
        await session.pilot.pause()
        hidden = session.app.query_one("#workspace-button").has_class("hidden")
        card = _text(session.app, "#tui-folder-summary")
        await session.launch(root)
        return hidden, card, _dialog(session.app), list(session.notices)

    hidden, card, dialog, notices = _drive(steps)

    assert hidden and dialog is None
    assert "same answer key" in card and "one scan per section" in card
    assert any("Choose one of them" in notice for notice in notices)


def _workspace(root: Path, *titles: str) -> Path:
    from opengrader.core.workspace.store import Workspace

    workspace = Workspace.create(root)
    for title in titles:
        workspace.create_assignment(
            _QA_TEXT.replace("title: Quiz", f"title: {title}")
        )
    return root


async def _choose_folder(session: _Session, folder: Path):  # noqa: ANN202
    session.app.query_one("#tui-class-folder", Input).value = str(folder)
    session.screen._scan_launch_folder()
    await session.pilot.pause()
    button = session.app.query_one("#workspace-button", Button)
    return (
        None if button.has_class("hidden") else str(button.label),
        _text(session.app, "#tui-folder-summary"),
    )


def test_a_workspace_folder_offers_to_open_it_and_opens_on_the_press(tmp_path) -> None:  # noqa: ANN001
    from opengrader.tui.make_workspace import OpenWorkspaceRequest

    root = _workspace(tmp_path / "Fall 2026", "Quiz 1", "Midterm")

    async def steps(session: _Session):  # noqa: ANN202
        label, card = await _choose_folder(session, root)
        # Choosing it opens nothing by itself.
        still_here = session.app.return_value is None
        session.app.query_one("#workspace-button", Button).press()
        await _settle(session.pilot, lambda: session.app.return_value is not None)
        return label, card, still_here, session.app.return_value, _dialog(session.app)

    label, card, still_here, result, dialog = _drive(steps)

    assert label == "Open workspace"
    assert "workspace with 2 assignment(s): Quiz 1, Midterm" in card
    assert still_here and dialog is None  # opening asks nothing
    assert result == OpenWorkspaceRequest(root=root)


def test_launch_grading_on_a_workspace_opens_it(tmp_path) -> None:  # noqa: ANN001
    from opengrader.tui.make_workspace import OpenWorkspaceRequest

    root = _workspace(tmp_path / "Fall 2026", "Quiz 1")

    async def steps(session: _Session):  # noqa: ANN202
        await session.launch(root)
        await _settle(session.pilot, lambda: session.app.return_value is not None)
        return session.app.return_value

    assert _drive(steps) == OpenWorkspaceRequest(root=root)


def test_a_workspace_in_a_subfolder_is_offered_by_name(tmp_path) -> None:  # noqa: ANN001
    from opengrader.tui.make_workspace import OpenWorkspaceRequest

    root = tmp_path / "Fall 2026"
    _assignment(root / "Quiz 1", "Quiz 1")
    inner = _workspace(root / "Old term", "Final")

    async def steps(session: _Session):  # noqa: ANN202
        label, card = await _choose_folder(session, root)
        session.app.query_one("#workspace-button", Button).press()
        await _settle(session.pilot, lambda: session.app.return_value is not None)
        return label, card, session.app.return_value

    label, card, result = _drive(steps)

    assert label == "Open “Old term” workspace"
    assert "Old term/ is a workspace of its own" in card
    assert result == OpenWorkspaceRequest(root=inner)


def test_a_class_folder_opened_in_the_browser_is_still_graded_in_place(tmp_path) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path)
    submissions = _add_submission(folder)
    marker = folder / ".opengrader" / "folder-session.json"
    _workspace(folder)
    marker.write_text("{}", encoding="utf-8")

    async def steps(session: _Session):  # noqa: ANN202
        label, _card = await _choose_folder(session, folder)
        await session.launch(folder)
        return label, session.launched

    label, launched = _drive(steps)

    assert label is None
    assert launched == [(str(folder / "qa.md"), str(submissions))]


# Recent class folders --------------------------------------------------------


def _choose_recent(session: _Session, folder: Path, *, browser: bool) -> None:  # noqa: ANN001
    from types import SimpleNamespace

    from opengrader.core.recents import RecentItem

    session.app.query_one("#browser-open-button").set_class(not browser, "hidden")
    item = RecentItem(
        kind="folder",
        path=str(folder),
        title="Quiz",
        qa_file=str(folder / "qa.md"),
        submissions=str(folder / "submissions"),
    )
    session.screen.on_recent_list_chosen(SimpleNamespace(item=item, stop=lambda: None))


def test_a_recent_folder_opens_at_once_when_there_is_no_browser_button(tmp_path) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path)
    submissions = _add_submission(folder)

    async def steps(session: _Session):  # noqa: ANN202
        _choose_recent(session, folder, browser=False)
        await session.pilot.pause()
        return session.launched

    assert _drive(steps) == [(str(folder / "qa.md"), str(submissions))]


def test_a_recent_folder_is_only_chosen_when_it_could_open_in_the_browser(tmp_path) -> None:  # noqa: ANN001
    folder = _class_folder(tmp_path)
    submissions = _add_submission(folder)

    async def steps(session: _Session):  # noqa: ANN202
        _choose_recent(session, folder, browser=True)
        await session.pilot.pause()
        filled = (
            session.app.query_one("#tui-class-folder", Input).value,
            session.app.query_one("#tui-qa-path", Input).value,
            session.app.query_one("#tui-sub-path", Input).value,
        )
        waiting = list(session.launched)
        focused = session.app.focused.id if session.app.focused else None
        card = _text(session.app, "#tui-folder-summary")
        # Launch Grading then opens exactly the pair recorded.
        session.app.query_one("#launch-button", Button).press()
        await session.pilot.pause()
        return filled, waiting, focused, card, session.launched

    filled, waiting, focused, card, launched = _drive(steps)

    assert filled == (str(folder), str(folder / "qa.md"), str(submissions))
    assert waiting == [] and focused == "launch-button"
    assert "Open in browser" in card
    assert launched == [(str(folder / "qa.md"), str(submissions))]
