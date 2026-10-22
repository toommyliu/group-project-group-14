"""Asking the terminal what it draws in one round trip, so a slow (SSH)
terminal is waited for exactly as long as it takes to answer."""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

from opengrader.tui import terminal_query
from opengrader.tui.terminal_graphics import detect_graphics_capability
from opengrader.tui.terminal_query import (
    ENV_TIMEOUT,
    LOCAL_TIMEOUT,
    SSH_TIMEOUT,
    TerminalAnswers,
    ask_terminal,
    parse_answers,
    query_timeout,
)

KITTY_ANSWER = b"\x1b_Gi=31;OK\x1b\\"
CELL_ANSWER = b"\x1b[6;18;9t"
DA1_SIXEL = b"\x1b[?62;4;22c"
DA1_PLAIN = b"\x1b[?62;22c"


def test_the_wait_is_longer_over_ssh_and_can_be_set():
    assert query_timeout({}) == LOCAL_TIMEOUT
    assert query_timeout({"SSH_CONNECTION": "1.2.3.4 22 5.6.7.8 22"}) == SSH_TIMEOUT
    assert query_timeout({ENV_TIMEOUT: "3.5", "SSH_TTY": "/dev/pts/1"}) == 3.5
    assert query_timeout({ENV_TIMEOUT: "nonsense"}) == LOCAL_TIMEOUT


def test_answers_are_read_from_one_reply():
    kitty = parse_answers(KITTY_ANSWER + CELL_ANSWER + DA1_PLAIN, seconds=0.01, timeout=1, over_ssh=False)
    assert (kitty.kitty, kitty.sixel, kitty.cell_size, kitty.answered) == (True, False, (9, 18), True)

    sixel = parse_answers(DA1_SIXEL, seconds=0.01, timeout=1, over_ssh=False)
    assert (sixel.kitty, sixel.sixel, sixel.cell_size) == (False, True, None)

    silent = parse_answers(b"", seconds=1, timeout=1, over_ssh=True)
    assert not silent.answered


def _answers(**changes) -> TerminalAnswers:
    base = dict(sixel=False, kitty=False, cell_size=None, answered=True, seconds=0.31, timeout=2.0, over_ssh=True)
    return TerminalAnswers(**{**base, **changes})


def test_detection_uses_the_answers_and_says_how_long_they_took():
    capability = detect_graphics_capability(environ={}, is_tty=True, query=lambda: _answers(kitty=True))

    assert capability.protocol == "kitty" and capability.source == "query"
    assert capability.seconds == pytest.approx(0.31)
    assert "over SSH in 0.31 s" in capability.detail


def test_sixel_wins_over_kitty_as_the_library_chooses():
    capability = detect_graphics_capability(environ={}, is_tty=True, query=lambda: _answers(kitty=True, sixel=True))

    assert capability.protocol == "sixel"


def test_a_terminal_that_never_answers_says_how_to_skip_or_wait_longer():
    capability = detect_graphics_capability(environ={}, is_tty=True, query=lambda: _answers(answered=False, seconds=2.0))

    assert capability.protocol == "halfcell"
    assert "did not answer within 2 s over SSH" in capability.detail
    assert "OPENGRADER_GRAPHICS" in capability.detail and ENV_TIMEOUT in capability.detail


def test_naming_the_protocol_skips_asking_altogether():
    asked: list[bool] = []

    capability = detect_graphics_capability(
        environ={"OPENGRADER_GRAPHICS": "kitty"}, is_tty=True, query=lambda: asked.append(True)
    )

    assert capability.protocol == "kitty" and asked == []


@pytest.mark.skipif(os.name == "nt", reason="needs a POSIX pseudo-terminal")
def test_a_terminal_that_answers_late_is_still_heard(monkeypatch):
    import pty

    from textual_image._terminal import get_cell_size, probe_terminal

    original_capabilities = probe_terminal()
    master, slave = pty.openpty()
    stdin = os.fdopen(slave, "r", closefd=False)
    stdout = os.fdopen(os.dup(slave), "w")
    monkeypatch.setattr(sys, "__stdin__", stdin)
    monkeypatch.setattr(sys, "__stdout__", stdout)
    asked = bytearray()

    def far_terminal() -> None:
        deadline = time.monotonic() + 3
        while not asked.endswith(b"\x1b[c") and time.monotonic() < deadline:
            asked.extend(os.read(master, 1024))
        time.sleep(0.3)
        os.write(master, KITTY_ANSWER + CELL_ANSWER + DA1_PLAIN)

    thread = threading.Thread(target=far_terminal, daemon=True)
    thread.start()
    try:
        answers = ask_terminal({"SSH_CONNECTION": "x"})
        capability = detect_graphics_capability(
            environ={}, is_tty=True, query=lambda: answers
        )
        image_cell_size = get_cell_size()
    finally:
        setattr(probe_terminal, "_result", original_capabilities)
        thread.join(timeout=5)
        stdout.close()
        stdin.close()
        os.close(master)
        os.close(slave)

    assert answers is not None and answers.answered
    assert answers.kitty and answers.cell_size == (9, 18)
    assert capability.protocol == "kitty"
    assert image_cell_size == (9, 18)
    # Waited as long as the answer took, not the whole 2 s allowed over SSH.
    assert 0.25 < answers.seconds < 1.5
    assert bytes(asked) == terminal_query.QUERY
