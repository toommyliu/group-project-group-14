import os
import subprocess
import sys

import pytest

from opengrader.server import dev


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
        assert dev.run_dev(8000) == (api_exit or 1)
        assert len(processes) == 2
        assert all(process.poll() is not None for process in processes)
        assert processes[1].returncode != 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait()
