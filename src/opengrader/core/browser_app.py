"""The optional browser app, as `opengrader classic` sees it.

Classic grades plain folders and needs none of this. A user who ticks
*Show Open in browser* in Tools gets a button on the Start grading card that
imports the class folder into a workspace and opens it in the browser
(`tui/ui/widgets/browser_launcher.py`). This module holds the parts that are
not UI:

- the opt-in itself, and the last workspace the button opened, in one small
  per-user file beside the AI settings (`browser.json`), best effort like
  `core/recents.py`;
- whether the web app is built (`web/dist/index.html`), and building it on
  first use with the user's own Node.js + pnpm, so neither is ever needed by
  someone who never opens the browser app;
- installing those two when they are missing and the user asks, on a Mac
  that has Homebrew (`core/homebrew`).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

__all__ = [
    "BrowserPreferences",
    "WebBuildError",
    "ToolInfo",
    "ToolStatus",
    "load_browser_preferences",
    "save_browser_preferences",
    "browser_preferences_path",
    "web_source_dir",
    "web_dist_ready",
    "web_dist_state",
    "web_buildable",
    "pnpm_command",
    "tool_status",
    "missing_tool_formulae",
    "install_tools",
    "source_fingerprint",
    "build_log_path",
    "build_web",
]

PREFERENCES_FILENAME = "browser.json"
BUILD_LOG_FILENAME = "web-build.log"
# Written into dist/ right after a successful build, so a later run can tell
# "built" from "built from sources that have since changed" without needing
# to ask pnpm anything.
BUILD_STAMP_FILENAME = ".opengrader-build.json"

# The files that end up in the bundle. Anything else under the source tree
# (README, scripts/, the pnpm store) can change without making dist/ stale.
_FINGERPRINT_ROOT_FILES = (
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "index.html",
    "vite.config.ts",
)
_FINGERPRINT_DIRS = ("src", "public")

# Where Node tools usually live when a desktop launcher starts with a bare PATH:
# Homebrew (Apple silicon, Intel), MacPorts, and per-user installs.
_EXTRA_TOOL_DIRS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/opt/local/bin",
    str(Path.home() / ".local/bin"),
)


@dataclass(frozen=True)
class BrowserPreferences:
    # Off until the user opts in: classic never touches a workspace otherwise.
    enabled: bool = False
    # The workspace root the button last opened, so the status line can offer
    # Stop for a service that outlived the previous classic session.
    last_workspace: str = ""


def browser_preferences_path() -> Path:
    from opengrader.core.app_config import settings_path

    return settings_path().parent / PREFERENCES_FILENAME


def load_browser_preferences(path: Path | None = None) -> BrowserPreferences:
    target = path or browser_preferences_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return BrowserPreferences()
    if not isinstance(data, dict):
        return BrowserPreferences()
    last = data.get("last_workspace")
    return BrowserPreferences(
        enabled=data.get("enabled") is True,
        last_workspace=last if isinstance(last, str) else "",
    )


def save_browser_preferences(
    preferences: BrowserPreferences, path: Path | None = None
) -> None:
    """Write the preferences; raises `OSError` so a failed opt-in is reported."""
    target = path or browser_preferences_path()
    body = {"version": 1, "enabled": preferences.enabled}
    if preferences.last_workspace:
        body["last_workspace"] = preferences.last_workspace
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def web_source_dir() -> Path:
    """`src/opengrader/web` — the same folder the service serves `dist/` from."""
    return Path(__file__).resolve().parents[1] / "web"


def web_dist_ready(source: Path | None = None) -> bool:
    return ((source or web_source_dir()) / "dist" / "index.html").is_file()


def web_buildable(source: Path | None = None) -> bool:
    """A checkout (or a desktop install copied from one) carries the sources."""
    root = source or web_source_dir()
    return (root / "package.json").is_file() and (root / "pnpm-lock.yaml").is_file()


def _fingerprint_files(root: Path) -> list[Path]:
    """Every source file that affects the bundle — never `node_modules/` or `dist/`."""
    files: list[Path] = []
    for name in _FINGERPRINT_ROOT_FILES:
        candidate = root / name
        if candidate.is_file():
            files.append(candidate)
    if root.is_dir():
        files.extend(path for path in root.glob("tsconfig*.json") if path.is_file())
    for name in _FINGERPRINT_DIRS:
        base = root / name
        if base.is_dir():
            files.extend(path for path in base.rglob("*") if path.is_file())
    return files


def source_fingerprint(source: Path | None = None) -> str:
    """A sha256 over the sources that end up in `dist/`, path and bytes both.

    Deterministic regardless of directory-listing order, so it can be
    compared against a stamp written on a different machine or run.
    """
    root = source or web_source_dir()
    files = sorted(_fingerprint_files(root), key=lambda p: p.relative_to(root).as_posix())
    digest = hashlib.sha256()
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            data = path.read_bytes()
        except OSError:
            continue
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def _stamp_path(root: Path) -> Path:
    return root / "dist" / BUILD_STAMP_FILENAME


def _write_build_stamp(root: Path, fingerprint: str) -> None:
    """Best effort: a stamp that fails to write just means the next check
    falls back to comparing mtimes, same as a `dist/` built by `pnpm build`."""
    stamp = _stamp_path(root)
    body = {
        "version": 1,
        "fingerprint": fingerprint,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        temporary = stamp.with_name(f".{stamp.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        temporary.replace(stamp)
    except OSError:
        pass


def web_dist_state(source: Path | None = None) -> Literal["missing", "stale", "ready"]:
    """Whether `dist/` can be served as-is, or wants a rebuild first.

    A stamp from `build_web` settles it exactly (fingerprint match). With no
    stamp — a `dist/` a developer built with `pnpm build`, or one the
    installer copied in — mtimes are the best available signal: stale if any
    hashed source is newer than `dist/index.html`. Anything unreadable is
    called stale rather than ready, since serving a bundle that is actually
    out of date is the worse mistake.
    """
    root = source or web_source_dir()
    index = root / "dist" / "index.html"
    try:
        if not index.is_file():
            return "missing"
        dist_mtime = index.stat().st_mtime
    except OSError:
        return "missing"

    try:
        stamp_data = json.loads(_stamp_path(root).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        stamp_data = None

    if isinstance(stamp_data, dict) and isinstance(stamp_data.get("fingerprint"), str):
        try:
            current = source_fingerprint(root)
        except OSError:
            return "stale"
        return "ready" if stamp_data["fingerprint"] == current else "stale"

    try:
        for path in _fingerprint_files(root):
            if path.stat().st_mtime > dist_mtime:
                return "stale"
    except OSError:
        return "stale"
    return "ready"


@dataclass(frozen=True)
class ToolInfo:
    path: str
    version: str


@dataclass(frozen=True)
class ToolStatus:
    node: ToolInfo | None
    pnpm: ToolInfo | None


def _tool_version(argv: list[str], env: dict[str, str]) -> str | None:
    try:
        result = subprocess.run(
            argv,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    text = (result.stdout or result.stderr).strip()
    return text or None


def tool_status() -> ToolStatus:
    """Node and pnpm as this machine actually has them — for the health panel.

    A tool counts only if its `--version` call also succeeds: a `node` on
    PATH that can't run is no better than no `node` at all.
    """
    search = _tool_path()
    env = {
        **os.environ,
        "PATH": search,
        "CI": "true",
        "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
    }

    node: ToolInfo | None = None
    node_path = shutil.which("node", path=search)
    if node_path:
        version = _tool_version([node_path, "--version"], env)
        if version:
            node = ToolInfo(path=node_path, version=version)

    pnpm: ToolInfo | None = None
    pnpm_path = shutil.which("pnpm", path=search)
    if pnpm_path:
        version = _tool_version([pnpm_path, "--version"], env)
        if version:
            pnpm = ToolInfo(path=pnpm_path, version=version)
    else:
        corepack_path = shutil.which("corepack", path=search)
        if corepack_path:
            version = _tool_version([corepack_path, "pnpm", "--version"], env)
            if version:
                pnpm = ToolInfo(path="via corepack", version=version)

    return ToolStatus(node=node, pnpm=pnpm)


def missing_tool_formulae(status: ToolStatus) -> tuple[str, ...]:
    """The Homebrew formulae that would supply what `status` lacks.

    Both are named when both are missing: Homebrew's pnpm does not bring
    Node.js with it.
    """
    formulae: list[str] = []
    if status.node is None:
        formulae.append("node")
    if status.pnpm is None:
        formulae.append("pnpm")
    return tuple(formulae)


def install_tools(
    brew: str,
    *,
    on_line: Callable[[str], None] | None = None,
    on_start: Callable[[subprocess.Popen[str]], None] | None = None,
) -> None:
    """Install whichever of Node.js and pnpm is missing, with Homebrew.

    Checks again afterwards, so "installed" means the build can find and run
    them, not just that Homebrew exited cleanly. Raises `WebBuildError`.
    """
    from opengrader.core.homebrew import HomebrewError, brew_install

    formulae = missing_tool_formulae(tool_status())
    if not formulae:
        return
    try:
        brew_install(brew, formulae, on_line=on_line, on_start=on_start)
    except HomebrewError as exc:
        raise WebBuildError(
            f"Node.js and pnpm were not installed. {exc}", exc.tail
        ) from exc
    still_missing = missing_tool_formulae(tool_status())
    if still_missing:
        raise WebBuildError(
            f"Homebrew finished, but {' and '.join(still_missing)} still cannot "
            f"be run here. Try `brew install {' '.join(still_missing)}` in a "
            "terminal to see why."
        )


def _tool_path() -> str:
    entries = os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join([*entries, *(d for d in _EXTRA_TOOL_DIRS if d not in entries)])


def pnpm_command() -> list[str] | None:
    """How to run pnpm here: pnpm itself, else Node's bundled corepack."""
    search = _tool_path()
    pnpm = shutil.which("pnpm", path=search)
    if pnpm:
        return [pnpm]
    corepack = shutil.which("corepack", path=search)
    if corepack:
        return [corepack, "pnpm"]
    return None


def build_log_path() -> Path:
    from opengrader.core.app_config import settings_path

    return settings_path().parent / BUILD_LOG_FILENAME


class WebBuildError(RuntimeError):
    """The build failed; `tail` holds the last lines of its output."""

    def __init__(self, message: str, tail: str = "") -> None:
        super().__init__(message)
        self.tail = tail


# One build at a time: the button that starts it is disabled while busy, but
# a stray second call (a retried notification, a second window) must not
# also start `pnpm install` into the same `node_modules/`.
_build_lock = threading.Lock()


def build_web(
    source: Path | None = None,
    *,
    on_line: Callable[[str], None] | None = None,
    log: Path | None = None,
) -> None:
    """Install the web app's packages and build `dist/` (needs the internet once).

    Runs `pnpm install --frozen-lockfile` then `vite build`. The type check in
    `pnpm build` is for developers; an end user only needs the bundle, and a
    type error must not stop them from grading.
    """
    if not _build_lock.acquire(blocking=False):
        raise WebBuildError("The browser app is already being built.")
    try:
        _build_web_locked(source, on_line=on_line, log=log)
    finally:
        _build_lock.release()


def _build_web_locked(
    source: Path | None,
    *,
    on_line: Callable[[str], None] | None,
    log: Path | None,
) -> None:
    root = source or web_source_dir()
    if not web_buildable(root):
        raise WebBuildError(
            "This copy of Opengrader has no browser app sources to build from."
        )
    pnpm = pnpm_command()
    if pnpm is None:
        raise WebBuildError(
            "The browser app needs Node.js and pnpm to build once. Install "
            "Node.js from https://nodejs.org (or `brew install node pnpm`), "
            "then try again."
        )
    env = {
        **os.environ,
        "PATH": _tool_path(),
        # No TTY here: never stop for a question nobody can answer.
        "CI": "true",
        "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
    }
    steps = (
        ("Installing packages", [*pnpm, "install", "--frozen-lockfile"]),
        ("Building the browser app", [*pnpm, "exec", "vite", "build"]),
    )
    target = log or build_log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tail: deque[str] = deque(maxlen=12)
    with target.open("w", encoding="utf-8") as stream:
        for label, argv in steps:
            if on_line:
                on_line(f"{label}…")
            stream.write(f"$ {' '.join(argv)}\n")
            stream.flush()
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=root,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError as exc:
                raise WebBuildError(f"{label} could not start: {exc}") from exc
            assert process.stdout is not None
            for line in process.stdout:
                stream.write(line)
                text = line.rstrip()
                if text:
                    tail.append(text)
                    if on_line:
                        on_line(text)
            if process.wait() != 0:
                raise WebBuildError(
                    f"{label} failed (exit {process.returncode}). "
                    f"The full output is in {target}.",
                    "\n".join(tail),
                )
    if not web_dist_ready(root):
        raise WebBuildError(
            f"The build finished but produced no dist/index.html. See {target}."
        )
    # Best effort: a missing stamp only means the next check falls back to
    # comparing mtimes, same as a dist/ built by hand with `pnpm build`.
    _write_build_stamp(root, source_fingerprint(root))
