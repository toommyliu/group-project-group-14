"""Putting text on the system clipboard from the terminal app.

Textual's own `App.copy_to_clipboard` asks the *terminal* to do it (an
"OSC 52" escape sequence). Several terminals ignore that request — Apple's
Terminal, which is what the desktop icon opens on a Mac, is one — and the app
cannot tell whether it worked. So the operating system's own clipboard tool is
tried first, and the terminal request is sent as well: it is harmless where it
is ignored, and it is the only thing that works over SSH.
"""

from __future__ import annotations

import os
import sys
from typing import Any

__all__ = ["copy_text"]

COPY_TIMEOUT_SECONDS = 5.0


def _native_commands() -> list[tuple[list[str], str]]:
    """Clipboard tools to try, in order, each with the encoding it reads on stdin."""
    if sys.platform == "darwin":
        return [(["/usr/bin/pbcopy"], "utf-8")]
    if os.name == "nt":
        # clip.exe reads the console code page unless the text starts with a
        # UTF-16 byte-order mark, which Python's "utf-16" codec writes.
        return [(["clip"], "utf-16")]
    commands: list[tuple[list[str], str]] = []
    if os.environ.get("WAYLAND_DISPLAY"):
        commands.append((["wl-copy"], "utf-8"))
    if os.environ.get("DISPLAY"):
        commands.append((["xclip", "-selection", "clipboard"], "utf-8"))
        commands.append((["xsel", "--clipboard", "--input"], "utf-8"))
    return commands


def _copy_natively(text: str) -> bool:
    import subprocess

    for argv, encoding in _native_commands():
        try:
            # Output is discarded rather than captured: xclip stays running
            # in the background to serve the clipboard, and a captured pipe
            # would keep this call waiting on it.
            proc = subprocess.run(
                argv,
                input=text.encode(encoding),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=COPY_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            return True
    return False


def copy_text(app: Any, text: str) -> bool:
    """Copy `text`. True when the system's clipboard tool confirmed it.

    False does not mean it failed — only that nothing confirmed it: the
    terminal was asked too and may have done it. A caller that must be sure
    should offer another way to get the text when this returns False.
    """
    confirmed = _copy_natively(text)
    try:
        app.copy_to_clipboard(text)
    except Exception:  # noqa: BLE001 - a clipboard is never worth a crash
        pass
    return confirmed
