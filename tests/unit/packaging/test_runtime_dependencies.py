"""What the desktop launcher installs must be enough to run what it launches.

`packaging/bootstrap.sh` and `bootstrap.ps1` install with `uv sync --frozen
--no-dev`, while a developer checkout and this test run have the dev group
too. A package that only the dev group provides therefore works in every test
and fails on the first click of the desktop icon.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _names(requirements: list[str]) -> set[str]:
    return {
        re.match(r"[A-Za-z0-9._-]+", requirement).group().lower()
        for requirement in requirements
    }


def _locked_package(lock: dict, name: str) -> dict:
    return next(package for package in lock["package"] if package["name"] == name)


def test_launchers_install_without_the_dev_group():
    for launcher in ("bootstrap.sh", "bootstrap.ps1"):
        text = (REPO_ROOT / "packaging" / launcher).read_text(encoding="utf-8")
        assert re.search(r"uv sync --frozen --no-dev", text), launcher


def test_httpx_is_a_runtime_dependency():
    # authlib's Starlette client (server/auth.py, imported on every start of
    # the workspace service) imports httpx without declaring it.
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "authlib" in _names(project["project"]["dependencies"])
    assert "httpx" in _names(project["project"]["dependencies"])


def test_lockfile_agrees_with_pyproject_about_runtime_dependencies():
    # The launchers pass --frozen, so the lockfile decides what gets installed.
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = {
        dependency["name"]
        for dependency in _locked_package(lock, "opengrader")["dependencies"]
    }
    assert _names(project["project"]["dependencies"]) <= locked
