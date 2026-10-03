"""Open or stop the browser service for the current workspace."""

import os
import sys
import time
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS


def _root(raw: Path | None) -> Path:
    from opengrader.core.workspace.store import discover_workspace

    if raw is not None:
        return raw.expanduser().resolve()
    return discover_workspace(Path.cwd()) or Path.cwd()


_workspace_option = click.option("--workspace", type=click.Path(path_type=Path))


@click.command(
    "web",
    context_settings=CONTEXT_SETTINGS,
    help=(
        "Open this workspace in a browser."
    ),
)
@_workspace_option
@click.option(
    "--detach",
    is_flag=True,
    help="Leave the service running after this command exits.",
)
@click.option(
    "--no-open",
    is_flag=True,
    help="Print the address without opening a browser.",
)
def open_command(workspace: Path | None, no_open: bool, detach: bool) -> None:
    from opengrader.server.workspace.__main__ import serve
    from opengrader.server.workspace.client import (
        ServiceConnection,
        ensure_service,
        running_service,
    )

    try:
        root = _root(workspace)
        if detach:
            connection = ensure_service(root)
            click.echo(f"Workspace service is running with PID {connection.pid}.", err=True)
            _open_browser(connection.url, no_open=no_open)
        elif connection := running_service(root):
            _open_browser(connection.url, no_open=no_open)
            click.echo(
                f"A service is already running with PID {connection.pid}. Leaving it running."
            )
        else:
            def ready(connection: ServiceConnection) -> None:
                _open_browser(connection.url, no_open=no_open)

            serve(root, on_ready=ready)
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
    from opengrader.server.workspace.client import (
        WorkspaceClient,
        running_service,
        workspace_free,
    )

    try:
        root = _root(workspace)
        connection = running_service(root)
        if connection:
            WorkspaceClient(connection).request("POST", "/api/shutdown")
            waiting_at = time.monotonic() + 1
            reported_wait = False
            while not workspace_free(root):
                if not reported_wait and time.monotonic() >= waiting_at:
                    click.echo("Waiting for running jobs to finish…", err=True)
                    reported_wait = True
                time.sleep(0.1)
    except (OSError, ValueError, RuntimeError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
