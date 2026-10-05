"""Explicit workspace creation and registration for local setup."""

from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS
from opengrader.core.workspace.models import CourseDetails
from opengrader.server.registry import WorkspaceRegistry


@click.group(context_settings=CONTEXT_SETTINGS)
def command():
    """Create, register, or list workspaces known to this Opengrader instance."""


@command.command("create")
@click.argument("number")
@click.argument("name")
@click.option(
    "--directory",
    type=click.Path(path_type=Path),
    help="Create in this directory instead of ~/Opengrader/NUMBER.",
)
def create(number: str, name: str, directory: Path | None):
    """Create a named workspace, ready to open in the web app or TUI."""
    try:
        item = WorkspaceRegistry().create(
            CourseDetails(number=number, name=name), directory
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{item.number}\n{item.path}")


@command.command("add")
@click.argument(
    "directory", type=click.Path(path_type=Path, exists=True, file_okay=False)
)
@click.option("--number", help="Course number shown in the workspace list.")
def add(directory: Path, number: str | None):
    """Register an existing workspace without moving its files."""
    try:
        item = WorkspaceRegistry().register(
            directory, CourseDetails(number=number) if number else None
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{item.number}\n{item.path}")


@command.command("list")
def list_workspaces():
    """List registered names and locations, most recently opened first."""
    for item in WorkspaceRegistry().list():
        click.echo(f"{item.number}\t{item.path}")
