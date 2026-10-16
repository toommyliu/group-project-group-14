"""Run the shared Opengrader server."""

import os
import socket
from collections.abc import Callable
from pathlib import Path

import click

from opengrader.server.client import ServerConnection, own_server
from opengrader.server.home import resolve_data_home


def serve(
    *,
    home: Path | None = None,
    port: int = 0,
    on_ready: Callable[[ServerConnection], None] | None = None,
) -> None:
    import uvicorn

    from opengrader.server.app import create_app
    from opengrader.server.workspace.lifecycle import WorkspaceServer

    home = (home or resolve_data_home()).resolve()
    with own_server(home):
        app = create_app(home)
        record = home / "run/server.json"
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
        address = home / "run/port"
        if port == 0 and address.exists():
            port = int(address.read_text())
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", port))
            temporary = address.with_suffix(".tmp")
            temporary.write_text(str(listener.getsockname()[1]))
            temporary.replace(address)
            connection = ServerConnection(
                home=str(home),
                id=app.state.registry.id,
                url=f"http://localhost:{listener.getsockname()[1]}",
                pid=os.getpid(),
                token=app.state.shutdown_token,
            )
            temporary = record.with_suffix(".tmp")
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
            )
            with os.fdopen(descriptor, "w") as stream:
                stream.write(connection.model_dump_json())
            temporary.replace(record)
            # Shared workspaces tell other accounts this address.
            app.state.workspaces.base_url = connection.url
            if on_ready is None:
                print(f"Open {connection.url}/courses", flush=True)
            try:
                server.run(sockets=[listener])
            finally:
                server.finish_shutdown()
                record.unlink(missing_ok=True)


@click.command(help=__doc__)
@click.option("--home", type=click.Path(path_type=Path))
def command(home: Path | None):
    try:
        serve(home=home)
    except (OSError, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


if __name__ == "__main__":
    command()
