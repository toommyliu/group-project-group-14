"""Launch the localhost grading API and web app."""

from copy import deepcopy
import locale
import logging.config
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
        ServerSettings()
    except ValueError as exc:
        raise click.UsageError(str(exc)) from None

    from uvicorn.config import LOGGING_CONFIG

    try:
        locale.setlocale(locale.LC_TIME, "")
    except locale.Error:
        # An unavailable shell locale should not prevent the server from starting.
        pass

    log_config = deepcopy(LOGGING_CONFIG)
    for formatter in log_config["formatters"].values():
        formatter["fmt"] = "%(asctime)s " + formatter["fmt"]
        formatter["datefmt"] = "%X"
    log_config["formatters"]["access"]["()"] = (
        "opengrader.server.request_logging.TimedAccessFormatter"
    )
    log_config["loggers"]["opengrader"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    logging.config.dictConfig(log_config)

    from opengrader.core.workspace.store import discover_workspace
    from opengrader.server.workspace.client import WorkspaceInUse

    root = workspace or discover_workspace(Path.cwd()) or Path.cwd()
    if dev:
        if port == 0:
            raise click.UsageError("--dev requires a fixed, nonzero API port")
        from opengrader.server.dev import run_dev

        try:
            code = run_dev(port, root)
        except (OSError, ValueError, WorkspaceInUse) as exc:
            click.echo(f"error: {exc}", err=True)
            sys.exit(2)
        sys.exit(code)

    from opengrader.server.workspace.__main__ import serve

    try:
        serve(root, port=port)
    except (OSError, ValueError, WorkspaceInUse) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)


if __name__ == "__main__":
    command()
