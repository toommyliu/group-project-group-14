"""Compatibility entry point for starting Opengrader with an existing workspace."""

from pathlib import Path
from typing import Callable

import click

from opengrader.server.workspace.client import ServiceConnection


def serve(root: Path, *, port: int = 0, on_ready: Callable[[ServiceConnection], None] | None = None) -> None:
    from opengrader.server.__main__ import serve as serve_server
    from opengrader.server.registry import WorkspaceRegistry

    from opengrader.server.workspace.client import own_workspace
    with own_workspace(root):
        pass
    item = WorkspaceRegistry().register(root)
    serve_server(port=port, on_ready=(lambda server: on_ready(server.workspace(item))) if on_ready else None)


@click.command(help=__doc__)
@click.argument("workspace", type=click.Path(path_type=Path))
def command(workspace: Path) -> None:
    try:
        serve(workspace)
    except (OSError, ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


if __name__ == "__main__":
    command()
