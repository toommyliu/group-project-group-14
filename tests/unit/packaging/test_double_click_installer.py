"""The double-click installers at the top of the checkout.

They exist so someone who has never opened a terminal can install the desktop
app: `Install Opengrader (Mac).command` and `Install Opengrader (Windows).bat`
only find the folder they sit in and hand over to `packaging/install.*`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MAC_INSTALLER = "Install Opengrader (Mac).command"
WINDOWS_INSTALLER = "Install Opengrader (Windows).bat"

mac_only = pytest.mark.skipif(
    sys.platform == "win32", reason="the .command installer is a bash script"
)


def _unzipped(tmp_path: Path, *, install_script: str | None) -> Path:
    """A stand-in for the downloaded folder, in a path with a space in it."""
    folder = tmp_path / "Downloads" / "group project main"
    (folder / "packaging").mkdir(parents=True)
    shutil.copy(REPO_ROOT / MAC_INSTALLER, folder / MAC_INSTALLER)
    if install_script is not None:
        (folder / "packaging" / "install.sh").write_text(install_script)
    return folder


def _run(folder: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(folder / MAC_INSTALLER), *args],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )


@mac_only
def test_it_runs_the_installer_beside_it_from_any_working_directory(
    tmp_path: Path,
) -> None:
    record = tmp_path / "ran"
    folder = _unzipped(
        tmp_path,
        install_script=f'printf \'%s|%s\\n\' "$0" "$*" > "{record}"\n',
    )

    # Finder starts a .command in the home folder, not beside the file.
    completed = _run(folder, "--build-web", cwd=tmp_path)

    assert completed.returncode == 0
    script, arguments = record.read_text().strip().split("|")
    assert Path(script).resolve() == (folder / "packaging" / "install.sh").resolve()
    assert arguments == "--build-web"
    assert "was not installed" not in completed.stdout


@mac_only
def test_a_failed_install_says_so_and_keeps_its_exit_status(tmp_path: Path) -> None:
    folder = _unzipped(tmp_path, install_script="echo 'error: no space left'\nexit 3\n")

    completed = _run(folder, cwd=tmp_path)

    assert completed.returncode == 3
    assert "error: no space left" in completed.stdout
    assert "Opengrader was not installed" in completed.stdout


@mac_only
def test_moved_out_of_its_folder_it_explains_instead_of_failing_blankly(
    tmp_path: Path,
) -> None:
    folder = _unzipped(tmp_path, install_script=None)

    completed = _run(folder, cwd=tmp_path)

    assert completed.returncode == 1
    assert "Keep it inside the Opengrader folder" in completed.stdout


def test_the_mac_installer_is_executable_in_git() -> None:
    # A .command without the executable bit cannot be double-clicked, and the
    # bit comes from git: a ZIP download has whatever mode the index records.
    # (This checkout may sit on a drive that reports every file as executable.)
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    listed = subprocess.run(
        ["git", "ls-files", "-s", "--", MAC_INSTALLER],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if not listed:
        pytest.skip("not added to git yet")
    assert listed.split()[0] == "100755"


def test_the_windows_installer_hands_over_to_the_real_one() -> None:
    raw = (REPO_ROOT / WINDOWS_INSTALLER).read_bytes()
    text = raw.decode("ascii")

    # cmd.exe misreads batch files with bare LF line endings.
    assert raw.count(b"\n") == raw.count(b"\r\n")
    assert r'-File "%~dp0packaging\install.ps1" %*' in text
    assert (REPO_ROOT / "packaging" / "install.ps1").is_file()
    assert "-ExecutionPolicy Bypass" in text
