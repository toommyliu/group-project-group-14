"""Explicit workspace creation and registration for local setup."""

from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS
from opengrader.server.registry import WorkspaceRegistry


@click.group(context_settings=CONTEXT_SETTINGS)
def command():
    """Create, register, or list workspaces known to this Opengrader instance."""


@command.command("create")
@click.argument("name")
@click.option(
    "--directory",
    type=click.Path(path_type=Path),
    help="Create in this directory instead of ~/Opengrader/NAME.",
)
def create(name: str, directory: Path | None):
    """Create a named workspace, ready to open in the web app or TUI."""
    try:
        item = WorkspaceRegistry().create(name, directory)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{item.name}\n{item.path}")


@command.command("add")
@click.argument(
    "directory", type=click.Path(path_type=Path, exists=True, file_okay=False)
)
@click.option("--name", help="Name shown in the workspace list.")
def add(directory: Path, name: str | None):
    """Register an existing workspace without moving its files."""
    try:
        item = WorkspaceRegistry().register(directory, name)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{item.name}\n{item.path}")


@command.command("list")
def list_workspaces():
    """List registered names and locations, most recently opened first."""
    for item in WorkspaceRegistry().list():
        click.echo(f"{item.name}\t{item.path}")
