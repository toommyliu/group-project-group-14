from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Button, Checkbox, Input

from opengrader.core.crypto import is_encrypted
from opengrader.core.integrity import resolve_snapshot_path
from opengrader.tui.ui.screens.backup_screen import BackupScreen

SUBMISSION = """---
student_name: "Ada Lovelace"
student_id: "1001"
course_code: "CS146"
assignment_title: "Quiz 1"
source_pdf: ""
student_score: ""
---


- q1:
  - Question type: Free Response
  - Prompt: What is a loop?
  - Response text: a repeat
  - Possible points: 5
  - Points: 3
  - Rubric: Correct
  - Analysis:
"""


class _Host(App):
    def __init__(self, screen: BackupScreen) -> None:
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


@pytest.fixture()
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "subs"
    student = root / "sub-0001"
    (student / "responses").mkdir(parents=True)
    (student / "sub-0001.submission.md").write_text(SUBMISSION)
    (student / "responses" / "q1.png").write_bytes(b"crop")
    return root


def _log_text(screen: BackupScreen) -> str:
    return "".join(str(line) for line in screen.query_one("#backup-log").lines)


async def _settle(pilot, predicate, limit: int = 80) -> None:  # noqa: ANN001
    for _ in range(limit):
        await pilot.pause()
        await asyncio.sleep(0.01)
        if predicate():
            return


def test_it_says_up_front_when_no_checksums_exist(folder: Path) -> None:
    async def run() -> None:
        screen = BackupScreen(submissions_path=str(folder))
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert "No checksums recorded" in _log_text(screen)

    asyncio.run(run())


def test_recording_checksums_writes_the_snapshot(folder: Path) -> None:
    async def run() -> None:
        screen = BackupScreen(submissions_path=str(folder))
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#backup-snapshot")
            await _settle(
                pilot, lambda: not screen.query_one("#backup-snapshot", Button).disabled
            )
            assert resolve_snapshot_path(folder).is_file()
            assert "Recorded" in _log_text(screen)

    asyncio.run(run())


def test_verify_names_a_corrupted_file(folder: Path) -> None:
    async def run() -> None:
        screen = BackupScreen(submissions_path=str(folder))
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#backup-snapshot")
            await _settle(
                pilot, lambda: not screen.query_one("#backup-snapshot", Button).disabled
            )
            (folder / "sub-0001" / "responses" / "q1.png").write_bytes(b"bad")
            await pilot.click("#backup-verify")
            await _settle(pilot, lambda: "need attention" in _log_text(screen))
            assert "q1.png" in _log_text(screen)

    asyncio.run(run())


def test_verify_without_a_snapshot_explains_rather_than_crashes(folder: Path) -> None:
    async def run() -> None:
        screen = BackupScreen(submissions_path=str(folder))
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#backup-verify")
            await _settle(pilot, lambda: "snapshot" in _log_text(screen).lower())
            assert "run 'snapshot' first" in _log_text(screen)

    asyncio.run(run())


def test_backup_writes_a_verified_archive(folder: Path, tmp_path: Path) -> None:
    async def run() -> None:
        screen = BackupScreen(
            submissions_path=str(folder), archive_path=str(tmp_path / "b.zip")
        )
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#backup-run")
            await _settle(pilot, lambda: (tmp_path / "b.zip").is_file())
            await _settle(pilot, lambda: "verified" in _log_text(screen).lower())
            assert (tmp_path / "b.zip").is_file()
            assert "Read back and verified" in _log_text(screen)

    asyncio.run(run())


def test_checking_an_archive_reports_it_intact(folder: Path, tmp_path: Path) -> None:
    async def run() -> None:
        screen = BackupScreen(
            submissions_path=str(folder), archive_path=str(tmp_path / "b.zip")
        )
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#backup-run")
            await _settle(pilot, lambda: (tmp_path / "b.zip").is_file())
            await _settle(pilot, lambda: "verified" in _log_text(screen).lower())

            await pilot.click("#backup-check")
            await _settle(pilot, lambda: "Intact" in _log_text(screen))
            assert "Intact" in _log_text(screen)

    asyncio.run(run())


def test_the_archive_field_is_hidden_until_the_box_is_ticked(folder: Path) -> None:
    async def run() -> None:
        screen = BackupScreen(submissions_path=str(folder))
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert screen.query_one("#backup-custom-archive", Checkbox).value is False
            assert screen.query_one("#backup-archive-area").has_class("hidden")

            screen.query_one("#backup-custom-archive", Checkbox).value = True
            await pilot.pause()
            assert not screen.query_one("#backup-archive-area").has_class("hidden")

    asyncio.run(run())


def test_the_hint_names_the_archive_and_explains_the_snapshot(folder: Path) -> None:
    async def run() -> None:
        screen = BackupScreen(submissions_path=str(folder))
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            hint = str(screen.query_one("#backup-archive-hint").render())
            assert f"{folder.name}-backup-" in hint
            assert ".opengrader/integrity.json" in hint

    asyncio.run(run())


def test_backing_up_with_the_box_unticked_writes_beside_the_folder(
    folder: Path,
) -> None:
    async def run() -> None:
        screen = BackupScreen(submissions_path=str(folder))
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#backup-run")
            await _settle(pilot, lambda: bool(list(folder.parent.glob("*.zip"))))
            written = list(folder.parent.glob("*.zip"))
            assert len(written) == 1
            assert not list(folder.rglob("*.zip"))

    asyncio.run(run())


def test_a_stale_archive_path_in_a_hidden_field_is_ignored(
    folder: Path, tmp_path: Path
) -> None:
    async def run() -> None:
        screen = BackupScreen(submissions_path=str(folder))
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen.query_one("#backup-custom-archive", Checkbox).value = True
            screen.query_one("#backup-archive", Input).value = str(tmp_path / "stale.zip")
            await pilot.pause()
            screen.query_one("#backup-custom-archive", Checkbox).value = False
            await pilot.pause()
            await pilot.click("#backup-run")
            await _settle(pilot, lambda: bool(list(folder.parent.glob("*.zip"))))
            assert not (tmp_path / "stale.zip").exists()

    asyncio.run(run())


def test_a_passphrase_encrypts_the_archive(folder: Path, tmp_path: Path) -> None:
    async def run() -> None:
        archive = tmp_path / "quiz1.zip.enc"
        screen = BackupScreen(
            submissions_path=str(folder), archive_path=str(archive)
        )
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen.query_one("#backup-encrypt", Checkbox).value = True
            await pilot.pause()
            screen.query_one("#backup-passphrase", Input).value = "correct horse"
            screen.query_one("#backup-passphrase-confirm", Input).value = "correct horse"
            await pilot.click("#backup-run")
            await _settle(
                pilot, lambda: not screen.query_one("#backup-run", Button).disabled
            )

            assert archive.is_file()
            assert is_encrypted(archive)
            assert b"grades" not in archive.read_bytes()
            assert "Encrypted" in _log_text(screen)

    asyncio.run(run())


def test_mismatched_passphrases_write_nothing(folder: Path, tmp_path: Path) -> None:
    async def run() -> None:
        archive = tmp_path / "quiz1.zip.enc"
        screen = BackupScreen(
            submissions_path=str(folder), archive_path=str(archive)
        )
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen.query_one("#backup-encrypt", Checkbox).value = True
            await pilot.pause()
            screen.query_one("#backup-passphrase", Input).value = "one"
            screen.query_one("#backup-passphrase-confirm", Input).value = "two"
            await pilot.click("#backup-run")
            await pilot.pause()
            assert not archive.exists()

    asyncio.run(run())


def test_the_passphrase_fields_stay_hidden_until_asked_for(
    folder: Path, tmp_path: Path
) -> None:
    async def run() -> None:
        screen = BackupScreen(submissions_path=str(folder))
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert screen.query_one("#backup-passphrase-area").has_class("hidden")
            screen.query_one("#backup-encrypt", Checkbox).value = True
            await pilot.pause()
            assert not screen.query_one("#backup-passphrase-area").has_class("hidden")
            assert str(screen._resolved_archive()).endswith(".zip.enc")

    asyncio.run(run())
