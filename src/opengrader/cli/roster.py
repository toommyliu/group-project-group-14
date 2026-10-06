import json
from pathlib import Path
import shlex
import sqlite3
from typing import BinaryIO

import click
from pydantic import ValidationError

from opengrader.cli import CONTEXT_SETTINGS
from opengrader.cli.course import display_path
from opengrader.core.workspace import roster
from opengrader.core.workspace.models import RosterImport, Student, StudentDetails
from opengrader.core.workspace.store import (
    DATABASE,
    Workspace,
    WorkspaceConflict,
    WorkspaceNeedsUpgrade,
    WorkspaceNotFound,
    discover_workspace,
)

LABELS = {"sid": "SID", "first_name": "First name", "last_name": "Last name", "email": "Email"}

course_option = click.option(
    "--course",
    "-c",
    type=click.Path(path_type=Path, file_okay=False),
    help="Course folder. Defaults to the course that contains the current folder.",
)


class RosterCommands(click.Group):
    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except BrokenPipeError:
            raise
        except (WorkspaceConflict, WorkspaceNotFound) as exc:
            raise click.ClickException(str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            raise click.ClickException(f"Couldn't read or save the roster: {exc}") from exc


def suggest(subcommand: click.Command, *args: str) -> str:
    ctx = click.get_current_context()
    if ctx.params.get("course") is not None:
        args = (*args, "--course", str(ctx.params["course"]))
    return f"`{ctx.parent.command_path} {shlex.join([subcommand.name, *args])}`"


def open_course(course: Path | None, *, upgrade: bool = True) -> Workspace:
    if course is None:
        root = discover_workspace(Path.cwd())
        if root is None:
            raise click.ClickException(
                "This folder isn't in a course. Run the command in a course folder, or pass --course."
            )
    else:
        root = course.expanduser().resolve()
        if not (root / DATABASE).is_file():
            raise click.ClickException(f"{display_path(root)} isn't a course folder.")
    try:
        return Workspace(root, upgrade=upgrade)
    except WorkspaceNeedsUpgrade as exc:
        raise click.ClickException(
            f"{display_path(root)} needs an upgrade for this version of Opengrader, "
            f"and a dry run doesn't change the course. Run {suggest(list_students)} "
            "to upgrade it, then try the dry run again."
        ) from exc
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


def find_student(workspace: Workspace, sid: str) -> Student:
    try:
        return roster.student_by_sid(workspace, sid)
    except WorkspaceNotFound as exc:
        raise click.ClickException(
            f"{exc} Run {suggest(list_students)} to find the right SID."
        ) from exc


def details(**fields) -> StudentDetails:
    try:
        return StudentDetails.model_validate(fields)
    except ValidationError as exc:
        raise click.UsageError(
            "\n".join(
                (f"--{error['loc'][0].replace('_', '-')}: " if error["loc"] else "")
                + error["msg"]
                for error in exc.errors()
            )
        ) from exc


def fields(student: StudentDetails) -> dict[str, str | None]:
    return student.model_dump(include=set(LABELS))


def changes(before: StudentDetails, after: StudentDetails) -> str:
    old, new = fields(before), fields(after)
    return "; ".join(
        f"{label} {old[key] or '(none)'} -> {new[key] or '(none)'}"
        for key, label in LABELS.items()
        if old[key] != new[key]
    )


def table(students: list[Student]) -> None:
    rows = [("SID", "Last name", "First name", "Email")] + [
        (student.sid, student.last_name, student.first_name, student.email or "")
        for student in students
    ]
    widths = [max(len(row[column]) for row in rows) for column in range(3)]
    for row in rows:
        click.echo(
            "  ".join([*(cell.ljust(width) for cell, width in zip(row, widths)), row[3]]).rstrip()
        )


@click.group("roster", cls=RosterCommands, context_settings=CONTEXT_SETTINGS)
def command():
    """Manage the students in a course.

    Tip: Run this command in a course folder to use that course. Pass --course to choose a different course folder.

    \b
    Examples:
      opengrader roster import roster.csv --course ~/Courses/cs146
      opengrader roster add --sid 012345678 --first-name "Ana María" --last-name "de la Cruz"
    """


@command.command("list")
@course_option
@click.option("--json", "as_json", is_flag=True, help="Print the students as JSON.")
def list_students(course: Path | None, as_json: bool):
    """List the students, sorted by last name."""
    students = roster.students(open_course(course))
    if as_json:
        click.echo(json.dumps([fields(student) for student in students], indent=2))
    elif students:
        table(students)
    else:
        click.echo(
            f"No students yet. Import a roster with {suggest(import_roster, 'FILE')}, "
            f"or add students with {suggest(add)}."
        )


@command.command("add")
@click.option("--sid", required=True, help="Student ID. Leading zeros are kept.")
@click.option("--first-name", default="", help="Given names, including middle names.")
@click.option("--last-name", default="", help="Family names.")
@click.option("--email", help="Optional.")
@course_option
def add(course: Path | None, **values):
    """Add a student."""
    student = details(**values)
    workspace = open_course(course)
    added = roster.add_student(workspace, student)
    click.echo(f"Added {added} to {display_path(workspace.root)}.")


@command.command("edit")
@click.argument("sid")
@click.option("--sid", "new_sid", help="Change the student ID.")
@click.option("--first-name", help="Given names, including middle names.")
@click.option("--last-name", help="Family names.")
@click.option("--email", help='Use --email "" to remove the email.')
@course_option
def edit(sid: str, new_sid: str | None, course: Path | None, **values):
    """Change the details of the student with SID."""
    given = {key: value for key, value in {"sid": new_sid, **values}.items() if value is not None}
    if not given:
        raise click.UsageError(
            "Nothing to change. Pass --first-name, --last-name, --email, or --sid."
        )
    workspace = open_course(course)
    current = find_student(workspace, sid)
    updated = details(**{**fields(current), **given})
    saved = roster.update_student(workspace, current.id, updated, current.revision)
    if saved.revision == current.revision:
        click.echo(f"Unchanged: {saved}.")
    else:
        click.echo(f"Updated {saved}: {changes(current, saved)}.")


@command.command("remove")
@click.argument("sid")
@course_option
def remove(sid: str, course: Path | None):
    """Remove the student with SID."""
    workspace = open_course(course)
    student = find_student(workspace, sid)
    roster.remove_student(workspace, student.id)
    click.echo(f"Removed {student} from {display_path(workspace.root)}.")


def report(result: RosterImport, name: str, root: Path, *, dry_run: bool) -> None:
    added, updated, unchanged = len(result.added), len(result.updated), result.unchanged
    if dry_run:
        click.echo(
            f"{name}: {added} to add, {updated} to update, {unchanged} unchanged. "
            "Nothing was changed."
        )
    else:
        click.echo(
            f"Imported {name} into {display_path(root)}: "
            f"{added} added, {updated} updated, {unchanged} unchanged."
        )
    for update in result.updated:
        click.echo(f"  {update.after}: {changes(update.before, update.after)}")


@command.command("import")
@click.argument("file", type=click.File("rb"))
@click.option(
    "--dry-run",
    is_flag=True,
    help="Check the file and show what would change, without changing the roster.",
)
@click.option("--json", "as_json", is_flag=True, help="Print the result as JSON.")
@course_option
@click.pass_context
def import_roster(
    ctx: click.Context, file: BinaryIO, dry_run: bool, as_json: bool, course: Path | None
):
    """Import students from a CSV file.

    FILE needs First Name, Last Name, and SID columns. Email and Role are
    optional, and every role must be Student. FILE can be - to read standard
    input.

    \b
    Example FILE:
      First Name,Last Name,SID,Email
      Ana María,de la Cruz,012345678,ana@example.edu
      Bo,Chen,012345679,

    Students are matched by SID. New students are added and existing ones are
    updated. Students who aren't in FILE stay on the roster, and a blank
    email never removes a saved one. If any row has a problem, nothing is
    imported.
    """
    workspace = open_course(course, upgrade=not dry_run)
    name = click.format_filename(file.name)
    result = roster.import_roster(
        workspace, file.read(roster.MAX_ROSTER_BYTES + 1), dry_run=dry_run
    )
    if as_json:
        click.echo(result.model_dump_json(exclude={"fingerprint"}, indent=2))
    elif not result.problems:
        report(result, name, workspace.root, dry_run=dry_run)
    if result.problems:
        count = len(result.problems)
        click.echo(
            f"{name} has {count} problem{'s' if count > 1 else ''}, so nothing was imported.",
            err=True,
        )
        if not as_json:
            for problem in result.problems:
                prefix = f"Row {problem.row}: " if problem.row else ""
                click.echo(f"  {prefix}{problem.message}", err=True)
        ctx.exit(1)
