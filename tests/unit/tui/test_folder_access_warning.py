"""Opening a class folder this account cannot write says so at once."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from opengrader.tui.tui import TuiApp

from .fakes import DEFAULT_IMAGE_PATH

REPO = Path(__file__).resolve().parents[3]


def _titles(app: TuiApp) -> list[str]:
    return [notification.title for notification in app._notifications]


def _open(tmp_path: Path) -> list[str]:
    seen: list[str] = []

    async def run() -> None:
        app = TuiApp(
            qa_file_path=tmp_path / "quiz" / "qa.md",
            submissions_path=tmp_path / "quiz" / "student-submissions",
            default_image_path=DEFAULT_IMAGE_PATH,
        )
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            seen.extend(_titles(app))

    asyncio.run(run())
    return seen


@pytest.fixture
def quiz(tmp_path: Path) -> Path:
    shutil.copytree(REPO / "samples" / "cs146-quiz1", tmp_path / "quiz", ignore=shutil.ignore_patterns(".opengrader", "._*"))
    return tmp_path


def test_a_writable_folder_opens_without_a_warning(quiz) -> None:
    assert "Cannot save here" not in _open(quiz)


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_a_folder_this_account_cannot_write_warns_on_opening(quiz) -> None:
    submissions = quiz / "quiz" / "student-submissions"
    files = list(submissions.rglob("*.submission.md"))
    for path in files:
        path.chmod(0o444)
    try:
        assert "Cannot save here" in _open(quiz)
    finally:
        for path in files:
            path.chmod(0o644)
