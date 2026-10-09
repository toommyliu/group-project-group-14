from __future__ import annotations

import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from opengrader.core import app_config
from opengrader.core.app_config import local_ai
from opengrader.core.app_config.local_ai import (
    Cancel,
    LocalAICancelled,
    LocalAIError,
    OllamaNotInstalled,
    PullProgress,
)

needs_shell = pytest.mark.skipif(
    sys.platform == "win32", reason="the stand-in ollama is a shell script"
)

GB = 1_000_000_000


# ---------------------------------------------------------------------------
# A stand-in Ollama server on a real loopback port. Nothing here reaches the
# internet, and no real model is ever downloaded: the "download" is a few
# lines of JSON, which is all a real one is from this side of the socket.
# ---------------------------------------------------------------------------


class _FakeOllama(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.models: list[str] = []
        self.pulled: list[str] = []
        self.release_stall = threading.Event()

    @property
    def host(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(BaseHTTPRequestHandler):
    server: _FakeOllama

    def log_message(self, *args: object) -> None:  # keep test output quiet
        return

    def _line(self, payload: dict) -> None:
        self.wfile.write(json.dumps(payload).encode("utf-8") + b"\n")
        self.wfile.flush()

    def do_GET(self) -> None:  # noqa: N802 - http.server's naming
        body = json.dumps(
            {"models": [{"name": name} for name in self.server.models]}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length) or b"{}")
        model = request["model"]
        self.server.pulled.append(model)
        if model.startswith("refused"):
            body = json.dumps({"error": "invalid model name"}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # No Content-Length: the reply ends when the connection closes, a
        # line at a time, as a real download's does.
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        self._line({"status": "pulling manifest"})
        if model.startswith("missing"):
            self._line({"error": "pull model manifest: file does not exist"})
            return
        if model.startswith("offline"):
            self._line(
                {"error": "dial tcp: lookup registry.ollama.ai: no such host"}
            )
            return
        self._line(
            {"status": "pulling aaa", "digest": "aaa", "total": 6 * GB, "completed": 3 * GB}
        )
        if model.startswith("stall"):
            self.server.release_stall.wait(timeout=20)
            return
        if model.startswith("cutoff"):
            return
        self._line(
            {"status": "pulling aaa", "digest": "aaa", "total": 6 * GB, "completed": 6 * GB}
        )
        self._line(
            {"status": "pulling bbb", "digest": "bbb", "total": 1 * GB, "completed": 1 * GB}
        )
        self._line({"status": "verifying sha256 digest"})
        self._line({"status": "success"})
        self.server.models.append(model)


@pytest.fixture
def ollama():
    server = _FakeOllama()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.release_stall.set()
        server.shutdown()
        server.server_close()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# ---------------------------------------------------------------------------
# Model names
# ---------------------------------------------------------------------------


def test_a_model_without_a_tag_is_the_latest_one() -> None:
    assert local_ai.missing_models(["llama3.1"], ["llama3.1:latest"]) == ()
    assert local_ai.missing_models(["llama3.1:8b"], ["llama3.1:latest"]) == (
        "llama3.1:8b",
    )
    assert local_ai.same_model("llama3.1", "llama3.1:latest")


def test_one_model_named_for_both_jobs_is_wanted_once() -> None:
    assert local_ai.distinct_models(["a:1", "", "a:1", "b"]) == ("a:1", "b")
    assert local_ai.missing_models(["a:1", "a:1", "b:2"], []) == ("a:1", "b:2")


def test_the_ollama_clients_default_to_the_configured_models() -> None:
    from opengrader.core.llm.ollama_client import OllamaConfig
    from opengrader.core.ocr.ollama_vlm import OllamaVisionConfig

    assert OllamaConfig().model == app_config.DEFAULT_TEXT_MODEL
    assert OllamaVisionConfig().model == app_config.DEFAULT_VISION_MODEL


# ---------------------------------------------------------------------------
# Downloading a model
# ---------------------------------------------------------------------------


def test_a_download_reports_progress_summed_over_its_files(ollama: _FakeOllama) -> None:
    seen: list[PullProgress] = []

    local_ai.pull_model(ollama.host, "good:1", on_progress=seen.append)

    assert ollama.models == ["good:1"]
    assert seen[0].percent is None  # the manifest: nothing to measure yet
    assert (seen[1].completed, seen[1].total) == (3 * GB, 6 * GB)
    assert seen[1].describe() == "50% · 3.0 GB of 6.0 GB"
    assert (seen[-1].completed, seen[-1].total) == (7 * GB, 7 * GB)
    assert seen[-1].status == "success"


def test_an_unknown_model_is_explained_by_name(ollama: _FakeOllama) -> None:
    with pytest.raises(LocalAIError) as caught:
        local_ai.pull_model(ollama.host, "missing:1")

    assert "'missing:1'" in str(caught.value)
    assert "AI setup" in str(caught.value)


def test_a_request_the_server_refuses_outright_is_explained_too(
    ollama: _FakeOllama,
) -> None:
    with pytest.raises(LocalAIError) as caught:
        local_ai.pull_model(ollama.host, "refused:1")

    assert "no model named 'refused:1'" in str(caught.value)


def test_a_download_with_no_internet_says_so(ollama: _FakeOllama) -> None:
    with pytest.raises(LocalAIError) as caught:
        local_ai.pull_model(ollama.host, "offline:1")

    assert "online" in str(caught.value)


def test_a_download_that_ends_early_is_not_reported_as_done(
    ollama: _FakeOllama,
) -> None:
    with pytest.raises(LocalAIError) as caught:
        local_ai.pull_model(ollama.host, "cutoff:1")

    assert "before it finished" in str(caught.value)
    assert ollama.models == []


def test_a_download_with_no_server_is_an_interruption_not_a_bad_name() -> None:
    with pytest.raises(LocalAIError) as caught:
        local_ai.pull_model(f"http://127.0.0.1:{_free_port()}", "good:1")

    assert "interrupted" in str(caught.value)


def test_stopping_reaches_a_download_that_is_waiting_on_the_network(
    ollama: _FakeOllama,
) -> None:
    # The server sends one line and then nothing, as a stalled connection
    # does. Only cutting the connection can end the read; a flag alone would
    # leave this waiting out the whole idle timeout.
    cancel = Cancel()
    started = threading.Event()
    outcome: list[BaseException | None] = []

    def run() -> None:
        try:
            local_ai.pull_model(
                ollama.host,
                "stall:1",
                on_progress=lambda progress: started.set()
                if progress.total
                else None,
                cancel=cancel,
                idle_timeout_seconds=60,
            )
        except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
            outcome.append(exc)
        else:
            outcome.append(None)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert started.wait(timeout=10)
    began = time.monotonic()
    cancel.set()
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert time.monotonic() - began < 5
    assert isinstance(outcome[0], LocalAICancelled)


def test_a_stop_asked_for_before_the_work_starts_still_stops_it() -> None:
    cancel = Cancel()
    cancel.set()
    ran: list[bool] = []

    cancel.add_stopper(lambda: ran.append(True))

    assert ran == [True]
    with pytest.raises(LocalAICancelled):
        cancel.check()


def test_progress_as_lines_is_one_line_per_step() -> None:
    lines: list[str] = []
    report = local_ai.progress_lines(lines.append, step=50)

    for done in (0, 1, 2, 3, 4, 5, 6):
        report(PullProgress("m:1", "pulling", completed=done * GB, total=6 * GB))
    report(PullProgress("m:1", "verifying"))

    assert lines == [
        "  m:1: 0% · 0 MB of 6.0 GB",
        "  m:1: 50% · 3.0 GB of 6.0 GB",
        "  m:1: 100% · 6.0 GB of 6.0 GB",
    ]


# ---------------------------------------------------------------------------
# Starting the server
# ---------------------------------------------------------------------------


def _fake_ollama_command(directory: Path, body: str) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    command = directory / "ollama"
    command.write_text(f"#!/bin/bash\n{body}\n")
    command.chmod(0o755)
    return str(command)


_LISTENER = """\
import os, socket, time
host, port = os.environ["OLLAMA_HOST"].split(":")
server = socket.create_server((host, int(port)))
while True:
    connection, _ = server.accept()
    connection.close()
"""


def test_a_server_that_is_already_answering_is_left_alone(ollama: _FakeOllama) -> None:
    lines: list[str] = []

    assert local_ai.start_server(ollama.host, emit=lines.append, binary="/nowhere") is None
    assert lines == []


def test_nothing_to_start_is_its_own_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_ai, "find_ollama", lambda: None)

    with pytest.raises(OllamaNotInstalled) as caught:
        local_ai.start_server(f"http://127.0.0.1:{_free_port()}", emit=lambda _: None)

    assert local_ai.OLLAMA_DOWNLOAD_URL in str(caught.value)


def test_a_server_on_another_computer_is_never_started_here(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_ai, "server_listening", lambda host, **_: False)
    monkeypatch.setattr(local_ai, "find_ollama", lambda: pytest.fail("looked for ollama"))

    with pytest.raises(LocalAIError) as caught:
        local_ai.start_server("http://192.168.50.4:11434", emit=lambda _: None)

    assert "another computer" in str(caught.value)
    assert not isinstance(caught.value, OllamaNotInstalled)


@needs_shell
def test_the_server_is_started_on_the_port_ai_setup_names_and_can_be_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    script = tmp_path / "listener.py"
    script.write_text(_LISTENER)
    binary = _fake_ollama_command(
        tmp_path / "bin", f'[ "$1" = serve ] && exec "{sys.executable}" "{script}"'
    )
    host = f"http://127.0.0.1:{_free_port()}"
    lines: list[str] = []

    started = local_ai.start_server(host, emit=lines.append, binary=binary)

    assert started is not None
    assert local_ai.server_listening(host)
    assert any("Starting the AI server" in line for line in lines)
    started.stop()
    assert started.process.poll() is not None
    assert not local_ai.server_listening(host)


@needs_shell
def test_a_server_that_dies_at_once_is_reported_with_what_it_said(
    tmp_path: Path,
) -> None:
    binary = _fake_ollama_command(
        tmp_path / "bin", 'echo "Error: listen tcp: address already in use"; exit 1'
    )

    with pytest.raises(LocalAIError) as caught:
        local_ai.start_server(
            f"http://127.0.0.1:{_free_port()}", emit=lambda _: None, binary=binary
        )

    assert "stopped as soon as it was started" in str(caught.value)
    assert "address already in use" in str(caught.value)


@needs_shell
def test_a_server_that_never_answers_is_stopped_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _fake_ollama_command(tmp_path / "bin", "exec sleep 60")
    processes: list = []
    real_popen = local_ai.subprocess.Popen

    def recording_popen(*args, **kwargs):  # noqa: ANN002, ANN003
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(local_ai.subprocess, "Popen", recording_popen)

    with pytest.raises(LocalAIError) as caught:
        local_ai.start_server(
            f"http://127.0.0.1:{_free_port()}",
            emit=lambda _: None,
            binary=binary,
            wait_seconds=0.6,
        )

    assert "did not answer" in str(caught.value)
    assert processes and processes[0].poll() is not None


# ---------------------------------------------------------------------------
# The whole job
# ---------------------------------------------------------------------------


def test_without_permission_to_download_it_only_says_what_is_missing(
    ollama: _FakeOllama,
) -> None:
    ollama.models = ["have:1"]

    status = local_ai.prepare(ollama.host, ["have:1", "good:1"], emit=lambda _: None)

    assert status.missing == ("good:1",)
    assert status.ready is False
    assert status.started_server is False
    assert ollama.pulled == []


def test_with_permission_it_downloads_only_what_is_missing(ollama: _FakeOllama) -> None:
    ollama.models = ["have:1"]
    lines: list[str] = []

    status = local_ai.prepare(
        ollama.host,
        ["have:1", "good:1", "good:2"],
        emit=lines.append,
        download=True,
    )

    assert ollama.pulled == ["good:1", "good:2"]
    assert status.ready
    assert status.missing == ()
    assert "Downloading good:1…" in lines
    assert "good:2 downloaded." in lines


def test_a_missing_ollama_is_not_installed_without_being_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opengrader.core import homebrew

    monkeypatch.setattr(local_ai, "find_ollama", lambda: None)
    monkeypatch.setattr(
        homebrew, "find_homebrew", lambda: pytest.fail("looked for Homebrew")
    )

    with pytest.raises(OllamaNotInstalled):
        local_ai.prepare(
            f"http://127.0.0.1:{_free_port()}",
            ["good:1"],
            emit=lambda _: None,
            download=True,
        )


def test_asked_to_install_it_goes_on_to_start_and_download(
    ollama: _FakeOllama, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opengrader.core import homebrew

    steps: list[str] = []
    listening = iter([False])  # nothing answering until "ollama serve" runs

    monkeypatch.setattr(
        local_ai,
        "server_listening",
        lambda host, **_: next(listening, True),
    )
    monkeypatch.setattr(local_ai, "find_ollama", lambda: None)
    monkeypatch.setattr(homebrew, "find_homebrew", lambda: "/stand-in/brew")

    def fake_install(brew, *, on_line=None, cancel=None):  # noqa: ANN001
        steps.append(f"install with {brew}")
        return "/stand-in/ollama"

    def fake_start(host, *, emit, binary=None, cancel=None):  # noqa: ANN001
        steps.append(f"start {binary}")
        return object()

    monkeypatch.setattr(local_ai, "install_ollama", fake_install)
    monkeypatch.setattr(local_ai, "start_server", fake_start)

    status = local_ai.prepare(
        ollama.host, ["good:1"], emit=lambda _: None, install=True, download=True
    )

    assert steps == ["install with /stand-in/brew", "start /stand-in/ollama"]
    assert status.started_server is True
    assert status.ready
    assert ollama.pulled == ["good:1"]


def test_a_failed_homebrew_install_keeps_homebrews_last_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opengrader.core import homebrew

    def boom(brew, formulae, *, on_line=None, on_start=None):  # noqa: ANN001
        raise homebrew.HomebrewError("`brew install ollama` failed (exit 1).", "no bottle")

    monkeypatch.setattr(homebrew, "brew_install", boom)

    with pytest.raises(LocalAIError) as caught:
        local_ai.install_ollama("/stand-in/brew")

    assert "Ollama was not installed" in str(caught.value)
    assert "no bottle" in str(caught.value)


# ---------------------------------------------------------------------------
# What --auto-ollama does
# ---------------------------------------------------------------------------


def test_before_a_run_a_missing_model_is_downloaded_a_line_at_a_time(
    ollama: _FakeOllama,
) -> None:
    lines: list[str] = []

    started = local_ai.prepare_for_run(ollama.host, ["good:1"], emit=lines.append)

    assert started is None  # the server was already there
    assert ollama.models == ["good:1"]
    assert any("Downloading model 'good:1'" in line for line in lines)
    assert any("good:1: 100%" in line for line in lines)


def test_before_a_run_an_installed_model_is_not_fetched_again(
    ollama: _FakeOllama,
) -> None:
    # `ollama pull` on a model that is already there asks the internet
    # whether a newer one exists; an offline run must not depend on that.
    ollama.models = ["good:1"]
    lines: list[str] = []

    local_ai.prepare_for_run(ollama.host, ["good:1"], emit=lines.append)

    assert ollama.pulled == []
    assert lines == []


def test_before_a_run_a_failed_download_is_a_warning_not_a_crash(
    ollama: _FakeOllama,
) -> None:
    lines: list[str] = []

    local_ai.prepare_for_run(ollama.host, ["missing:1"], emit=lines.append)

    assert any(line.startswith("Warning:") and "missing:1" in line for line in lines)


def test_before_a_run_a_missing_ollama_is_raised_for_the_tool_to_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_ai, "find_ollama", lambda: None)

    with pytest.raises(OllamaNotInstalled):
        local_ai.prepare_for_run(
            f"http://127.0.0.1:{_free_port()}", ["good:1"], emit=lambda _: None
        )
