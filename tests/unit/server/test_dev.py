import os
import subprocess
import sys

import pytest

from opengrader.server import dev
from opengrader.server.workspace.client import exclusive_lock
from opengrader.server.client import ServerInUse
from opengrader.server.home import resolve_data_home


@pytest.mark.parametrize("api_exit", [0, 7])
def test_api_exit_stops_frontend_and_reports_failure(tmp_path, monkeypatch, api_exit):
    source = tmp_path / "opengrader"
    vite = source / "web/node_modules/vite/bin/vite.js"
    vite.parent.mkdir(parents=True)
    vite.touch()
    monkeypatch.setattr(dev, "__file__", str(source / "server/dev.py"))
    monkeypatch.setattr(dev.shutil, "which", lambda name: sys.executable)
    processes = []

    def start(script):
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            start_new_session=os.name == "posix",
        )
        processes.append(process)
        return process

    monkeypatch.setattr(
        dev, "_start_api", lambda *args, **kwargs: start(f"raise SystemExit({api_exit})")
    )
    monkeypatch.setattr(
        dev, "_start_vite", lambda *args: start("import time; time.sleep(2)")
    )

    try:
        assert dev.run_dev(8000, tmp_path / "workspace") == (api_exit or 1)
        assert len(processes) == 2
        assert all(process.poll() is not None for process in processes)
        assert processes[1].returncode != 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait()


def test_dev_refuses_workspace_in_use_before_starting(tmp_path, monkeypatch):
    source = tmp_path / "opengrader"
    vite = source / "web/node_modules/vite/bin/vite.js"
    vite.parent.mkdir(parents=True)
    vite.touch()
    monkeypatch.setattr(dev, "__file__", str(source / "server/dev.py"))
    monkeypatch.setattr(dev.shutil, "which", lambda name: sys.executable)
    monkeypatch.setattr(dev, "_start_api", lambda *args: pytest.fail("API started"))
    monkeypatch.setattr(dev, "_start_vite", lambda *args: pytest.fail("Vite started"))

    root = tmp_path / "workspace"
    with (
        exclusive_lock(resolve_data_home() / "run/server.lock"),
        pytest.raises(ServerInUse, match="already running"),
    ):
        dev.run_dev(8000, root)

