"""Run the local API and Vite app together with source reload."""

import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import structlog


logger = structlog.stdlib.get_logger(__name__)


def _stop(process: subprocess.Popen, *, timeout: float = 5) -> None:
    """Allow shutdown, then kill remaining workers even if their parent exited."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            process.terminate()
    except ProcessLookupError:
        pass

    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.warning("dev.stop_forced", pid=process.pid)
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False
            )
            process.kill()
    finally:
        if os.name == "posix":
            # API scan workers and Vite workers inherit the child's process group.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()


def _python_snapshot(source: Path) -> dict[Path, tuple[int, int, int]]:
    """Track edits, additions, deletions, and atomic saves without walking web dependencies."""
    snapshot = {}
    for directory, directories, files in source.walk():
        directories[:] = [
            name
            for name in directories
            if name not in {"web", "__pycache__"} and not name.startswith(".")
        ]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = directory / name
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            snapshot[path] = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    return snapshot


def _start_api(port: int, workspace: Path | None = None) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "opengrader.cli.serve",
            "--port",
            str(port),
            *(["--workspace", str(workspace)] if workspace else []),
        ],
        start_new_session=os.name == "posix",
    )


def _start_vite(node: str, web: Path, port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [
            node,
            str(web / "node_modules" / "vite" / "bin" / "vite.js"),
            "--host",
            "127.0.0.1",
            "--strictPort",
        ],
        cwd=web,
        env={**os.environ, "OPENGRADER_API_URL": f"http://127.0.0.1:{port}"},
        start_new_session=os.name == "posix",
    )


def run_dev(port: int, *, workspace: Path | None = None) -> int:
    """Restart both servers on Python edits; Vite handles frontend-only changes."""
    source = Path(__file__).resolve().parents[1]
    web = source / "web"
    vite = web / "node_modules" / "vite" / "bin" / "vite.js"
    node = shutil.which("node")
    if node is None or not vite.is_file():
        raise ValueError(
            "Development mode requires Node.js and the web dependencies. "
            "Install Node.js, then run "
            "'pnpm --dir src/opengrader/web install --frozen-lockfile'."
        )

    snapshot = _python_snapshot(source)
    changed_at: float | None = None
    api = None
    frontend = None

    def interrupt(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        api = _start_api(port, workspace)
        frontend = _start_vite(node, web, port)
        while True:
            code = frontend.poll()
            if code is not None:
                logger.error("dev.exited", process="vite", status=code, pid=frontend.pid)
                return code if code > 0 else 1
            if api is not None:
                code = api.poll()
                if code is not None:
                    logger.error("dev.exited", process="api", status=code, pid=api.pid)
                    return code if code > 0 else 1
            updated = _python_snapshot(source)
            if updated != snapshot:
                snapshot = updated
                changed_at = time.monotonic()
            elif changed_at is not None and time.monotonic() - changed_at >= 0.5:
                logger.info("dev.restarting")
                _stop(frontend)
                frontend = None
                if api is not None:
                    _stop(api)
                    api = None
                changed_at = None
                api = _start_api(port, workspace)
                frontend = _start_vite(node, web, port)
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        for process in (frontend, api):
            if process is not None:
                _stop(process)
        signal.signal(signal.SIGTERM, previous)
