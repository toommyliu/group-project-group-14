"""Discover and start the single server for an Opengrader instance."""

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel

from opengrader.server.home import resolve_data_home
from opengrader.server.registry import RegisteredWorkspace, WorkspaceRegistry
from opengrader.server.transport import request
from opengrader.server.workspace.client import (
    ServiceConnection,
    ServiceError,
    _last_log_line,
    exclusive_lock,
)


class ServerConnection(BaseModel):
    home: str
    id: str
    url: str
    pid: int
    token: str

    def request(
        self,
        method: str,
        path: str,
        body: BaseModel | None = None,
        *,
        timeout: float = 10,
    ) -> bytes:
        return request(self.url, self.token, method, path, body, timeout=timeout)

    def workspace(self, item: RegisteredWorkspace) -> ServiceConnection:
        return ServiceConnection(
            root=item.path,
            url=self.url + item.url_path,
            pid=self.pid,
            workspace_id=item.id,
            token=self.token,
            server_url=self.url,
        )


class ServerInUse(RuntimeError):
    pass


@contextmanager
def own_server(home: Path) -> Iterator[None]:
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    (home / "run").mkdir(exist_ok=True, mode=0o700)
    try:
        with exclusive_lock(home / "run/server.lock"):
            yield
    except BlockingIOError:
        raise ServerInUse(
            "Opengrader is already running for this server home. Open it with 'opengrader web'."
        ) from None


def server_free(home: Path | None = None) -> bool:
    try:
        with own_server(home or resolve_data_home()):
            return True
    except ServerInUse:
        return False


def running_server(home: Path | None = None) -> ServerConnection | None:
    home = (home or resolve_data_home()).resolve()
    try:
        record = ServerConnection.model_validate_json(
            (home / "run/server.json").read_bytes()
        )
        if record.home != str(home):
            return None
        import json

        info = json.loads(record.request("GET", "/api/server", timeout=0.5))
        return record if info["id"] == record.id else None
    except (OSError, ValueError, KeyError, ServiceError):
        return None


def register_workspace(connection: ServerConnection, root: Path) -> ServiceConnection:
    class Registration(BaseModel):
        path: str

    item = RegisteredWorkspace.model_validate_json(
        connection.request(
            "POST",
            "/api/server/workspaces/register",
            Registration(path=str(root.resolve())),
        )
    )
    return connection.workspace(item)


def ensure_server(home: Path | None = None) -> ServerConnection:
    home = (home or resolve_data_home()).resolve()
    WorkspaceRegistry(home)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if active := running_server(home):
            return active
        try:
            with exclusive_lock(home / "run/startup.lock"):
                if active := running_server(home):
                    return active
                # A foreground launcher may own the server lock before its
                # socket is ready, without holding the detached startup lock.
                if not server_free(home):
                    time.sleep(0.1)
                    continue
                log_path = home / "logs/server.log"
                log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                log_start = log_path.stat().st_size if log_path.exists() else 0
                with log_path.open("ab") as log:
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "opengrader.server",
                            "--home",
                            str(home),
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
                    if active := running_server(home):
                        return active
                    if process.poll() is not None:
                        reason = _last_log_line(log_path, log_start)
                        raise RuntimeError(
                            f"Opengrader could not start: {reason or 'see server log'}. Check {log_path}."
                        )
                    time.sleep(0.1)
                process.terminate()
                process.wait(timeout=7)
                break
        except BlockingIOError:
            time.sleep(0.1)
    raise RuntimeError(
        f"Opengrader did not become ready. Check {home / 'logs/server.log'}."
    )
