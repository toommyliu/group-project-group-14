"""Token usage of the most recent model call on this thread.

A client that can read token counts off a reply calls `record_last`; the
wrapper that knows *what kind of thing* was sent (an answer image, a graded
response) calls `take_last` right after and writes both into the egress log.
Thread-local because grading fans calls out over a thread pool, and one call's
usage must never be attributed to another's.
"""

from __future__ import annotations

import threading

__all__ = ["record_last", "take_last"]

_local = threading.local()


def record_last(input_tokens: int | None, output_tokens: int | None) -> None:
    _local.last = (input_tokens, output_tokens)


def take_last() -> tuple[int | None, int | None]:
    """Return and clear this thread's last recorded usage."""
    last = getattr(_local, "last", (None, None))
    _local.last = (None, None)
    return last
