"""Installing what Opengrader needs from inside the app (macOS, through Homebrew).

`core.environment` only looks; this is the one place that installs a
program, and only when asked to: the Doctor screen's Install Tesseract
button, the browser app's Install Node.js and pnpm (`core/browser_app`) and
AI setup's Set up local AI (`core/app_config/local_ai`, for Ollama). Homebrew
itself is never installed from here: its installer asks for an administrator
password in a terminal.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Callable, Sequence

HOMEBREW_URL = "https://brew.sh"

# Where Homebrew lives when a desktop launcher starts with a bare PATH:
# Apple silicon, then Intel.
_HOMEBREW_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


class HomebrewError(RuntimeError):
    """The install failed; `tail` holds the last lines of Homebrew's output."""

    def __init__(self, message: str, tail: str = "") -> None:
        super().__init__(message)
        self.tail = tail


def find_homebrew() -> str | None:
    if sys.platform != "darwin":
        return None
    return shutil.which("brew") or shutil.which(
        "brew", path=os.pathsep.join(_HOMEBREW_DIRS)
    )


def brew_install(
    brew: str,
    formulae: Sequence[str],
    *,
    on_line: Callable[[str], None] | None = None,
    on_start: Callable[[subprocess.Popen[str]], None] | None = None,
) -> None:
    """Run `brew install <formulae>`, reporting each line of its output.

    `on_start` receives the running process, so a caller that goes away can
    stop it. Raises `HomebrewError` when it cannot start or fails.
    """
    argv = [brew, "install", *formulae]
    try:
        process = subprocess.Popen(
            argv,
            # No TTY here: never stop for a question nobody can answer.
            env={**os.environ, "NONINTERACTIVE": "1", "HOMEBREW_NO_ENV_HINTS": "1"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise HomebrewError(f"Homebrew could not start: {exc}") from exc
    if on_start:
        on_start(process)
    tail: deque[str] = deque(maxlen=12)
    assert process.stdout is not None
    for line in process.stdout:
        text = line.rstrip()
        if text:
            tail.append(text)
            if on_line:
                on_line(text)
    if process.wait() != 0:
        raise HomebrewError(
            f"`{' '.join(argv)}` failed (exit {process.returncode}).",
            "\n".join(tail),
        )
    _put_on_path(Path(brew).parent)


def _put_on_path(directory: Path) -> None:
    # Homebrew found off PATH installs off PATH too; without this the app
    # that just installed something would still not see it.
    entries = os.environ.get("PATH", "").split(os.pathsep)
    if str(directory) not in entries:
        os.environ["PATH"] = os.pathsep.join([*filter(None, entries), str(directory)])
