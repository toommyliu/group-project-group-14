"""Launch the localhost grading API and web app."""

import sys

from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS


@click.command("serve", context_settings=CONTEXT_SETTINGS)
@click.option(
    "--port",
    type=click.IntRange(0, 65535),
    default=8000,
    help="Local port, default 8000.",
)
@click.option(
    "--dev",
    is_flag=True,
    help="Hot-reload the frontend and restart API and Vite on Python edits.",
)
@click.option(
    "--workspace",
    type=click.Path(path_type=Path),
    help="Workspace directory. Defaults to the current workspace.",
)
def command(port: int, dev: bool, workspace: Path | None) -> None:
    """Run this workspace service on localhost."""
    from opengrader.server.settings import ServerSettings

    try:
        settings = ServerSettings()
    except ValueError as exc:
        raise click.UsageError(str(exc)) from None

    if dev:
        if port == 0:
            raise click.UsageError("--dev requires a fixed, nonzero API port")
        from opengrader.server import logs

        logs.configure(settings.log_level)
        from opengrader.server.dev import run_dev

        try:
            code = run_dev(port, workspace=workspace)
        except (OSError, ValueError) as exc:
            click.echo(f"error: {exc}", err=True)
            sys.exit(2)
        sys.exit(code)

    from opengrader.core.workspace.store import discover_workspace
    from opengrader.server.workspace.__main__ import serve

    try:
        root = workspace or discover_workspace(Path.cwd()) or Path.cwd()
        serve(root, port=port, detached=False)
    except (OSError, ValueError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)


if __name__ == "__main__":
    command()
