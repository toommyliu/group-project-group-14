"""Open or stop the shared Opengrader server."""

import os
import sys
import time
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS


def _root(raw: Path | None) -> Path | None:
    from opengrader.core.workspace.store import discover_workspace

    if raw is not None:
        from opengrader.core.workspace.store import Workspace
        root = raw.expanduser().resolve()
        Workspace(root)
        return root
    return discover_workspace(Path.cwd())


_workspace_option = click.option("--workspace", type=click.Path(path_type=Path))


@click.command(
    "web",
    context_settings=CONTEXT_SETTINGS,
    help=(
        "Open Opengrader in a browser."
    ),
)
@_workspace_option
@click.option(
    "--detach",
    is_flag=True,
    help="Leave the server running after this command exits.",
)
@click.option(
    "--no-open",
    is_flag=True,
    help="Print the address without opening a browser.",
)
def open_command(workspace: Path | None, no_open: bool, detach: bool) -> None:
    from opengrader.server.__main__ import serve
    from opengrader.server.client import ensure_server, running_server, register_workspace

    try:
        root = _root(workspace)

        def ready(connection):
            selected = register_workspace(connection, root) if root is not None else None
            url = selected.url if selected else f"{connection.url}/courses"
            if selected:
                from opengrader.server.workspace.client import WorkspaceClient
                WorkspaceClient(selected).request("GET", "/api/workspace", timeout=20)
            _open_browser(url, no_open=no_open)

        if detach:
            connection = ensure_server()
            ready(connection)
            click.echo(f"Opengrader is running with PID {connection.pid}. It will keep running after this terminal closes.", err=True)
        elif connection := running_server():
            ready(connection)
            click.echo(f"Opengrader is already running with PID {connection.pid}. Leaving it running.")
        else:
            click.echo("Press Ctrl+C to stop Opengrader for all open workspaces.")
            serve(on_ready=ready)
    except (OSError, ValueError, RuntimeError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)


def _open_browser(url: str, *, no_open: bool) -> None:
    click.echo(f"Open this address in a browser: {url}", err=True)
    if not no_open and not os.environ.get("SSH_CONNECTION"):
        import webbrowser

        try:
            opened = webbrowser.open(url)
        except (OSError, webbrowser.Error):
            opened = False
        if not opened:
            click.echo("Could not open a browser. Open the address above manually.", err=True)


@click.command("stop", context_settings=CONTEXT_SETTINGS)
@_workspace_option
def stop_command(workspace: Path | None) -> None:
    """Stop Opengrader for all workspaces, allowing up to 5 seconds for jobs."""
    from opengrader.server.client import running_server, server_free

    try:
        connection = running_server()
        if connection:
            click.echo("Stopping Opengrader for all open workspaces.")
            connection.request("POST", "/api/shutdown")
            waiting_at = time.monotonic() + 1
            reported_wait = False
            while not server_free():
                if not reported_wait and time.monotonic() >= waiting_at:
                    click.echo("Waiting for running jobs to finish…", err=True)
                    reported_wait = True
                time.sleep(0.1)
    except (OSError, ValueError, RuntimeError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
