import os
import signal
import subprocess
import sys
import time

from click.testing import CliRunner
import pytest
import psutil

from opengrader.cli.web import open_command, stop_command
from opengrader.server.workspace.client import (
    WorkspaceClient,
    running_service,
    workspace_free,
)


@pytest.fixture
def web_process(tmp_path):
    root = tmp_path / "workspace"
    processes = []

    def start(*arguments, script=None):
        entry = ["-c", script] if script else ["-m", "opengrader.cli.main"]
        process = subprocess.Popen(
            [sys.executable, *entry, "web", "--workspace", str(root), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=os.name == "posix",
        )
        processes.append(process)
        return process

    yield root, start

    (tmp_path / "release-job").touch()
    connection = running_service(root)
    if connection:
        WorkspaceClient(connection).request("POST", "/api/shutdown")
    for process in processes:
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
    deadline = time.monotonic() + 10
    while not workspace_free(root) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert workspace_free(root)


def _wait_for_service(process, root):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        connection = running_service(root)
        if connection:
            return connection
        if process.poll() is not None:
            pytest.fail(f"web exited before starting: {process.communicate()[0]}")
        time.sleep(0.05)
    pytest.fail("web did not become ready")


def _job_script(tmp_path):
    # A real executor job makes shutdown wait for ScanJobs.close().
    return f"""
import time
import os
from pathlib import Path
from opengrader.server.scan_jobs import ScanJobs
from opengrader.cli.main import main

original_init = ScanJobs.__init__
def initialize(self, *args, **kwargs):
    original_init(self, *args, **kwargs)
    def job():
        pid = self.processing.submit(os.getpid).result()
        Path({str(tmp_path / 'worker-pid')!r}).write_text(str(pid))
        Path({str(tmp_path / 'job-started')!r}).touch()
        while not Path({str(tmp_path / 'release-job')!r}).exists():
            self.processing.submit(time.sleep, 0.1).result()
        Path({str(tmp_path / 'job-finished')!r}).touch()
    self.executor.submit(job)
ScanJobs.__init__ = initialize
main()
"""


def _wait_for_job(tmp_path):
    deadline = time.monotonic() + 10
    while not (tmp_path / "job-started").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert (tmp_path / "job-started").exists()


@pytest.mark.skipif(os.name != "posix", reason="Uses POSIX process signals")
@pytest.mark.parametrize(
    "stop_signal", [signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", None)]
)
def test_web_owns_service_and_drains_jobs_on_signal(web_process, tmp_path, stop_signal):
    root, start = web_process
    process = start("--no-open", script=_job_script(tmp_path))
    connection = _wait_for_service(process, root)
    assert connection.pid == process.pid
    assert not workspace_free(root)

    _wait_for_job(tmp_path)
    if stop_signal == signal.SIGTERM:
        process.send_signal(stop_signal)
    else:
        os.killpg(process.pid, stop_signal)
    deadline = time.monotonic() + 10
    while running_service(root) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert running_service(root) is None
    assert process.poll() is None
    assert not workspace_free(root)

    (tmp_path / "release-job").touch()
    output, _ = process.communicate(timeout=10)
    assert (tmp_path / "job-finished").exists(), output
    assert workspace_free(root)
    assert not (root / ".opengrader/service.json").exists()


def test_web_detach_exits_and_leaves_service_running(web_process):
    root, start = web_process
    process = start("--detach", "--no-open")
    output, _ = process.communicate(timeout=20)
    assert process.returncode == 0, output
    connection = running_service(root)
    assert connection is not None
    assert connection.pid != process.pid
    assert f"running with PID {connection.pid}" in output
    assert connection.url in output
    result = CliRunner().invoke(stop_command, ["--workspace", str(root)])
    assert result.exit_code == 0, result.output
    assert workspace_free(root)


@pytest.mark.skipif(os.name != "posix", reason="Uses POSIX process signals")
@pytest.mark.parametrize("stop_mode", ["timeout", "second_interrupt", "api"])
def test_shutdown_forces_stuck_jobs_and_workers(web_process, tmp_path, stop_mode):
    root, start = web_process
    # Stand in for a browser process. Forced shutdown must leave it alone.
    script = f"""
import subprocess
import sys
from pathlib import Path
sibling = subprocess.Popen(
    [sys.executable, '-c', 'import time; time.sleep(60)'],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
)
Path({str(tmp_path / 'sibling-pid')!r}).write_text(str(sibling.pid))
""" + _job_script(tmp_path)
    process = start("--no-open", script=script)
    connection = _wait_for_service(process, root)
    sibling = psutil.Process(int((tmp_path / "sibling-pid").read_text()))
    try:
        _wait_for_job(tmp_path)
        worker = psutil.Process(int((tmp_path / "worker-pid").read_text()))
        started = time.monotonic()
        if stop_mode == "api":
            WorkspaceClient(connection).request("POST", "/api/shutdown")
        else:
            process.send_signal(signal.SIGINT)
        if stop_mode == "second_interrupt":
            deadline = time.monotonic() + 2
            while running_service(root) and time.monotonic() < deadline:
                time.sleep(0.05)
            process.send_signal(signal.SIGINT)
        output, _ = process.communicate(timeout=8)
        elapsed = time.monotonic() - started
        assert process.returncode == 1, output
        assert "Forcing shutdown" in output
        if stop_mode == "second_interrupt":
            assert elapsed < 3
        else:
            assert 4.5 <= elapsed < 8
        assert not (tmp_path / "job-finished").exists()
        assert not (root / ".opengrader/service.json").exists()
        assert workspace_free(root)
        _, alive = psutil.wait_procs([worker], timeout=3)
        assert not alive
        assert sibling.is_running() and sibling.status() != psutil.STATUS_ZOMBIE

        # A forced stop must not leave a lock that prevents restarting.
        restarted = start("--no-open")
        assert _wait_for_service(restarted, root).pid == restarted.pid
        result = CliRunner().invoke(stop_command, ["--workspace", str(root)])
        assert result.exit_code == 0, result.output
        restarted.communicate(timeout=10)
        assert restarted.returncode == 0
    finally:
        if sibling.is_running():
            sibling.kill()
        psutil.wait_procs([sibling], timeout=3)


def test_detach_reports_pid_and_keeps_browser_behavior(web_process, monkeypatch):
    import webbrowser

    root, _ = web_process
    opened = []
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    runner = CliRunner(mix_stderr=False)
    connection = None
    for _ in range(2):
        result = runner.invoke(open_command, ["--detach", "--workspace", str(root)])
        assert result.exit_code == 0, result.output
        active = running_service(root)
        assert active is not None
        if connection is not None:
            assert active == connection
        connection = active
        assert f"running with PID {active.pid}" in result.stderr
        assert active.url in result.stderr
    assert opened == [connection.url, connection.url]


def test_web_reuses_existing_service_without_stopping_it(web_process):
    root, start = web_process
    original = start("--no-open")
    connection = _wait_for_service(original, root)
    second = start("--no-open")
    output, _ = second.communicate(timeout=15)
    assert second.returncode == 0, output
    assert f"already running with PID {connection.pid}" in output
    assert running_service(root) == connection
    assert original.poll() is None
    result = CliRunner().invoke(stop_command, ["--workspace", str(root)])
    assert result.exit_code == 0, result.output
    original.communicate(timeout=10)
    assert original.returncode == 0


def test_web_opens_browser_only_after_service_is_ready(tmp_path, monkeypatch):
    import webbrowser

    root = tmp_path / "workspace"
    opened = []

    def open_browser(url):
        connection = running_service(root)
        assert connection is not None
        assert connection.url == url
        opened.append(url)
        WorkspaceClient(connection).request("POST", "/api/shutdown")
        return True

    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.setattr(webbrowser, "open", open_browser)
    result = CliRunner().invoke(open_command, ["--workspace", str(root)])
    assert result.exit_code == 0, result.output
    assert len(opened) == 1
    assert workspace_free(root)
