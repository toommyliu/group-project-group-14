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
  first use with the user's own Node.js and npm, so neither is ever needed by
  someone who never opens the browser app;
- installing Node.js when it is missing and the user asks, on a Mac
  that has Homebrew (`core/homebrew`), with Homebrew's output kept in
  `tools-install.log` beside the build log.
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
from typing import IO, Callable, Literal

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
    "npm_command",
    "tool_status",
    "missing_tool_formulae",
    "install_tools",
    "source_fingerprint",
    "build_log_path",
    "install_log_path",
    "build_web",
]

PREFERENCES_FILENAME = "browser.json"
BUILD_LOG_FILENAME = "web-build.log"
INSTALL_LOG_FILENAME = "tools-install.log"
# Written into dist/ right after a successful build, so a later run can tell
# "built" from "built from sources that have since changed" without needing
# to ask npm anything.
BUILD_STAMP_FILENAME = ".opengrader-build.json"

# The files that end up in the bundle. Anything else under the source tree
# (README, scripts/) can change without making dist/ stale.
_FINGERPRINT_ROOT_FILES = (
    "package.json",
    "package-lock.json",
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

# How long a `--version` call may take. The health panel asks often and must
# not hang on a broken tool, so its limit is short.
_TOOL_CHECK_SECONDS = 5.0
# The first run of a tool Homebrew has just installed can be much slower than
# that (macOS checks a new binary before letting it start), and calling a
# working install "failed" sends the user off to fix nothing.
_FIRST_RUN_CHECK_SECONDS = 30.0


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
    return (root / "package.json").is_file() and (root / "package-lock.json").is_file()


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
    falls back to comparing mtimes, same as a `dist/` built by `npm run build`."""
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
    stamp — a `dist/` a developer built with `npm run build`, or one the
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
    npm: ToolInfo | None


def _tool_version(
    argv: list[str], env: dict[str, str], timeout: float = _TOOL_CHECK_SECONDS
) -> str | None:
    try:
        result = subprocess.run(
            argv,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    text = (result.stdout or result.stderr).strip()
    return text or None


def tool_status(*, timeout: float = _TOOL_CHECK_SECONDS) -> ToolStatus:
    """Node and npm as this machine actually has them — for the health panel.

    A tool counts only if its `--version` call also succeeds within `timeout`
    seconds: a `node` on PATH that can't run is no better than no `node` at
    all.
    """
    search = _tool_path()
    env = {**os.environ, "PATH": search, "CI": "true"}

    node: ToolInfo | None = None
    node_path = shutil.which("node", path=search)
    if node_path:
        version = _tool_version([node_path, "--version"], env, timeout)
        if version:
            node = ToolInfo(path=node_path, version=version)

    npm: ToolInfo | None = None
    npm_path = shutil.which("npm", path=search)
    if npm_path:
        version = _tool_version([npm_path, "--version"], env, timeout)
        if version:
            npm = ToolInfo(path=npm_path, version=version)

    return ToolStatus(node=node, npm=npm)


def missing_tool_formulae(status: ToolStatus) -> tuple[str, ...]:
    """The Homebrew formulae that would supply what `status` lacks.

    Homebrew's `node` brings npm with it.
    """
    if status.node is None or status.npm is None:
        return ("node",)
    return ()


def install_log_path() -> Path:
    from opengrader.core.app_config import settings_path

    return settings_path().parent / INSTALL_LOG_FILENAME


def _open_install_log(target: Path) -> IO[str] | None:
    """Appends, so a retry that works does not erase why the first try failed.

    Best effort: a log that cannot be written must not stop the install.
    """
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        return target.open("a", encoding="utf-8")
    except OSError:
        return None


def _tool_status_after_install() -> ToolStatus:
    """The tools as a fresh install leaves them: patient, and asked twice.

    npm is itself run by Node.js, so one slow or failed first start of
    `node` can make either tool look missing when both are fine.
    """
    status = tool_status(timeout=_FIRST_RUN_CHECK_SECONDS)
    if missing_tool_formulae(status):
        status = tool_status(timeout=_FIRST_RUN_CHECK_SECONDS)
    return status


def _tool_report(label: str, info: ToolInfo | None) -> str:
    if info is None:
        return f"{label}: cannot be run"
    return f"{label}: {info.version} ({info.path})"


def install_tools(
    brew: str,
    *,
    on_line: Callable[[str], None] | None = None,
    on_start: Callable[[subprocess.Popen[str]], None] | None = None,
    log: Path | None = None,
) -> None:
    """Install Node.js (which brings npm) with Homebrew, if either is missing.

    What counts is the check afterwards, not Homebrew's exit code: "installed"
    means the build can find and run both, and a Homebrew that complained but
    left both working has not failed. Raises `WebBuildError` if they still
    cannot be run; Homebrew's whole output is appended to `log`.
    """
    from opengrader.core.homebrew import HomebrewError, brew_install

    formulae = missing_tool_formulae(tool_status())
    if not formulae:
        return
    target = log or install_log_path()
    stream = _open_install_log(target)

    def record(text: str) -> None:
        if stream is None:
            return
        try:
            stream.write(text + "\n")
            stream.flush()
        except OSError:
            pass

    def relay(text: str) -> None:
        record(text)
        if on_line:
            on_line(text)

    try:
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        record(f"[{started}] $ {brew} install {' '.join(formulae)}")
        failure: HomebrewError | None = None
        try:
            brew_install(brew, formulae, on_line=relay, on_start=on_start)
        except HomebrewError as exc:
            failure = exc
            record(str(exc))
        status = _tool_status_after_install()
        record(
            "Afterwards — "
            f"{_tool_report('Node.js', status.node)}; {_tool_report('npm', status.npm)}\n"
        )
    finally:
        if stream is not None:
            stream.close()

    if not missing_tool_formulae(status):
        return
    where = f"The full output is in {target}." if stream is not None else ""
    if failure is not None:
        raise WebBuildError(
            f"Node.js was not installed. {failure} Trying again often "
            f"fixes a download that failed. {where}".rstrip(),
            failure.tail,
        ) from failure
    raise WebBuildError(
        "Homebrew finished, but Node.js and npm still cannot be run here. Try "
        f"`brew install node` in a terminal to see why. {where}".rstrip()
    )


def _tool_path() -> str:
    entries = os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join([*entries, *(d for d in _EXTRA_TOOL_DIRS if d not in entries)])


def npm_command() -> list[str] | None:
    npm = shutil.which("npm", path=_tool_path())
    return [npm] if npm else None


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
# also start `npm ci` into the same `node_modules/`.
_build_lock = threading.Lock()


def build_web(
    source: Path | None = None,
    *,
    on_line: Callable[[str], None] | None = None,
    log: Path | None = None,
) -> None:
    """Install the web app's packages and build `dist/` (needs the internet once).

    Runs `npm ci` then `vite build`. The type check in `npm run build` is
    for developers; an end user only needs the bundle, and a
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
    npm = npm_command()
    if npm is None:
        raise WebBuildError(
            "The browser app needs Node.js and npm to build once. Install "
            "Node.js from https://nodejs.org (it includes npm), then try again."
        )
    env = {
        **os.environ,
        "PATH": _tool_path(),
        # No TTY here: never stop for a question nobody can answer.
        "CI": "true",
    }
    steps = (
        ("Installing packages", [*npm, "ci", "--no-audit", "--no-fund"]),
        ("Building the browser app", [*npm, "exec", "--", "vite", "build"]),
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
    # comparing mtimes, same as a dist/ built by hand with `npm run build`.
    _write_build_stamp(root, source_fingerprint(root))
