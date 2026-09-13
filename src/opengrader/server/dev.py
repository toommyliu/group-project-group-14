"""Run the local API and Vite app together for frontend development."""

import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time


def _stop(process: subprocess.Popen) -> None:
    # Vite can spawn workers; stop the whole session so they do not outlive us.
    def send(sig: signal.Signals) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, sig)
            elif process.poll() is None:
                process.terminate()
        except ProcessLookupError:
            pass

    send(signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            send(signal.SIGKILL)
        else:
            process.kill()
        process.wait()


def run_dev(port: int) -> int:
    """Supervise both servers and clean up on interruption or either child exiting."""
    web = Path(__file__).resolve().parents[1] / "web"
    vite = web / "node_modules" / "vite" / "bin" / "vite.js"
    node = shutil.which("node")
    if node is None or not vite.is_file():
        raise ValueError(
            "Development mode requires Node.js and the web dependencies. "
            "Install Node.js, then run "
            "'pnpm --dir src/opengrader/web install --frozen-lockfile'."
        )

    processes: list[subprocess.Popen] = []

    def interrupt(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        processes.append(
            subprocess.Popen(
                [sys.executable, "-m", "opengrader.cli.serve", "--port", str(port)],
                start_new_session=os.name == "posix",
            )
        )
        processes.append(
            subprocess.Popen(
                [node, str(vite), "--host", "127.0.0.1"],
                cwd=web,
                env={**os.environ, "OPENGRADER_API_URL": f"http://127.0.0.1:{port}"},
                start_new_session=os.name == "posix",
            )
        )
        while True:
            for name, process in zip(("API", "Vite"), processes):
                code = process.poll()
                if code is not None:
                    print(
                        f"{name} exited with status {code}; stopping development servers.",
                        file=sys.stderr,
                    )
                    return code if code > 0 else 1
            time.sleep(0.2)
    except KeyboardInterrupt:
        return 0
    finally:
        for process in reversed(processes):
            _stop(process)
        signal.signal(signal.SIGTERM, previous)
