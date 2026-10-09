"""Getting the AI on this computer ready: Ollama installed, answering, models downloaded.

There is one implementation of this, used by AI setup's *Set up local AI*
button and by every tool's `--auto-ollama` (what "Start the AI automatically
when needed" turns on). Five tools used to carry their own copy, each a
little different, and all of them downloaded a model without a word on
screen.

Which models are wanted is never decided here. A caller passes the names it
resolved through `app_config.resolve()`, so changing
`app_config.DEFAULT_VISION_MODEL` / `DEFAULT_TEXT_MODEL`, or typing another
name into AI setup, is all it takes for a different model to be downloaded.

Like `core.homebrew`, this changes the machine — it starts a server and
downloads several gigabytes — and only when asked to: a button press, or the
setting above. A model is downloaded through the server's own HTTP API
rather than by running `ollama pull`, for three reasons: the server reports
progress as numbers instead of a drawn progress bar, the download goes to
the server AI setup names (which may not be the one the `ollama` command
would talk to), and stopping it is closing a connection.

Nothing here prints. Callers pass `emit` and decide where a line goes.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import IO
from urllib.parse import urlsplit

__all__ = [
    "OLLAMA_DOWNLOAD_URL",
    "LocalAIError",
    "LocalAICancelled",
    "OllamaNotInstalled",
    "Cancel",
    "PullProgress",
    "StartedServer",
    "LocalAIStatus",
    "find_ollama",
    "is_this_computer",
    "server_listening",
    "start_server",
    "installed_models",
    "same_model",
    "distinct_models",
    "missing_models",
    "pull_model",
    "install_ollama",
    "progress_lines",
    "prepare",
    "prepare_for_run",
]

OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"

DEFAULT_PORT = 11434

# A server that is going to start answers within a few seconds; this is the
# allowance for a slow disk, not an expected wait.
SERVER_START_SECONDS = 30.0

# How long the server may say nothing in the middle of a download before it
# counts as stuck. Generous on purpose: after the last byte arrives the
# server checks the whole file, silently, and on a slow disk that takes a
# while for a file of several gigabytes.
PULL_IDLE_TIMEOUT_SECONDS = 300.0

_PROBE_TIMEOUT_SECONDS = 5.0

# Where the `ollama` command lives when a desktop launcher starts with a bare
# PATH: Homebrew on Apple silicon, Homebrew on Intel (also where the Ollama
# app links it), then inside the app itself.
_OLLAMA_DIRS = {
    "darwin": (
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/Applications/Ollama.app/Contents/Resources",
    ),
    "linux": ("/usr/local/bin", "/usr/bin"),
}

_THIS_COMPUTER = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0", ""})


class LocalAIError(RuntimeError):
    """Something here could not be done; the message is written for the user."""


class LocalAICancelled(LocalAIError):
    """The user stopped it. Not a failure, so callers report it differently."""


class OllamaNotInstalled(LocalAIError):
    """There is no `ollama` command on this computer to start."""

    def __init__(self) -> None:
        super().__init__(
            "Ollama, the program that runs the AI on this computer, is not "
            f"installed. Install it from {OLLAMA_DOWNLOAD_URL}."
        )


class Cancel:
    """A stop request shared by the thread doing the work and the one asking.

    Setting a flag is not enough on its own: a download that is waiting on a
    stalled network, or a Homebrew install, never looks at a flag. So the
    work registers a *stopper* for whatever it is blocked in, and `set()`
    runs them.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._stoppers: list[Callable[[], None]] = []

    def is_set(self) -> bool:
        return self._event.is_set()

    def set(self) -> None:
        with self._lock:
            self._event.set()
            stoppers = list(self._stoppers)
        for stop in stoppers:
            _quietly(stop)

    def add_stopper(self, stop: Callable[[], None]) -> None:
        with self._lock:
            if not self._event.is_set():
                self._stoppers.append(stop)
                return
        # Asked to stop before the work got this far: stop it now.
        _quietly(stop)

    def remove_stopper(self, stop: Callable[[], None]) -> None:
        with self._lock:
            if stop in self._stoppers:
                self._stoppers.remove(stop)

    def check(self) -> None:
        if self._event.is_set():
            raise LocalAICancelled("Stopped.")


def _quietly(action: Callable[[], None]) -> None:
    try:
        action()
    except Exception:  # noqa: BLE001 - stopping must not fail the one who asked
        pass


# ---------------------------------------------------------------------------
# Is it there, is it answering
# ---------------------------------------------------------------------------


def find_ollama() -> str | None:
    """The `ollama` command, looked for beyond PATH too."""
    found = shutil.which("ollama")
    if found:
        return found
    directories = list(_OLLAMA_DIRS.get(sys.platform, ()))
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            directories.append(os.path.join(local, "Programs", "Ollama"))
    if not directories:
        return None
    return shutil.which("ollama", path=os.pathsep.join(directories))


def _address(host: str) -> tuple[str, int]:
    text = host.strip()
    parts = urlsplit(text if "//" in text else f"//{text}")
    try:
        port = parts.port
    except ValueError:
        port = None
    return (parts.hostname or "127.0.0.1"), (port or DEFAULT_PORT)


def is_this_computer(host: str) -> bool:
    """Whether a server at `host` is one this computer could start itself."""
    return _address(host)[0].lower() in _THIS_COMPUTER


def server_listening(host: str, *, timeout_seconds: float = 1.0) -> bool:
    hostname, port = _address(host)
    if hostname == "0.0.0.0":
        hostname = "127.0.0.1"
    try:
        with socket.create_connection((hostname, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Starting the server
# ---------------------------------------------------------------------------


@dataclass
class StartedServer:
    """An `ollama serve` this process started, for a caller that stops it again."""

    process: subprocess.Popen
    log_file: IO[str] | None = None

    def log_tail(self, lines: int = 8) -> str:
        if self.log_file is None:
            return ""
        try:
            self.log_file.seek(0)
            text = self.log_file.read()
        except (OSError, ValueError):
            return ""
        return "\n".join(text.splitlines()[-lines:])

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.log_file is not None:
            _quietly(self.log_file.close)
            self.log_file = None


def start_server(
    host: str,
    *,
    emit: Callable[[str], None],
    binary: str | None = None,
    cancel: Cancel | None = None,
    wait_seconds: float = SERVER_START_SECONDS,
) -> StartedServer | None:
    """Start `ollama serve` unless a server is already answering at `host`.

    Returns None when one was already there. Raises `OllamaNotInstalled`
    when there is nothing to start, and `LocalAIError` when the server is on
    another computer or did not come up — in which case the one started here
    is stopped again, so nothing is left behind half-running.
    """
    if server_listening(host):
        return None
    if not is_this_computer(host):
        raise LocalAIError(
            f"The AI server at {host} is on another computer and is not "
            "answering. Start Ollama there, or change the server address in "
            "AI setup."
        )
    binary = binary or find_ollama()
    if binary is None:
        raise OllamaNotInstalled()

    emit(f"Starting the AI server (ollama serve) at {host}…")
    hostname, port = _address(host)
    env = dict(os.environ)
    if port != DEFAULT_PORT:
        # Ollama listens where OLLAMA_HOST says. Only filled in when the
        # user has not set it, and only for a port it would not pick itself.
        env.setdefault("OLLAMA_HOST", f"{hostname}:{port}")
    log_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    try:
        process = subprocess.Popen(
            [binary, "serve"],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            env=env,
        )
    except OSError as exc:
        log_file.close()
        raise LocalAIError(f"Ollama could not be started: {exc}") from exc
    started = StartedServer(process=process, log_file=log_file)

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if cancel is not None and cancel.is_set():
            started.stop()
            raise LocalAICancelled("Stopped.")
        if server_listening(host):
            return started
        if process.poll() is not None:
            break
        time.sleep(0.25)

    tail = started.log_tail()
    exited = process.poll() is not None
    started.stop()
    reason = (
        "Ollama stopped as soon as it was started."
        if exited
        else f"Ollama did not answer at {host} within {wait_seconds:g} s."
    )
    raise LocalAIError(f"{reason}\n{tail}" if tail else reason)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def installed_models(
    host: str, *, timeout_seconds: float = _PROBE_TIMEOUT_SECONDS
) -> list[str]:
    from opengrader.core.llm import ollama_transport

    return ollama_transport.fetch_model_names(
        host=host, timeout_seconds=timeout_seconds, error_factory=LocalAIError
    )


def _canonical(model: str) -> str:
    # Ollama files "llama3.1" as "llama3.1:latest".
    name = model.strip()
    return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"


def same_model(first: str, second: str) -> bool:
    return _canonical(first) == _canonical(second)


def distinct_models(models: Iterable[str]) -> tuple[str, ...]:
    """The names given, blanks dropped and each model once ("a" is "a:latest")."""
    seen: dict[str, str] = {}
    for model in models:
        if model and model.strip():
            seen.setdefault(_canonical(model), model.strip())
    return tuple(seen.values())


def missing_models(wanted: Iterable[str], installed: Iterable[str]) -> tuple[str, ...]:
    """The wanted models the server does not have, each once, in the order asked."""
    have = {_canonical(name) for name in installed if name}
    return tuple(model for model in distinct_models(wanted) if _canonical(model) not in have)


def _size(count: int) -> str:
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.1f} GB"
    return f"{count / 1_000_000:.0f} MB"


@dataclass(frozen=True)
class PullProgress:
    """How far a download has got, summed over the files a model is made of."""

    model: str
    status: str
    completed: int = 0
    total: int = 0

    @property
    def percent(self) -> int | None:
        if self.total <= 0:
            return None
        return min(100, int(self.completed * 100 / self.total))

    def describe(self) -> str:
        percent = self.percent
        if percent is None:
            return self.status or "starting…"
        return f"{percent}% · {_size(self.completed)} of {_size(self.total)}"


def _explain_pull_failure(model: str, reported: str) -> str:
    first = (reported.strip().splitlines() or [""])[0][:200]
    lowered = reported.lower()
    if any(
        sign in lowered
        for sign in ("file does not exist", "manifest unknown", "invalid model name", "not found")
    ):
        return (
            f"Ollama's library has no model named {model!r}. Check the name "
            "in AI setup."
        )
    if "no space left" in lowered:
        return f"This computer ran out of disk space while downloading {model}."
    if "newer version" in lowered:
        return f"{model} needs a newer Ollama. Update Ollama, then try again."
    if any(
        sign in lowered
        for sign in ("no such host", "dial tcp", "unreachable", "i/o timeout", "tls")
    ):
        return (
            "Ollama could not reach its model library — is this computer "
            f"online? ({first})"
        )
    return f"Downloading {model} failed: {first}"


def pull_model(
    host: str,
    model: str,
    *,
    on_progress: Callable[[PullProgress], None] | None = None,
    cancel: Cancel | None = None,
    idle_timeout_seconds: float = PULL_IDLE_TIMEOUT_SECONDS,
) -> None:
    """Have the server at `host` download `model`, reporting as it goes.

    A download that is stopped or fails keeps what it fetched; asking again
    carries on from there. Raises `LocalAICancelled` when stopped through
    `cancel`, otherwise `LocalAIError` with the reason in plain words.
    """
    from opengrader.core.llm import ollama_transport

    files: dict[str, tuple[int, int]] = {}
    cutters: list[Callable[[], None]] = []
    failure = ""
    succeeded = False

    def _opened(cut: Callable[[], None]) -> None:
        if cancel is not None:
            cutters.append(cut)
            cancel.add_stopper(cut)

    try:
        for event in ollama_transport.pull_model_events(
            host=host,
            model=model,
            idle_timeout_seconds=idle_timeout_seconds,
            error_factory=LocalAIError,
            on_open=_opened,
        ):
            if cancel is not None:
                cancel.check()
            reported = event.get("error")
            if reported:
                failure = _explain_pull_failure(model, str(reported))
                break
            status = str(event.get("status") or "")
            digest = event.get("digest")
            total = event.get("total")
            if digest and isinstance(total, int) and total > 0:
                completed = event.get("completed")
                files[str(digest)] = (
                    completed if isinstance(completed, int) else 0,
                    total,
                )
            if on_progress is not None:
                on_progress(
                    PullProgress(
                        model=model,
                        status=status,
                        completed=sum(done for done, _ in files.values()),
                        total=sum(size for _, size in files.values()),
                    )
                )
            if status == "success":
                succeeded = True
    except LocalAICancelled:
        raise
    except LocalAIError as exc:
        if cancel is not None and cancel.is_set():
            # Cutting the connection is how a stop reaches a waiting read,
            # and it surfaces here as a lost connection.
            raise LocalAICancelled("Stopped.") from exc
        if "returned HTTP" in str(exc):
            # The server refused the request outright, with its reason in
            # the body: the same reasons it otherwise reports part-way.
            raise LocalAIError(_explain_pull_failure(model, str(exc))) from exc
        raise LocalAIError(
            f"Downloading {model} was interrupted: {exc}. Try again — what "
            "was downloaded so far is kept."
        ) from exc
    finally:
        if cancel is not None:
            for cut in cutters:
                cancel.remove_stopper(cut)

    if cancel is not None:
        # A cut connection can also look like the reply simply ending.
        cancel.check()
    if failure:
        raise LocalAIError(failure)
    if not succeeded:
        raise LocalAIError(
            f"The download of {model} ended before it finished. Try again — "
            "what was downloaded so far is kept."
        )


def progress_lines(
    emit: Callable[[str], None], *, step: int = 10
) -> Callable[[PullProgress], None]:
    """A progress callback for somewhere that can only add lines: one per `step` percent."""
    last: dict[str, int] = {}

    def report(progress: PullProgress) -> None:
        percent = progress.percent
        if percent is None:
            return
        mark = percent // step
        if mark <= last.get(progress.model, -1):
            return
        last[progress.model] = mark
        emit(f"  {progress.model}: {progress.describe()}")

    return report


# ---------------------------------------------------------------------------
# Installing Ollama itself (macOS, through Homebrew)
# ---------------------------------------------------------------------------


def install_ollama(
    brew: str,
    *,
    on_line: Callable[[str], None] | None = None,
    cancel: Cancel | None = None,
) -> str:
    """`brew install ollama`; returns where the command ended up.

    Whether it worked is decided by looking for the command afterwards, not
    by Homebrew's exit code alone.
    """
    from opengrader.core.homebrew import HomebrewError, brew_install

    def _started(process: subprocess.Popen[str]) -> None:
        if cancel is not None:
            cancel.add_stopper(process.terminate)

    try:
        brew_install(brew, ("ollama",), on_line=on_line, on_start=_started)
    except HomebrewError as exc:
        if cancel is not None and cancel.is_set():
            raise LocalAICancelled("Stopped.") from exc
        detail = f"\n{exc.tail}" if exc.tail else ""
        raise LocalAIError(f"Ollama was not installed. {exc}{detail}") from exc
    binary = find_ollama()
    if binary is None:
        raise LocalAIError(
            "Homebrew finished, but the ollama command still cannot be found. "
            f"Install Ollama from {OLLAMA_DOWNLOAD_URL} instead."
        )
    return binary


# ---------------------------------------------------------------------------
# The whole job
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalAIStatus:
    """Where things stand once `prepare` has done what it was allowed to."""

    host: str
    wanted: tuple[str, ...]
    installed: tuple[str, ...]
    missing: tuple[str, ...]
    started_server: bool = False

    @property
    def ready(self) -> bool:
        return not self.missing


def prepare(
    host: str,
    models: Sequence[str],
    *,
    emit: Callable[[str], None],
    on_progress: Callable[[PullProgress], None] | None = None,
    cancel: Cancel | None = None,
    install: bool = False,
    download: bool = False,
) -> LocalAIStatus:
    """Get the server at `host` answering with `models` on it.

    Starting a server that is installed is always done: it is quick and
    costs nothing. The two expensive steps are separate permissions —
    `install` (Ollama itself, through Homebrew, where there is one) and
    `download` (the missing models) — so a caller can first find out what is
    needed and ask before spending the user's bandwidth. With `download`
    off, the returned status simply lists what is missing.

    A server started here is left running: whoever asked wants to use it.
    """
    wanted = distinct_models(models)
    started = False
    if cancel is not None:
        cancel.check()

    if not server_listening(host):
        binary = find_ollama() if is_this_computer(host) else None
        if binary is None and is_this_computer(host):
            from opengrader.core.homebrew import find_homebrew

            brew = find_homebrew() if install else None
            if brew is None:
                raise OllamaNotInstalled()
            emit(
                "Installing Ollama with Homebrew. This needs the internet and "
                "can take a few minutes."
            )
            binary = install_ollama(brew, on_line=emit, cancel=cancel)
            emit("Ollama installed.")
        started = start_server(host, emit=emit, binary=binary, cancel=cancel) is not None
    emit(f"The AI server is answering at {host}.")

    installed = tuple(installed_models(host))
    missing = missing_models(wanted, installed)
    if missing and download:
        for model in missing:
            if cancel is not None:
                cancel.check()
            emit(f"Downloading {model}…")
            pull_model(host, model, on_progress=on_progress, cancel=cancel)
            emit(f"{model} downloaded.")
        installed = tuple(installed_models(host))
        missing = missing_models(wanted, installed)
        if missing:
            raise LocalAIError(
                "The download finished, but the server still does not list "
                f"{', '.join(missing)}."
            )
    return LocalAIStatus(
        host=host,
        wanted=wanted,
        installed=installed,
        missing=missing,
        started_server=started,
    )


def prepare_for_run(
    host: str,
    models: Sequence[str],
    *,
    emit: Callable[[str], None],
) -> StartedServer | None:
    """What `--auto-ollama` does before a tool uses the AI.

    Starts the server if it is not answering and downloads any of `models`
    it lacks, a line of progress at a time. Returns the server when this
    call started it, for a tool that stops it again when it is done.

    Only a missing Ollama is raised (`OllamaNotInstalled`). Anything else
    becomes a warning line: the tool that follows checks the AI itself and
    reports what is wrong in its own words, or carries on without it.
    Ollama is never installed from here — that takes a button press.
    """
    try:
        started = start_server(host, emit=emit)
    except OllamaNotInstalled:
        raise
    except LocalAIError as exc:
        emit(f"Warning: {exc}")
        return None
    try:
        for model in missing_models(models, installed_models(host)):
            emit(
                f"Downloading model '{model}' — the first run downloads "
                "several GB…"
            )
            pull_model(host, model, on_progress=progress_lines(emit))
            emit(f"Model '{model}' downloaded.")
    except LocalAIError as exc:
        emit(f"Warning: {exc}")
    return started
