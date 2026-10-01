"""Local workspace connections shared by terminal entry points."""

from contextlib import contextmanager
from http.client import HTTPException
import json
import errno
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from threading import Event, Thread
from typing import Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel

from opengrader.core.workspace.models import Answer, WorkspaceEvent, WorkspaceInfo

logger = logging.getLogger(__name__)


class ServiceConnection(BaseModel):
    root: str
    url: str
    pid: int
    workspace_id: str
    # service.json from a server started before tokens existed has none.
    token: str = ""


class ServiceError(RuntimeError):
    def __init__(self, status: int, message: str, current: Answer | None = None):
        super().__init__(message)
        self.status = status
        self.current = current


class WorkspaceClient:
    def __init__(self, connection: ServiceConnection):
        self.connection = connection

    def request(
        self,
        method: str,
        path: str,
        body: BaseModel | None = None,
        *,
        timeout: float = 10,
    ) -> bytes:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Expected a workspace API path.")
        headers = {"Content-Type": "application/json"}
        if self.connection.token:
            headers["Authorization"] = f"Bearer {self.connection.token}"
        request = Request(
            self.connection.url + path,
            data=body.model_dump_json().encode() if body else None,
            method=method,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            try:
                data = json.loads(exc.read())
            except ValueError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            current = (
                Answer.model_validate(data["current"]) if data.get("current") else None
            )
            raise ServiceError(
                exc.code, str(data.get("detail", "Workspace request failed.")), current
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ServiceError(
                0,
                "Could not reach the workspace service. Your pending edits are preserved; reconnect and retry.",
            ) from exc

    def observe(
        self, after: int, callback: Callable[[WorkspaceEvent], None]
    ) -> Callable[[], None]:
        """Resume committed events after reconnecting; heartbeats bound dead connections."""
        stopped = Event()

        def run() -> None:
            cursor = after
            while not stopped.is_set():
                try:
                    with urlopen(
                        f"{self.connection.url}/api/events?after={cursor}", timeout=20
                    ) as stream:
                        for line in stream:
                            if stopped.is_set():
                                return
                            if line.startswith(b"data: "):
                                event = WorkspaceEvent.model_validate_json(line[6:])
                                callback(event)
                                cursor = event.cursor
                except (HTTPException, OSError, ValueError, RuntimeError) as exc:
                    logger.debug("Workspace event stream interrupted: %s", exc)
                if stopped.wait(1):
                    return

        Thread(target=run, name="workspace-events", daemon=True).start()
        return stopped.set


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    raise BlockingIOError(
                        errno.EAGAIN, "Another client holds this lock."
                    ) from exc
                raise
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if sys.platform == "win32":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def running_service(root: Path) -> ServiceConnection | None:
    try:
        record = ServiceConnection.model_validate_json(
            (root / ".opengrader/service.json").read_bytes()
        )
        if record.root != str(root.resolve()):
            return None
        info = WorkspaceInfo.model_validate_json(
            WorkspaceClient(record).request("GET", "/api/workspace", timeout=0.5)
        )
        return record if info.id == record.workspace_id else None
    except (OSError, ValueError, ServiceError):
        return None


def _last_log_line(path: Path, start: int = 0, *, limit: int = 200) -> str:
    """The service's final word before it exited — a traceback ends with the cause.

    The log is appended to across starts; `start` keeps an earlier run's
    failure from being blamed for this one.
    """
    try:
        with path.open("rb") as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(log.tell() - 4096, start, 0))
            lines = log.read().decode(errors="replace").splitlines()
    except OSError:
        return ""
    return next((line.strip()[:limit] for line in reversed(lines) if line.strip()), "")


def ensure_service(root: Path) -> ServiceConnection:
    """Attach to one service, or start it detached from the terminal lifetime."""
    root = root.expanduser().resolve()
    from opengrader.core.workspace.store import Workspace

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        active = running_service(root)
        if active:
            return active
        try:
            with exclusive_lock(root / ".opengrader/startup.lock"):
                active = running_service(root)
                if active:
                    return active
                from opengrader.core.workspace.store import DATABASE

                if not (root / DATABASE).exists():
                    Workspace.create(root)
                log_path = root / ".opengrader/service.log"
                log_start = log_path.stat().st_size if log_path.exists() else 0
                with log_path.open("ab") as log:
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "opengrader.server.workspace",
                            str(root),
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=log,
                        start_new_session=os.name != "nt",
                        creationflags=(
                            subprocess.CREATE_NEW_PROCESS_GROUP
                            | subprocess.DETACHED_PROCESS
                        )
                        if os.name == "nt"
                        else 0,
                    )
                while time.monotonic() < deadline:
                    active = running_service(root)
                    if active:
                        return active
                    if process.poll() is not None:
                        reason = _last_log_line(log_path, log_start)
                        raise RuntimeError(
                            "The workspace service could not start"
                            + (f" ({reason})" if reason else "")
                            + f". Check {log_path}."
                        )
                    time.sleep(0.1)
                process.terminate()
                process.wait(timeout=5)
                break
        except BlockingIOError:
            time.sleep(0.1)
    raise RuntimeError(
        "The workspace service did not become ready. Check .opengrader/service.log."
    )
