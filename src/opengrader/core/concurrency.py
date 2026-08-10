"""Optimistic concurrency guards for Opengrader's file-based state.

Opengrader keeps all grading truth in files (`.submission.md`, the QA file)
with no database or lock server. That is fine for a single grader, but when
two people (or two windows) point at the same directory, a naive
read-modify-write silently overwrites whichever save landed first.

These helpers add *optimistic* concurrency control: a writer records a file's
modification time when it reads the file, and re-checks that timestamp just
before writing. If the file changed underneath it, the write is refused with
``ConcurrentModificationError`` instead of clobbering the other person's work.
This does not lock the file — it narrows the race to the sub-millisecond gap
between check and write and turns the realistic "someone saved a minute ago"
case into a clear, recoverable error.
"""

from __future__ import annotations

from pathlib import Path


class ConcurrentModificationError(RuntimeError):
    """Raised when a file changed on disk since Opengrader last read it."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        super().__init__(
            f"{self.path.name} was changed on disk by another program since it "
            "was loaded. Reload the submissions before saving so you don't "
            "overwrite the other changes."
        )


def file_mtime_ns(path: str | Path) -> int | None:
    """Return a file's modification time in nanoseconds, or None if missing."""
    try:
        return Path(path).stat().st_mtime_ns
    except FileNotFoundError:
        return None


def check_unmodified(path: str | Path, expected_mtime_ns: int | None) -> None:
    """Raise if the file's mtime no longer matches the expected value.

    ``expected_mtime_ns`` of ``None`` opts out of the check (used by callers
    like the CLI batch tools that own the files exclusively), so existing
    single-writer flows are unaffected.
    """
    if expected_mtime_ns is None:
        return
    current = file_mtime_ns(path)
    if current != expected_mtime_ns:
        raise ConcurrentModificationError(path)
