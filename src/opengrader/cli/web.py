"""Open or stop the browser service for the current workspace."""

import os
import sys
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS


def _root(raw: Path | None) -> Path:
    from opengrader.core.workspace.store import discover_workspace

    if raw is not None:
        return raw.expanduser().resolve()
    return discover_workspace(Path.cwd()) or Path.cwd()


_workspace_option = click.option("--workspace", type=click.Path(path_type=Path))


@click.command("web", context_settings=CONTEXT_SETTINGS)
@_workspace_option
@click.option(
    "--no-open",
    is_flag=True,
    help="Print the address without opening a browser.",
)
def open_command(workspace: Path | None, no_open: bool) -> None:
    """Open the browser interface for this workspace."""
    from opengrader.server.workspace.client import ensure_service

    try:
        connection = ensure_service(_root(workspace))
    except (OSError, ValueError, RuntimeError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
    url = connection.url
    print(url)
    if not no_open and not os.environ.get("SSH_CONNECTION"):
        import webbrowser

        webbrowser.open(url)
    else:
        print(
            "This address is local to this host. For SSH access, forward its port to the same port on your computer."
        )


@click.command("stop", context_settings=CONTEXT_SETTINGS)
@_workspace_option
def stop_command(workspace: Path | None) -> None:
    """Stop the local workspace service after its active feedback job finishes."""
    from opengrader.server.workspace.client import WorkspaceClient, running_service

    try:
        connection = running_service(_root(workspace))
        if connection:
            WorkspaceClient(connection).request("POST", "/api/shutdown")
    except (OSError, ValueError, RuntimeError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
