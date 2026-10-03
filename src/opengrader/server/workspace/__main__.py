"""Local workspace service entry point."""

from pathlib import Path
import os
import socket
import sys
from typing import Callable

import click

from opengrader.server.workspace.client import (
    ServiceConnection,
    WorkspaceInUse,
    own_workspace,
)


def serve(
    root: Path,
    *,
    port: int = 0,
    on_ready: Callable[[ServiceConnection], None] | None = None,
) -> None:
    import uvicorn
    from opengrader.server.auth import RemoteAuthGuard
    from opengrader.server.settings import ServerSettings
    from opengrader.server.workspace.app import create_app
    from opengrader.server.workspace.lifecycle import WorkspaceServer

    root = root.resolve()
    with own_workspace(root):
        settings = ServerSettings()
        app = create_app(root, settings=settings)
        if not settings.auth_enabled:
            app.add_middleware(RemoteAuthGuard)
        workspace = app.state.workspace
        record = root / ".opengrader/service.json"
        server = WorkspaceServer(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=0,
                proxy_headers=True,
                forwarded_allow_ips="127.0.0.1",
                timeout_graceful_shutdown=3,
            ),
            record=record,
            on_ready=(lambda: on_ready(connection)) if on_ready else None,
        )
        app.state.shutdown = server.request_shutdown
        address = root / ".opengrader/port"
        if port == 0 and address.exists():
            port = int(address.read_text())
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
            # Browser drafts belong to an origin, so retain the port across restarts.
            temporary = address.with_name("port.tmp")
            temporary.write_text(str(listener.getsockname()[1]))
            temporary.replace(address)
            connection = ServiceConnection(
                root=str(root),
                url=f"http://localhost:{listener.getsockname()[1]}",
                pid=os.getpid(),
                workspace_id=workspace.info().id,
                token=app.state.shutdown_token,
            )
            temporary = record.with_suffix(".tmp")
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
            )
            with os.fdopen(descriptor, "w") as stream:
                stream.write(connection.model_dump_json())
            temporary.replace(record)
            if on_ready is None:
                print(f"Workspace: {root}\nOpen {connection.url}", flush=True)
            try:
                server.run(sockets=[listener])
            finally:
                server.finish_shutdown()
                record.unlink(missing_ok=True)


@click.command(help=__doc__)
@click.argument("workspace", type=click.Path(path_type=Path))
def command(workspace: Path) -> None:
    try:
        serve(workspace)
    except WorkspaceInUse as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)


if __name__ == "__main__":
    command()
