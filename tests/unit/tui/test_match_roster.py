"""Opening a class folder whose papers are not all matched to the roster."""

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Button, Input, ListView

from opengrader.core import browser_app
from opengrader.core.submissions.student_file import load_student_submission_markdown
from opengrader.tui.data.submission_files_provider import SubmissionFilesProvider
from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens.match_roster_screen import MatchRosterPrompt
from opengrader.tui.ui.screens.review_queue_screen import (
    AssignIdentityScreen,
    rank_roster,
)
from opengrader.core.roster import RosterStudent

from .fakes import DEFAULT_IMAGE_PATH
from .test_tui_app import workspace_provider  # noqa: F401 - fixture

QA = """---
title: Quiz
---

- (10 points) Convert.
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
"""


def _paper(path: Path, *, student_id: str, name: str) -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                f'student_name: "{name}"',
                f'student_id: "{student_id}"',
                'assignment_title: "Quiz"',
                f'source_pdf: "{path.stem}.pdf"',
                "---",
                "",
                "- q1:",
                "  - Question type: Free Response",
                "  - Prompt: Convert.",
                "  - Response text: x",
                "  - Possible points: 10",
                "  - Points: ",
                "",
            ]
        )
    )


def _class_folder(tmp_path: Path) -> tuple[Path, Path]:
    (tmp_path / "qa.md").write_text(QA)
    (tmp_path / "roster.tsv").write_text(
        "111111111\tTommy Liu\n222222222\tAndy Cortes\n333333333\tAmy Park\n"
    )
    submissions = tmp_path / "submissions"
    submissions.mkdir()
    _paper(submissions / "p1.submission.md", student_id="111111111", name="Tommy Liu")
    _paper(submissions / "p2.submission.md", student_id="22228", name="Andy Cortez")
    return tmp_path / "qa.md", submissions


def _app(tmp_path: Path) -> tuple[TuiApp, Path]:
    qa, submissions = _class_folder(tmp_path)
    provider = SubmissionFilesProvider(
        qa_file_path=qa, submissions_path=submissions, default_image_path=DEFAULT_IMAGE_PATH
    )
    return TuiApp(default_image_path=DEFAULT_IMAGE_PATH, data_provider=provider), submissions


@pytest.fixture
def browser_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        browser_app, "load_browser_preferences", lambda *a, **k: browser_app.BrowserPreferences()
    )


def test_rank_roster_puts_the_closest_student_first_and_filters_by_typing() -> None:
    roster = [
        RosterStudent("111111111", "Tommy Liu"),
        RosterStudent("222222222", "Andy Cortes"),
        RosterStudent("333333333", "Amy Park"),
    ]
    ranked = rank_roster(roster, read_name="Andy Cortez", read_id="22228")
    assert ranked[0].student_id == "222222222"
    assert [s.name for s in rank_roster(roster, read_name="", read_id="", query="am")] == [
        "Amy Park"
    ]
    assert [s.name for s in rank_roster(roster, read_name="", read_id="", query="3333")] == [
        "Amy Park"
    ]


def test_opening_asks_to_match_and_match_here_saves_the_pick(
    tmp_path: Path, browser_off: None
) -> None:
    async def run() -> None:
        app, submissions = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.screen
            assert isinstance(prompt, MatchRosterPrompt)
            # The browser app is off: only matching here is offered.
            assert not prompt.query("#match-roster-browser")
            prompt.query_one("#match-roster-here", Button).press()
            await pilot.pause()

            dialog = app.screen
            assert isinstance(dialog, AssignIdentityScreen)
            rows = dialog.query_one("#identity-list", ListView).children
            assert "222222222" in str(rows[0].query_one("Label").render())
            dialog.query_one("#identity-filter", Input).value = "cortes"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert not isinstance(app.screen, AssignIdentityScreen)
        paper = load_student_submission_markdown(submissions / "p2.submission.md")
        assert (paper.student_id, paper.student_name) == ("222222222", "Andy Cortes")

    asyncio.run(run())


def test_later_leaves_the_papers_as_they_are(tmp_path: Path, browser_off: None) -> None:
    async def run() -> None:
        app, submissions = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, MatchRosterPrompt)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, MatchRosterPrompt)
        paper = load_student_submission_markdown(submissions / "p2.submission.md")
        assert paper.student_id == "22228"

    asyncio.run(run())


def test_no_prompt_when_every_paper_is_matched(tmp_path: Path, browser_off: None) -> None:
    async def run() -> None:
        qa, submissions = _class_folder(tmp_path)
        _paper(submissions / "p2.submission.md", student_id="222222222", name="Andy Cortes")
        provider = SubmissionFilesProvider(
            qa_file_path=qa, submissions_path=submissions, default_image_path=DEFAULT_IMAGE_PATH
        )
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH, data_provider=provider)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert not isinstance(app.screen, MatchRosterPrompt)

    asyncio.run(run())


def test_match_in_browser_opens_this_folder_on_the_papers_needing_a_student(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        browser_app,
        "load_browser_preferences",
        lambda *a, **k: browser_app.BrowserPreferences(enabled=True),
    )
    from opengrader.tui.ui.widgets.browser_launcher import BrowserLauncher

    opened: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        BrowserLauncher,
        "open_folder",
        lambda self, qa, sub, *, page="": opened.append((str(qa), str(sub), page)),
    )

    async def run() -> None:
        app, submissions = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt = app.screen
            assert isinstance(prompt, MatchRosterPrompt)
            prompt.query_one("#match-roster-browser", Button).press()
            await pilot.pause()
        assert opened == [
            (str(tmp_path / "qa.md"), str(submissions), "submissions?show=attention")
        ]

    asyncio.run(run())


def test_workspace_session_opens_its_own_submissions_page(
    workspace_provider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opengrader.core.submissions.roster_check import RosterCoverage
    from opengrader.tui.ui.widgets import browser_launcher

    opened: list[str] = []
    monkeypatch.setattr(browser_launcher, "open_url", lambda url: opened.append(url) or True)
    monkeypatch.setattr(
        type(workspace_provider), "roster_coverage", lambda self: RosterCoverage(2, 1)
    )

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH, data_provider=workspace_provider)
        async with app.run_test() as pilot:
            for _ in range(20):
                await pilot.pause()
                if isinstance(app.screen, MatchRosterPrompt):
                    break
            prompt = app.screen
            assert isinstance(prompt, MatchRosterPrompt)
            # A workspace session has no files here to match in.
            assert not prompt.query("#match-roster-here")
            prompt.query_one("#match-roster-browser", Button).press()
            await pilot.pause()

    asyncio.run(run())
    assert opened == [
        f"http://testserver/assignments/{workspace_provider.assignment.id}"
        "/submissions?show=attention"
    ]
