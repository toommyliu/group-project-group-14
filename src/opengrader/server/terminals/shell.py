"""A POSIX shell whose prompt explicitly identifies safe idle periods."""

import asyncio
import os
import shutil
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import psutil


class ShellProcess:
    def __init__(
        self, cwd: str, directory: Path, output: Callable[[bytes], None]
    ) -> None:
        self.cwd = cwd
        self.directory = directory
        self.output = output
        self.process: asyncio.subprocess.Process | None = None
        self.fd: int | None = None
        self.at_prompt = False
        self.nonce = uuid4().hex
        self.marker = f"\x1b]777;{self.nonce};".encode()
        self.pending = b""
        self.done = asyncio.Event()
        self.write_lock = asyncio.Lock()

    async def start(self, cols: int, rows: int) -> None:
        if os.name != "posix":
            raise ValueError("Browser shells require a Linux or macOS server.")
        import pty
        import termios

        bash = shutil.which("bash")
        if bash is None:
            raise ValueError("Install bash on the server to start a terminal.")
        self.directory.mkdir(parents=True, exist_ok=True)
        rc = self.directory / "bashrc"
        # A known prompt is necessary: a silent process is not evidence of idleness.
        rc.write_text(
            "PS1='\\u@\\h:\\w\\$ '\n"
            f'PROMPT_COMMAND=\'printf "\\033]777;{self.nonce};%s\\007" "$PWD"\'\n'
            f"trap 'printf \"\\033]777;{self.nonce};busy\\007\"' DEBUG\n"
        )
        master, slave = pty.openpty()
        try:
            termios.tcsetwinsize(slave, (rows, cols))
            # The small exec helper acquires the controlling TTY after setsid.
            # Python code never runs between fork and exec in this threaded server.
            self.process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "opengrader.server.terminals.shell_exec",
                bash,
                "--noprofile",
                "--rcfile",
                str(rc),
                "-i",
                cwd=self.cwd,
                env={**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor"},
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
            )
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        self.fd = master
        os.set_blocking(master, False)
        asyncio.get_running_loop().add_reader(master, self._read)

    def _read(self) -> None:
        if self.fd is None:
            return
        try:
            data = os.read(self.fd, 16384)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            asyncio.get_running_loop().remove_reader(self.fd)
            self.done.set()
            return
        self.pending += data
        while self.pending:
            start = self.pending.find(self.marker)
            if start < 0:
                # Retain a possible marker prefix split across reads.
                keep = next(
                    (
                        n
                        for n in range(
                            min(len(self.marker) - 1, len(self.pending)), 0, -1
                        )
                        if self.pending.endswith(self.marker[:n])
                    ),
                    0,
                )
                visible = self.pending[:-keep] if keep else self.pending
                if visible:
                    self.output(visible)
                self.pending = self.pending[-keep:] if keep else b""
                break
            if start:
                self.output(self.pending[:start])
                self.pending = self.pending[start:]
            end = self.pending.find(b"\x07", len(self.marker))
            if end < 0:
                if len(self.pending) > 16384:
                    self.output(self.pending)
                    self.pending = b""
                break
            payload = self.pending[len(self.marker) : end]
            self.at_prompt = payload != b"busy"
            if self.at_prompt:
                self.cwd = payload.decode("utf-8", "replace")
            self.pending = self.pending[end + 1 :]

    async def write(self, data: bytes) -> None:
        self.at_prompt = False
        async with self.write_lock:
            remaining = memoryview(data)
            while remaining:
                if self.process is None or self.fd is None or self.done.is_set():
                    raise BrokenPipeError("Session has stopped.")
                try:
                    count = os.write(self.fd, remaining)
                    remaining = remaining[count:]
                except BlockingIOError:
                    await asyncio.sleep(0.01)

    async def resize(self, cols: int, rows: int) -> None:
        import termios

        if self.fd is not None:
            termios.tcsetwinsize(self.fd, (rows, cols))

    async def park(self) -> bool:
        if self.process is None or not self.at_prompt or self.done.is_set():
            return False
        try:
            if psutil.Process(self.process.pid).children(recursive=True):
                return False
        except psutil.Error:
            return False
        return True

    async def stop(self) -> None:
        if self.process is None:
            return
        try:
            descendants = psutil.Process(self.process.pid).children(recursive=True)
        except psutil.Error:
            descendants = []
        if self.fd is not None:
            asyncio.get_running_loop().remove_reader(self.fd)
            os.close(self.fd)
            self.fd = None
        for child in descendants:
            try:
                child.terminate()
            except psutil.Error:
                pass
        process, self.process = self.process, None
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 1)
            except TimeoutError:
                process.kill()
                await process.wait()
        for child in descendants:
            try:
                if child.is_running():
                    child.send_signal(signal.SIGKILL)
            except psutil.Error:
                pass
        self.done.set()
