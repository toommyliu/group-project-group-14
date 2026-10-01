"""Quitting the desktop app leaves nothing behind (macOS icon only).

`packaging/Opengrader.command` is what the icon opens in a terminal window.
Terminal.app keeps a finished window open, so after a clean exit the script
asks Terminal to close it; kitty is told to quit with its last window by the
app bundle's stub. Neither applies to the `opengrader` terminal command.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGING = REPO_ROOT / "packaging"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the .command entry point is macOS-only"
)


@pytest.fixture
def desktop(tmp_path: Path):
    """An install root with a stand-in launcher and a recording `osascript`."""
    root = tmp_path / "Application Support" / "Opengrader"
    root.mkdir(parents=True)
    shutil.copy(PACKAGING / "Opengrader.command", root / "Opengrader.command")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "osascript-calls"
    fake = bin_dir / "osascript"
    fake.write_text(f'#!/bin/bash\nprintf \'%s\\n\' "$*" >> "{calls}"\n')
    fake.chmod(0o755)

    def run(*, exit_status: int, term_program: str) -> tuple[int, str, str]:
        launcher = root / "bootstrap.sh"
        launcher.write_text(f"#!/bin/bash\nexit {exit_status}\n")
        launcher.chmod(0o755)
        controller, terminal = os.openpty()
        try:
            completed = subprocess.run(
                [str(root / "Opengrader.command")],
                stdin=terminal,
                stdout=terminal,
                stderr=terminal,
                env={
                    "PATH": f"{bin_dir}:/usr/bin:/bin",
                    "TERM_PROGRAM": term_program,
                },
                timeout=30,
            )
            tty_name = os.ttyname(terminal)
            # The close is asked for half a second after the script has exited;
            # a case that should not ask is given three times that to do so.
            expected = exit_status == 0 and term_program == "Apple_Terminal"
            deadline = time.monotonic() + (5 if expected else 1.5)
            while time.monotonic() < deadline and not (expected and calls.exists()):
                time.sleep(0.1)
        finally:
            os.close(controller)
            os.close(terminal)
        recorded = calls.read_text() if calls.exists() else ""
        return completed.returncode, recorded, tty_name

    return run


def test_a_clean_quit_closes_its_terminal_app_window(desktop):
    status, asked, tty_name = desktop(exit_status=0, term_program="Apple_Terminal")

    assert status == 0
    # The window is found by this session's tty, never "the front window".
    assert asked.strip() == f"- {tty_name}"


def test_a_failed_launch_keeps_the_window_so_the_error_can_be_read(desktop):
    status, asked, _ = desktop(exit_status=3, term_program="Apple_Terminal")

    assert status == 3
    assert asked == ""


def test_other_terminals_are_left_to_close_their_own_window(desktop):
    status, asked, _ = desktop(exit_status=0, term_program="kitty")

    assert status == 0
    assert asked == ""


def test_only_a_window_holding_just_this_session_is_closed():
    script = (PACKAGING / "Opengrader.command").read_text(encoding="utf-8")

    assert "(count of tabs of w) is 1 and (tty of tab 1 of w) is target" in script


def test_installer_ships_the_entry_point_and_kitty_quits_with_its_window():
    installer = (PACKAGING / "install.sh").read_text(encoding="utf-8")

    assert '"$SRC/packaging/Opengrader.command" "$ROOT/Opengrader.command"' in installer
    assert "--args -o macos_quit_when_last_window_closed=yes" in installer


def test_the_terminal_command_never_closes_a_window():
    # `opengrader` typed in someone's own terminal must leave that terminal alone.
    for name in ("opengrader", "bootstrap.sh"):
        text = (PACKAGING / name).read_text(encoding="utf-8")
        assert "osascript" not in text, name
        assert "macos_quit_when_last_window_closed" not in text, name
