from __future__ import annotations

from pathlib import Path


class ConcurrentModificationError(RuntimeError):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        super().__init__(
            f"{self.path.name} was changed on disk by another program since it "
            "was loaded. Reload the submissions before saving so you don't "
            "overwrite the other changes."
        )


def file_mtime_ns(path: str | Path) -> int | None:
    try:
        return Path(path).stat().st_mtime_ns
    except FileNotFoundError:
        return None


def check_unmodified(path: str | Path, expected_mtime_ns: int | None) -> None:
    if expected_mtime_ns is None:
        return
    current = file_mtime_ns(path)
    if current != expected_mtime_ns:
        raise ConcurrentModificationError(path)
