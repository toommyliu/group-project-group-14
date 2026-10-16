"""Local workspace connections shared by terminal entry points."""

from contextlib import ExitStack, contextmanager
from http.client import HTTPException
import errno
import logging
import os
from pathlib import Path
import sys
import time
from threading import Event, Thread
from typing import Callable, Iterator
from urllib.request import Request, urlopen

from pydantic import BaseModel

from opengrader.core.workspace.models import WorkspaceEvent, WorkspaceInfo
from opengrader.server.transport import ACCOUNT_HEADER, ServiceError, request

logger = logging.getLogger(__name__)
SERVICE_LOCK = Path(".opengrader/service.lock")


class ServiceConnection(BaseModel):
    root: str
    url: str
    pid: int
    workspace_id: str
    # service.json from a server started before tokens existed has none.
    token: str = ""
    server_url: str = ""
    # Set when this account joined a workspace another account owns
    # (`join_workspace`): it is sent with every request, and nothing of the
    # workspace's own folder is written from this side.
    account: str = ""
    owner: str = ""


class WorkspaceInUse(RuntimeError):
    def __init__(self, owner: ServiceConnection | None):
        message = "Another process is using this workspace"
        if owner is not None:
            message += f" (pid {owner.pid}, {owner.url})"
        super().__init__(message + ".")




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
        base = self.connection.server_url if path == "/api/shutdown" and self.connection.server_url else self.connection.url
        return request(
            base, self.connection.token, method, path, body, timeout=timeout,
            account=self.connection.account,
        )

    def observe(
        self, after: int, callback: Callable[[WorkspaceEvent], None]
    ) -> Callable[[], None]:
        """Resume committed events after reconnecting; heartbeats bound dead connections."""
        stopped = Event()

        def run() -> None:
            cursor = after
            while not stopped.is_set():
                try:
                    headers = {"Authorization": f"Bearer {self.connection.token}"}
                    if self.connection.account:
                        headers[ACCOUNT_HEADER] = self.connection.account
                    with urlopen(
                        Request(f"{self.connection.url}/api/events?after={cursor}", headers=headers),
                        timeout=20,
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


@contextmanager
def own_workspace(root: Path) -> Iterator[None]:
    with ExitStack() as stack:
        try:
            stack.enter_context(exclusive_lock(root / SERVICE_LOCK))
        except BlockingIOError:
            from opengrader.server.client import running_server
            from opengrader.server.registry import WorkspaceRegistry

            server = running_server()
            owner = None
            if server is not None:
                item = next((item for item in WorkspaceRegistry(Path(server.home)).list() if item.path == str(root.resolve())), None)
                if item is not None:
                    owner = server.workspace(item)
            raise WorkspaceInUse(owner) from None
        yield


def workspace_free(root: Path) -> bool:
    try:
        with exclusive_lock(root / SERVICE_LOCK):
            return True
    except BlockingIOError:
        return False


def running_service(root: Path) -> ServiceConnection | None:
    from opengrader.server.client import running_server
    from opengrader.server.registry import WorkspaceRegistry

    active = running_server()
    if active is None:
        return None
    root = root.expanduser().resolve()
    try:
        item = next((item for item in WorkspaceRegistry(Path(active.home)).list() if item.path == str(root)), None)
        if item is None:
            return None
        connection = active.workspace(item)
        info = WorkspaceInfo.model_validate_json(
            WorkspaceClient(connection).request("GET", "/api/workspace", timeout=0.5)
        )
        return connection if info.id == item.id else None
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
    """Compatibility for explicit folder/pipeline entry points.

    Those workflows already create grading data in their selected folder.
    Browser and server startup never call this to create a workspace.
    """
    from opengrader.core.workspace.store import DATABASE, Workspace
    from opengrader.server.client import ensure_server, register_workspace

    root = root.expanduser().resolve()
    deadline = time.monotonic() + 20
    while True:
        try:
            with exclusive_lock(root / ".opengrader/startup.lock"):
                if not (root / DATABASE).exists():
                    Workspace.create(root)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise RuntimeError("Another client is still preparing this workspace. Try again.") from None
            time.sleep(0.1)
    connection = register_workspace(ensure_server(), root)
    # Activate the workspace before callers publish or subscribe to it.
    WorkspaceClient(connection).request("GET", "/api/workspace", timeout=20)
    return connection


class WorkspaceNotShared(RuntimeError):
    """This account cannot join the workspace another account owns; the
    message says why and what to ask for."""


CONNECTION_NOTE = Path(".opengrader/connection.json")


def owned_by_another_account(root: Path) -> bool:
    from opengrader.core.workspace.store import DATABASE

    getuid = getattr(os, "getuid", None)
    if getuid is None:  # Windows: one account per computer, as before
        return False
    try:
        return (root / DATABASE).stat().st_uid != getuid()
    except OSError:
        return False


def join_workspace(root: Path) -> ServiceConnection:
    """Connect to the service of the account that owns this workspace,
    through the note its server leaves for the group it is shared with.
    Nothing in the workspace folder is written from this side."""
    import json

    from opengrader.core.folder_access import current_account, owner_of
    from opengrader.core.workspace.store import DATABASE

    root = root.expanduser().resolve()
    owner = owner_of(root / DATABASE) or "another account"
    me = current_account()
    note = root / CONNECTION_NOTE
    try:
        data = json.loads(note.read_text())
    except FileNotFoundError:
        raise WorkspaceNotShared(
            f"This workspace belongs to {owner} and is not open to other accounts "
            f"right now. Ask {owner} to share it (opengrader share --group <group> "
            f"{root}) and to keep Opengrader running for it (opengrader tui {root} "
            "starts it)."
        ) from None
    except PermissionError:
        raise WorkspaceNotShared(
            f"This workspace belongs to {owner}, who shares it with a group your "
            f"account ({me}) is not in. Ask {owner} to add you to it; then log "
            "out and in again."
        ) from None
    except (OSError, ValueError) as exc:
        raise WorkspaceNotShared(f"The note for joining {owner}'s workspace cannot be read: {exc}") from None
    try:
        connection = ServiceConnection(
            root=str(root),
            url=str(data["url"]),
            pid=int(data["pid"]),
            workspace_id=str(data["workspace_id"]),
            token=str(data["token"]),
            account=me,
            owner=str(data.get("owner") or owner),
        )
    except (KeyError, TypeError, ValueError):
        raise WorkspaceNotShared(f"The note for joining {owner}'s workspace is damaged; ask {owner} to open it again.") from None
    try:
        WorkspaceClient(connection).request("GET", "/api/me", timeout=5)
    except ServiceError as exc:
        if exc.status == 0:
            raise WorkspaceNotShared(
                f"{connection.owner}'s Opengrader is not running, so their workspace "
                f"cannot be joined now. Ask {connection.owner} to start it "
                f"(opengrader tui {root})."
            ) from None
        raise WorkspaceNotShared(str(exc)) from None
    return connection


def connect_workspace(root: Path) -> ServiceConnection:
    """This account's own service for a workspace it owns; the owner's, joined,
    for one another account owns."""
    if owned_by_another_account(root.expanduser().resolve()):
        return join_workspace(root)
    return ensure_service(root)
