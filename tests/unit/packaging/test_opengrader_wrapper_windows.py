"""`packaging/opengrader.ps1` — the Windows launcher, exercised under pwsh.

The PowerShell launcher is the twin of `packaging/opengrader`, and the logic
worth testing is the part that is *not* Windows-specific: the order it resolves
a checkout in, that it refuses a wrong `$env:OPENGRADER_HOME` instead of
silently searching past it, that it recognises the project by the name in
pyproject.toml rather than by folder name, and that a missing uv is not fatal.
All of that is plain PowerShell and runs identically under pwsh on Linux and
macOS, which is what lets these tests run in CI at all — GitHub's
`ubuntu-latest` image ships pwsh, and the workflow's path filter includes
`packaging/opengrader*` so a change to the script triggers them.

**What these tests deliberately do not cover**: the runner rules that depend on
Windows layout — `Scripts\\python.exe` inside a venv, `Lib\\site-packages`,
`$env:CONDA_PREFIX\\python.exe`. On a non-Windows host `Join-Path` does not
treat a backslash as a separator, so those paths cannot resolve and the rules
are unreachable here. Rule 1 (`$env:OPENGRADER_PYTHON`) is the exception: it
takes a path as given, so the "an interpreter you name yourself" path *is*
covered below. Anything else in job 2 needs a real Windows machine, and the
POSIX twin's suite (`test_opengrader_wrapper.py`) is what pins the shared
design those rules mirror.

Every test pins `$env:OPENGRADER_SEARCH_ROOTS`. Without it, the search falls
back to enumerating drive roots, which on a Unix host means `/` — a filesystem
walk no test should ever start.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LAUNCHER_SOURCE = REPO_ROOT / "packaging" / "opengrader.ps1"

PWSH = shutil.which("pwsh") or shutil.which("powershell")

pytestmark = pytest.mark.skipif(
    PWSH is None,
    reason="no PowerShell on this machine; these run in CI, where ubuntu-latest ships pwsh",
)

# A path component with a space: the real checkout has one, and quoting is
# exactly where a shell or PowerShell script goes wrong.
CHECKOUT_DIRNAME = "Samsung T5 1"


def _make_checkout(root: Path, name: str = "group-project-group-14") -> Path:
    checkout = root / CHECKOUT_DIRNAME / "CMPE 195" / name
    (checkout / "src" / "opengrader").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text(
        '[project]\nname = "opengrader"\nversion = "1.0"\n', encoding="utf-8"
    )
    return checkout


def _make_decoy(root: Path, name: str) -> Path:
    """Another Python project — a pyproject.toml that is *not* Opengrader."""
    decoy = root / CHECKOUT_DIRNAME / name
    decoy.mkdir(parents=True)
    (decoy / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1"\n', encoding="utf-8"
    )
    return decoy


@pytest.fixture
def launcher(tmp_path: Path) -> Path:
    """The launcher, copied somewhere that is *not* inside a checkout.

    This mirrors the installed state and disables the launcher's rule 2 ("the
    checkout this script lives in"), so the rules below it are the ones under
    test.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    target = bin_dir / "opengrader.ps1"
    shutil.copy(LAUNCHER_SOURCE, target)
    return target


@pytest.fixture
def stub_uv(tmp_path: Path) -> Path:
    """A fake `uv` that reports the command line it was handed, then succeeds."""
    stub_dir = tmp_path / "stub-bin"
    stub_dir.mkdir()
    uv = stub_dir / "uv"
    uv.write_text('#!/bin/sh\nprintf \'UV-ARGS: %s\\n\' "$*"\nexit 0\n', encoding="utf-8")
    uv.chmod(0o755)
    return stub_dir


@pytest.fixture
def sandbox(tmp_path: Path) -> dict[str, Path]:
    home = tmp_path / "home"
    cache = tmp_path / "cache"
    home.mkdir()
    cache.mkdir()
    return {"home": home, "cache": cache}


def _run(
    launcher: Path,
    *,
    cwd: Path,
    home: Path,
    cache: Path,
    env_extra: dict[str, str] | None = None,
    stub_uv_dir: Path | None = None,
    search_roots: str | None = None,
) -> subprocess.CompletedProcess[str]:
    assert PWSH is not None
    env = {
        "PATH": os.pathsep.join(
            ([str(stub_uv_dir)] if stub_uv_dir else []) + ["/usr/bin", "/bin", "/usr/local/bin"]
        ),
        "HOME": str(home),
        # The launcher prefers LOCALAPPDATA for its remembered path; setting it
        # keeps the cache inside tmp_path on any host.
        "LOCALAPPDATA": str(cache),
        # Never let a test fall through to enumerating real drive roots.
        "OPENGRADER_SEARCH_ROOTS": search_roots or str(home / "no-such-root"),
    }
    env.update(env_extra or {})
    return subprocess.run(
        [PWSH, "-NoProfile", "-File", str(launcher), "doctor", "--json"],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _chosen_project(result: subprocess.CompletedProcess[str]) -> str:
    """The checkout the launcher passed to `uv run --project`."""
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    line = next((ln for ln in result.stdout.splitlines() if ln.startswith("UV-ARGS: ")), None)
    assert line is not None, f"stub uv was never reached: {result.stdout!r}"
    args = line[len("UV-ARGS: ") :]
    assert args.startswith("run --project "), args
    assert args.endswith(" opengrader doctor --json"), args
    return args[len("run --project ") : -len(" opengrader doctor --json")]


def test_the_script_parses_and_runs_at_all(launcher, stub_uv, sandbox, tmp_path) -> None:
    """A syntax error in PowerShell is only ever found by running it.

    This file's real job: the launcher is authored on a machine with no
    PowerShell, so the first thing worth asserting is that pwsh can parse and
    execute it end to end.
    """
    checkout = _make_checkout(tmp_path / "pinned")
    result = _run(
        launcher,
        cwd=tmp_path,
        env_extra={"OPENGRADER_HOME": str(checkout)},
        stub_uv_dir=stub_uv,
        **sandbox,
    )
    assert result.returncode == 0, f"stderr={result.stderr!r}"
    assert "ParserError" not in result.stderr
    assert "is not recognized" not in result.stderr


def test_explicit_home_wins(launcher, stub_uv, sandbox, tmp_path) -> None:
    """`$env:OPENGRADER_HOME` beats every other rule, including the current dir."""
    pinned = _make_checkout(tmp_path / "pinned")
    standing_in = _make_checkout(tmp_path / "elsewhere")

    result = _run(
        launcher,
        cwd=standing_in,
        env_extra={"OPENGRADER_HOME": str(pinned)},
        stub_uv_dir=stub_uv,
        **sandbox,
    )
    assert _chosen_project(result) == str(pinned)


def test_explicit_home_that_is_wrong_is_an_error(launcher, stub_uv, sandbox, tmp_path) -> None:
    """A bad `$env:OPENGRADER_HOME` must be an error, not a silent fallback."""
    not_a_checkout = tmp_path / "just some folder"
    not_a_checkout.mkdir()

    result = _run(
        launcher,
        cwd=tmp_path,
        env_extra={"OPENGRADER_HOME": str(not_a_checkout)},
        stub_uv_dir=stub_uv,
        **sandbox,
    )
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "OPENGRADER_HOME" in combined
    assert "could not find an Opengrader checkout" not in combined


def test_the_checkout_you_are_standing_in_is_the_one_that_runs(
    launcher, stub_uv, sandbox, tmp_path
) -> None:
    """Deep inside a checkout, that checkout wins over a remembered other one."""
    remembered = _make_checkout(tmp_path / "remembered")
    standing_in = _make_checkout(tmp_path / "current", name="another-clone")
    cache_file = sandbox["cache"] / "opengrader" / "checkout-path"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text(f"{remembered}\n", encoding="utf-8")

    result = _run(
        launcher,
        cwd=standing_in / "src" / "opengrader",
        stub_uv_dir=stub_uv,
        **sandbox,
    )
    assert _chosen_project(result) == str(standing_in)


def test_remembered_path_is_used_when_standing_somewhere_unrelated(
    launcher, stub_uv, sandbox, tmp_path
) -> None:
    """The fast path: outside any checkout, use the one from last time."""
    remembered = _make_checkout(tmp_path / "remembered")
    cache_file = sandbox["cache"] / "opengrader" / "checkout-path"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text(f"{remembered}\n", encoding="utf-8")

    outside = tmp_path / "somewhere else"
    outside.mkdir()
    result = _run(launcher, cwd=outside, stub_uv_dir=stub_uv, **sandbox)
    assert _chosen_project(result) == str(remembered)


def test_a_stale_remembered_path_is_dropped_and_the_search_takes_over(
    launcher, stub_uv, sandbox, tmp_path
) -> None:
    """The case the whole design exists for: the path changed underneath us.

    On Windows that is a removable drive coming back as a different letter.
    """
    moved_to = _make_checkout(tmp_path / "new location")
    cache_file = sandbox["cache"] / "opengrader" / "checkout-path"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text(f"{tmp_path / 'old location' / 'gone'}\n", encoding="utf-8")

    outside = tmp_path / "somewhere else"
    outside.mkdir()
    result = _run(
        launcher,
        cwd=outside,
        search_roots=str(tmp_path / "new location"),
        stub_uv_dir=stub_uv,
        **sandbox,
    )
    assert _chosen_project(result) == str(moved_to)
    assert cache_file.read_text(encoding="utf-8").strip() == str(moved_to)


def test_a_folder_of_other_python_projects_is_not_a_checkout(
    launcher, stub_uv, sandbox, tmp_path
) -> None:
    """Decoys alone must find nothing — the marker is the *name*, not the file.

    Asserted with no real checkout present, so there is no lucky answer a
    launcher that accepted any pyproject.toml could stumble onto.
    """
    root = tmp_path / "drive"
    _make_decoy(root, "invoice-generator")
    _make_decoy(root, "honeypot")

    outside = tmp_path / "somewhere else"
    outside.mkdir()
    result = _run(
        launcher,
        cwd=outside,
        search_roots=str(root),
        stub_uv_dir=stub_uv,
        **sandbox,
    )
    assert result.returncode == 1
    assert "could not find an Opengrader checkout" in (result.stdout + result.stderr)


def test_the_search_ignores_other_python_projects(
    launcher, stub_uv, sandbox, tmp_path
) -> None:
    """Only a pyproject.toml naming *this* project counts as a checkout."""
    root = tmp_path / "drive"
    # Named to sort ahead of the real checkout, so a launcher that took the
    # first pyproject.toml it met would take one of these.
    _make_decoy(root, "aaa-invoice-generator")
    _make_decoy(root, "aab-honeypot")
    real = _make_checkout(root)

    outside = tmp_path / "somewhere else"
    outside.mkdir()
    result = _run(
        launcher,
        cwd=outside,
        search_roots=str(root),
        stub_uv_dir=stub_uv,
        **sandbox,
    )
    assert _chosen_project(result) == str(real)


def test_two_checkouts_are_reported_rather_than_silently_picked(
    launcher, stub_uv, sandbox, tmp_path
) -> None:
    """Running the wrong clone is expensive and silent; say something.

    Asserted on stderr specifically: this launcher forwards a CLI whose stdout
    may be JSON, so a diagnostic on stdout would corrupt a caller piping into
    ConvertFrom-Json.
    """
    root = tmp_path / "drive"
    _make_checkout(root, name="clone-a")
    _make_checkout(root, name="clone-b")

    outside = tmp_path / "somewhere else"
    outside.mkdir()
    result = _run(
        launcher,
        cwd=outside,
        search_roots=str(root),
        stub_uv_dir=stub_uv,
        **sandbox,
    )
    assert result.returncode == 0, result.stderr
    assert "found more than one checkout" in result.stderr
    assert "OPENGRADER_HOME" in result.stderr
    assert "found more than one checkout" not in result.stdout


def test_diagnostics_stay_off_stdout(launcher, stub_uv, sandbox, tmp_path) -> None:
    """Debug output must not pollute the forwarded command's stdout."""
    checkout = _make_checkout(tmp_path / "pinned")
    result = _run(
        launcher,
        cwd=tmp_path,
        env_extra={"OPENGRADER_HOME": str(checkout), "OPENGRADER_WRAPPER_DEBUG": "1"},
        stub_uv_dir=stub_uv,
        **sandbox,
    )
    assert result.returncode == 0, result.stderr
    assert "opengrader[wrapper]" in result.stderr
    assert "opengrader[wrapper]" not in result.stdout
    # stdout carries the forwarded command and nothing else.
    assert result.stdout.strip().startswith("UV-ARGS: ")


def test_an_explicit_interpreter_skips_uv(launcher, stub_uv, sandbox, tmp_path) -> None:
    """Rule 1 of the runner list, and the only one testable off Windows.

    It takes the interpreter path exactly as given, so it needs none of the
    `Scripts\\python.exe` layout the other venv rules depend on.
    """
    checkout = _make_checkout(tmp_path / "pinned")
    fake_python = tmp_path / "my python" / "python"
    fake_python.parent.mkdir()
    fake_python.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-c" ]; then exit 0; fi\n'
        "printf 'PY-ARGS: %s\\n' \"$*\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    result = _run(
        launcher,
        cwd=tmp_path,
        env_extra={
            "OPENGRADER_HOME": str(checkout),
            "OPENGRADER_PYTHON": str(fake_python),
        },
        stub_uv_dir=stub_uv,
        **sandbox,
    )
    assert result.returncode == 0, result.stderr
    assert "PY-ARGS: -m opengrader doctor --json" in result.stdout
    assert "UV-ARGS" not in result.stdout


def test_an_explicit_interpreter_that_cannot_import_is_rejected_clearly(
    launcher, stub_uv, sandbox, tmp_path
) -> None:
    """Naming a wrong interpreter must not fall through to a traceback."""
    checkout = _make_checkout(tmp_path / "pinned")
    fake_python = tmp_path / "wrong python" / "python"
    fake_python.parent.mkdir()
    fake_python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_python.chmod(0o755)

    result = _run(
        launcher,
        cwd=tmp_path,
        env_extra={
            "OPENGRADER_HOME": str(checkout),
            "OPENGRADER_PYTHON": str(fake_python),
        },
        stub_uv_dir=stub_uv,
        **sandbox,
    )
    assert result.returncode == 1
    assert "cannot import opengrader" in (result.stdout + result.stderr)


def test_arguments_reach_the_tool_unchanged(launcher, stub_uv, sandbox, tmp_path) -> None:
    """The launcher forwards; it must not parse, reorder or swallow anything.

    Flag-shaped arguments are the risk here: PowerShell binds `-name` tokens to
    script parameters, so this script deliberately declares none and reads the
    automatic `$args`.
    """
    assert PWSH is not None
    checkout = _make_checkout(tmp_path / "pinned")
    env = {
        "PATH": os.pathsep.join([str(stub_uv), "/usr/bin", "/bin"]),
        "HOME": str(sandbox["home"]),
        "LOCALAPPDATA": str(sandbox["cache"]),
        "OPENGRADER_HOME": str(checkout),
        "OPENGRADER_SEARCH_ROOTS": str(sandbox["home"] / "no-such-root"),
    }
    result = subprocess.run(
        [
            PWSH, "-NoProfile", "-File", str(launcher),
            "split", "--input", "a file.pdf", "--jobs", "8", "--help",
        ],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert (
        f"run --project {checkout} opengrader split --input a file.pdf --jobs 8 --help"
        in result.stdout
    )
