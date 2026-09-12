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

CHECKOUT_DIRNAME = "Samsung T5 1"


def _make_checkout(root: Path, name: str = "group-project-group-14") -> Path:
    checkout = root / CHECKOUT_DIRNAME / "CMPE 195" / name
    (checkout / "src" / "opengrader").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text(
        '[project]\nname = "opengrader"\nversion = "1.0"\n', encoding="utf-8"
    )
    return checkout


def _make_decoy(root: Path, name: str) -> Path:
    decoy = root / CHECKOUT_DIRNAME / name
    decoy.mkdir(parents=True)
    (decoy / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1"\n', encoding="utf-8"
    )
    return decoy


@pytest.fixture
def launcher(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    target = bin_dir / "opengrader.ps1"
    shutil.copy(LAUNCHER_SOURCE, target)
    return target


@pytest.fixture
def stub_uv(tmp_path: Path) -> Path:
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
        "LOCALAPPDATA": str(cache),
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
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    line = next((ln for ln in result.stdout.splitlines() if ln.startswith("UV-ARGS: ")), None)
    assert line is not None, f"stub uv was never reached: {result.stdout!r}"
    args = line[len("UV-ARGS: ") :]
    assert args.startswith("run --project "), args
    assert args.endswith(" opengrader doctor --json"), args
    return args[len("run --project ") : -len(" opengrader doctor --json")]


def test_the_script_parses_and_runs_at_all(launcher, stub_uv, sandbox, tmp_path) -> None:
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
    root = tmp_path / "drive"
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
    assert result.stdout.strip().startswith("UV-ARGS: ")


def test_an_explicit_interpreter_skips_uv(launcher, stub_uv, sandbox, tmp_path) -> None:
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
