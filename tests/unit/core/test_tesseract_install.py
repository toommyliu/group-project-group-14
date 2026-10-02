from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from opengrader.core import tesseract_install
from opengrader.core.tesseract_install import (
    TesseractInstallError,
    find_homebrew,
    install_tesseract,
)

needs_shell = pytest.mark.skipif(
    sys.platform == "win32", reason="the stand-in brew is a shell script"
)


def _fake_brew(directory: Path, body: str) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    brew = directory / "brew"
    brew.write_text(f"#!/bin/bash\n{body}\n")
    brew.chmod(0o755)
    return str(brew)


def test_homebrew_is_only_looked_for_on_a_mac(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tesseract_install.sys, "platform", "linux")
    monkeypatch.setattr(
        tesseract_install.shutil, "which", lambda *args, **kwargs: "/usr/bin/brew"
    )

    assert find_homebrew() is None


def test_homebrew_on_path_is_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tesseract_install.sys, "platform", "darwin")
    monkeypatch.setattr(
        tesseract_install.shutil,
        "which",
        lambda name, path=None: "/somewhere/brew" if path is None else None,
    )

    assert find_homebrew() == "/somewhere/brew"


@needs_shell
def test_homebrew_off_path_is_found_where_it_usually_lives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    brew = _fake_brew(tmp_path / "homebrew" / "bin", "exit 0")
    monkeypatch.setattr(tesseract_install.sys, "platform", "darwin")
    monkeypatch.setattr(
        tesseract_install, "_HOMEBREW_DIRS", (str(tmp_path / "homebrew" / "bin"),)
    )
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    assert find_homebrew() == brew


def test_no_homebrew_anywhere_is_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tesseract_install.sys, "platform", "darwin")
    monkeypatch.setattr(tesseract_install, "_HOMEBREW_DIRS", (str(tmp_path),))
    monkeypatch.setenv("PATH", str(tmp_path))

    assert find_homebrew() is None


@needs_shell
def test_install_runs_brew_install_tesseract_and_reports_its_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = tmp_path / "argv"
    brew = _fake_brew(
        tmp_path / "bin",
        f'printf \'%s\\n\' "$*" > "{record}"\necho "==> Pouring tesseract"\necho\necho done',
    )
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    lines: list[str] = []
    started: list[object] = []

    install_tesseract(brew, on_line=lines.append, on_start=started.append)

    assert record.read_text().strip() == "install tesseract"
    assert lines == ["==> Pouring tesseract", "done"]
    assert len(started) == 1


@needs_shell
def test_a_homebrew_found_off_path_is_put_on_path_after_installing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    brew = _fake_brew(tmp_path / "bin", "exit 0")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    install_tesseract(brew)

    assert os.environ["PATH"].split(os.pathsep) == [
        "/usr/bin",
        "/bin",
        str(tmp_path / "bin"),
    ]


@needs_shell
def test_a_failed_install_raises_with_the_last_output_and_leaves_path_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    brew = _fake_brew(tmp_path / "bin", 'echo "Error: no bottle available"\nexit 1')
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    with pytest.raises(TesseractInstallError) as caught:
        install_tesseract(brew)

    assert "exit 1" in str(caught.value)
    assert caught.value.tail == "Error: no bottle available"
    assert os.environ["PATH"] == "/usr/bin:/bin"


def test_a_brew_that_cannot_start_is_reported_not_raised_raw(tmp_path: Path) -> None:
    with pytest.raises(TesseractInstallError, match="could not start"):
        install_tesseract(str(tmp_path / "missing" / "brew"))
