"""Workspace server readiness and bounded shutdown."""

from contextlib import contextmanager
from multiprocessing import active_children
import os
from pathlib import Path
import signal
import socket
from threading import RLock, Timer, current_thread, main_thread
from types import FrameType
from typing import Callable, Iterator

from starlette.concurrency import run_in_threadpool
import uvicorn
from uvicorn.server import HANDLED_SIGNALS

SHUTDOWN_SECONDS = 5


class WorkspaceServer(uvicorn.Server):
    def __init__(
        self,
        config: uvicorn.Config,
        *,
        record: Path,
        on_ready: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(config)
        self.record = record
        self.on_ready = on_ready
        self._shutdown_lock = RLock()
        self._shutdown_timer: Timer | None = None
        self._closed = False

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if self.started and self.on_ready is not None:
            # Browser launchers may block; keep the API responsive meanwhile.
            await run_in_threadpool(self.on_ready)

    def request_shutdown(self) -> None:
        with self._shutdown_lock:
            if self._closed:
                return
            self.should_exit = True
            if self._shutdown_timer is None:
                self._shutdown_timer = Timer(SHUTDOWN_SECONDS, self._force_shutdown)
                self._shutdown_timer.daemon = True
                self._shutdown_timer.start()
                _notice("Stopping service. Waiting up to 5 seconds for running jobs; press Ctrl+C again to force.")

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        if sig == signal.SIGINT and self._shutdown_timer is not None:
            self._force_shutdown()
        else:
            self.request_shutdown()

    def finish_shutdown(self) -> None:
        with self._shutdown_lock:
            self._closed = True
            if self._shutdown_timer is not None:
                self._shutdown_timer.cancel()

    def _force_shutdown(self) -> None:
        import psutil

        with self._shutdown_lock:
            if self._closed:
                return
            _notice("Forcing shutdown. Interrupted jobs may need a retry.")
            try:
                # Only kill multiprocessing workers and their children. A browser
                # launched by webbrowser may also be a child of this service.
                for worker in active_children():
                    try:
                        process = psutil.Process(worker.pid)
                        descendants = process.children(recursive=True)
                        for child in [*reversed(descendants), process]:
                            try:
                                child.kill()
                            except psutil.NoSuchProcess:
                                pass
                    except psutil.Error:
                        continue
                self.record.unlink(missing_ok=True)
            finally:
                # Executor and asyncio shutdown can wait indefinitely for stuck
                # threads. The deadline covers those too.
                os._exit(1)

    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        if current_thread() is not main_thread():
            yield
            return
        handled = HANDLED_SIGNALS
        if hasattr(signal, "SIGHUP"):
            handled = (*handled, signal.SIGHUP)
        previous = {sig: signal.signal(sig, self.handle_exit) for sig in handled}
        try:
            yield
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        # Return normally so serve() can remove the record and release its lock.


def _notice(message: str) -> None:
    # Signal handlers must not reenter a buffered Python stderr writer.
    try:
        os.write(2, (message + "\n").encode())
    except OSError:
        pass
