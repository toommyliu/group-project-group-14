import os
from pathlib import Path

import pytest

from opengrader.core.concurrency import (
    ConcurrentModificationError,
    check_unmodified,
    file_mtime_ns,
)


def test_file_mtime_ns_returns_none_for_missing_file(tmp_path: Path) -> None:
    assert file_mtime_ns(tmp_path / "nope.txt") is None


def test_check_unmodified_passes_for_matching_mtime(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("x")
    mtime = file_mtime_ns(path)

    check_unmodified(path, mtime)  # must not raise


def test_check_unmodified_opts_out_when_expected_is_none(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("x")

    check_unmodified(path, None)  # None disables the guard


def test_check_unmodified_raises_when_file_changed(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("x")
    stale = file_mtime_ns(path)
    assert stale is not None

    # Simulate another writer touching the file after we recorded its mtime.
    os.utime(path, ns=(stale + 1_000_000, stale + 1_000_000))

    with pytest.raises(ConcurrentModificationError):
        check_unmodified(path, stale)


def test_check_unmodified_raises_when_file_deleted(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("x")
    stale = file_mtime_ns(path)
    path.unlink()

    with pytest.raises(ConcurrentModificationError):
        check_unmodified(path, stale)
