"""Ask the terminal what it can draw, in one round trip.

When the terminal cannot be recognised from its environment — usual over
SSH, which passes on few of its variables — the app has to ask it: can you
draw Sixel images, Kitty graphics, and how many pixels is a character cell?
`textual_image` asks those one at a time and gives each answer 0.1 s, which
a terminal at the far end of a network connection can miss: the app then
settles for blocky previews, and the late answer turns up as stray
characters once the app is running.

Here the questions go out together, followed by the one every terminal
answers (DA1, "who are you?"). Terminals answer in the order asked, so once
DA1's answer is in, every answer is in: locally that is a millisecond, over
SSH one network round trip, and nobody waits a fixed delay. The timeout only
applies to a terminal that never answers at all, so it can be generous —
longer over SSH — and `OPENGRADER_TERMINAL_TIMEOUT` sets it outright.
"""

from __future__ import annotations

import os
import re
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass

ENV_TIMEOUT = "OPENGRADER_TERMINAL_TIMEOUT"
LOCAL_TIMEOUT = 0.5
SSH_TIMEOUT = 2.0

# Kitty graphics query (a 1x1 image, query only), cell size in pixels, DA1.
QUERY = b"\x1b_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA\x1b\\" + b"\x1b[16t" + b"\x1b[c"
_KITTY_OK = re.compile(rb"\x1b_Gi=31;OK")
_CELL = re.compile(rb"\x1b\[6;(\d+);(\d+)t")
_DA1 = re.compile(rb"\x1b\[\?([0-9;]*)c")


@dataclass(frozen=True)
class TerminalAnswers:
    sixel: bool
    kitty: bool
    # A character cell in pixels (width, height), when the terminal said.
    cell_size: tuple[int, int] | None
    # The terminal answered DA1, so the other answers are complete.
    answered: bool
    seconds: float
    timeout: float
    over_ssh: bool


def over_ssh(environ: Mapping[str, str]) -> bool:
    return any(environ.get(name) for name in ("SSH_CONNECTION", "SSH_TTY", "SSH_CLIENT"))


def query_timeout(environ: Mapping[str, str]) -> float:
    """How long to wait for a terminal that never answers."""
    raw = environ.get(ENV_TIMEOUT, "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            value = 0.0
        if value > 0:
            return value
    return SSH_TIMEOUT if over_ssh(environ) else LOCAL_TIMEOUT


def parse_answers(
    received: bytes, *, seconds: float, timeout: float, over_ssh: bool
) -> TerminalAnswers:
    da1 = _DA1.search(received)
    cell = _CELL.search(received)
    return TerminalAnswers(
        sixel=bool(da1) and "4" in da1.group(1).decode().split(";"),
        kitty=bool(_KITTY_OK.search(received)),
        cell_size=(int(cell.group(2)), int(cell.group(1))) if cell else None,
        answered=bool(da1),
        seconds=seconds,
        timeout=timeout,
        over_ssh=over_ssh,
    )


def ask_terminal(environ: Mapping[str, str] | None = None) -> TerminalAnswers | None:
    """Ask once; None where it cannot be asked (no terminal on both stdin and
    stdout, or a system without POSIX terminals — Windows keeps the
    library's own way)."""
    environ = os.environ if environ is None else environ
    try:
        import select
        import termios
        import tty
    except ImportError:
        return None
    stdin, stdout = sys.__stdin__, sys.__stdout__
    if not stdin or not stdout or not stdin.isatty() or not stdout.isatty():
        return None
    timeout = query_timeout(environ)
    fd = stdin.fileno()
    try:
        saved = termios.tcgetattr(fd)
    except termios.error:
        return None
    received = b""
    started = time.monotonic()
    try:
        # No echo and no line buffering, so the answers can be read as they
        # come without showing up on screen.
        tty.setcbreak(fd)
        os.write(stdout.fileno(), QUERY)
        deadline = started + timeout
        while not _DA1.search(received):
            left = deadline - time.monotonic()
            if left <= 0:
                break
            ready, _, _ = select.select([fd], [], [], left)
            if not ready:
                break
            chunk = os.read(fd, 1024)
            if not chunk:
                break
            received += chunk
    except OSError:
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    return parse_answers(
        received,
        seconds=time.monotonic() - started,
        timeout=timeout,
        over_ssh=over_ssh(environ),
    )


def system_cell_size() -> tuple[int, int] | None:
    """A cell's size from the window size the system reports, without
    asking the terminal (often missing over SSH)."""
    try:
        import fcntl
        import struct
        import termios
    except ImportError:
        return None
    stdout = sys.__stdout__
    if not stdout or not stdout.isatty():
        return None
    try:
        rows, columns, width, height = struct.unpack(
            "HHHH", fcntl.ioctl(stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
        )
    except OSError:
        return None
    if not (rows and columns and width and height):
        return None
    return width // columns, height // rows
