import json
from datetime import date
from pathlib import Path
from typing import get_args

import click
from pydantic import ValidationError

from opengrader.cli import CONTEXT_SETTINGS
from opengrader.core.workspace.models import CourseDetails, Term
from opengrader.core.workspace.store import DATABASE, discover_workspace
from opengrader.server.home import default_workspaces_dir
from opengrader.server.registry import RegisteredWorkspace, WorkspaceRegistry, current_term

dry_run_option = click.option(
    "--dry-run", is_flag=True, help="Print the result without changing anything."
)


def course_options(*, required: bool):
    def decorate(function):
        for option in reversed(
            [
                click.option("--number", required=required, help='Course number, such as "CS 146".'),
                click.option("--name", required=required, help='Course name, such as "Data Structures".'),
                click.option(
                    "--term",
                    type=click.Choice(get_args(Term), case_sensitive=False),
                    help="Defaults to the current term." if required else None,
                ),
                click.option(
                    "--year",
                    type=int,
                    help="Defaults to the current year." if required else None,
                ),
            ]
        ):
            function = option(function)
        return function

    return decorate


def course(**fields) -> CourseDetails:
    try:
        return CourseDetails.model_validate(fields)
    except ValidationError as exc:
        raise click.UsageError(
            "\n".join(f"--{error['loc'][0]}: {error['msg']}" for error in exc.errors())
        ) from exc


def display_path(path: str | Path) -> str:
    path, home = Path(path), Path.home().resolve()
    return str(Path("~") / path.relative_to(home)) if path.is_relative_to(home) else str(path)


def report(action: str, details: CourseDetails, path: str | Path) -> None:
    click.echo(f"{action}: {details} ({display_path(path)})")


def table(items: list[RegisteredWorkspace]) -> None:
    rows = [("Number", "Name", "Term", "Folder")] + [
        (
            item.number,
            item.name or "",
            " ".join(
                str(part) for part in (item.term and item.term.title(), item.year) if part
            ),
            display_path(item.path)
            + ("" if (Path(item.path) / DATABASE).is_file() else " (unavailable)"),
        )
        for item in items
    ]
    widths = [max(len(row[column]) for row in rows) for column in range(3)]
    for row in rows:
        click.echo(
            "  ".join([*(cell.ljust(width) for cell, width in zip(row, widths)), row[3]])
        )


def update(root: Path, changes: dict, *, dry_run: bool) -> None:
    registry = WorkspaceRegistry()
    previous = {item.id: item for item in registry.list()}
    item = registry.register(root, dry_run=dry_run)
    changes = {key: value for key, value in changes.items() if value is not None}
    details = course(**{**item.course.model_dump(), **changes})
    if changes and not dry_run:
        item = registry.update(item.id, details)
    before = previous.get(item.id)
    if before is None:
        return report("Would add" if dry_run else "Added", details, item.path)
    old = {**before.course.model_dump(), "folder": display_path(before.path)}
    new = {**details.model_dump(), "folder": display_path(item.path)}
    changed = [key for key in new if old[key] != new[key]]
    if not changed:
        return report("Unchanged", details, item.path)
    report("Would update" if dry_run else "Updated", details, item.path)
    for key in changed:
        click.echo(f"  {key.capitalize()}: {'(none)' if old[key] is None else old[key]} -> {new[key]}")


@click.group(context_settings=CONTEXT_SETTINGS)
def command():
    """Manage courses."""


@command.command("create")
@course_options(required=True)
@click.option(
    "--directory",
    type=click.Path(path_type=Path),
    help=f"Folder to create the course in. Defaults to {default_workspaces_dir() / 'number-term-year'}.",
)
@dry_run_option
def create(directory: Path | None, dry_run: bool, **fields):
    """Create a course and its folder."""
    term, year = current_term(date.today())
    details = course(**{**fields, "term": fields["term"] or term, "year": fields["year"] or year})
    registry = WorkspaceRegistry()
    try:
        if dry_run:
            return report("Would create", details, registry.destination(details, directory))
        item = registry.create(details, directory)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    report("Created", item, item.path)


@command.command("add")
@click.argument(
    "directory", type=click.Path(path_type=Path, exists=True, file_okay=False)
)
@course_options(required=False)
@dry_run_option
def add(directory: Path, dry_run: bool, **changes):
    """Add an existing course folder."""
    try:
        update(directory, changes, dry_run=dry_run)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@command.command("edit")
@click.argument(
    "directory",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    default=".",
)
@course_options(required=False)
@dry_run_option
def edit(directory: Path, dry_run: bool, **changes):
    """Change a course's details.

    DIRECTORY defaults to the current folder.
    """
    root = discover_workspace(directory)
    if root is None:
        raise click.ClickException(f"No course folder at or above {directory}.")
    try:
        update(root, changes, dry_run=dry_run)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@command.command("remove")
@click.argument("directory", type=click.Path(path_type=Path), default=".")
@dry_run_option
def remove(directory: Path, dry_run: bool):
    """Remove a course without deleting its folder.

    DIRECTORY defaults to the current folder. It can be a folder that no longer
    exists.
    """
    root = (directory.exists() and discover_workspace(directory)) or directory.resolve()
    registry = WorkspaceRegistry()
    item = next((item for item in registry.list() if item.path == str(root)), None)
    if item is None:
        raise click.ClickException(f"No registered course at {root}.")
    if not dry_run:
        registry.remove(item.id)
    report("Would remove" if dry_run else "Removed", item, item.path)


@command.command("list")
@click.option(
    "--term",
    type=click.Choice(get_args(Term), case_sensitive=False),
    help="Only list courses in this term.",
)
@click.option("--year", type=int, help="Only list courses in this year.")
@click.option("--json", "as_json", is_flag=True, help="Print the courses as JSON.")
def list_courses(term: Term | None, year: int | None, as_json: bool):
    """List courses, most recently opened first."""
    items = [
        item
        for item in WorkspaceRegistry().list()
        if term in (None, item.term) and year in (None, item.year)
    ]
    if as_json:
        click.echo(
            json.dumps(
                [
                    {**item.model_dump(), "available": (Path(item.path) / DATABASE).is_file()}
                    for item in items
                ],
                indent=2,
            )
        )
    elif items:
        table(items)
    elif term or year:
        click.echo("No courses match.")
    else:
        click.echo("No courses yet. Create one with `opengrader course create`.")
