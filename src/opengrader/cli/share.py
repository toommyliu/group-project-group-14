"""Share a workspace with other accounts on this computer.

For a class graded on a machine several people reach over SSH, each with an
account of their own. The professor's account owns the workspace and runs
its service; graders join that service with `opengrader tui <folder>` from
their own accounts, so only the professor's account ever writes the files.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS

EPILOG = """\b
Examples:
  opengrader share --group cmpe195-graders     # graders in that group may join
  opengrader share --instructor alice          # alice may do what you can
  opengrader share --trust bob                 # bob may change others' grades
  opengrader share --off                       # nobody else may join
  opengrader share                             # who may join, and how
"""


@click.command("share", context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.argument("folder", required=False, type=click.Path(file_okay=False, path_type=Path))
@click.option("--group", help="Let members of this group join (they run opengrader tui FOLDER).")
@click.option("--off", is_flag=True, help="Stop sharing: nobody else may join.")
@click.option("--instructor", "instructors", multiple=True, metavar="LOGIN", help="Make an account an instructor here.")
@click.option("--grader", "graders", multiple=True, metavar="LOGIN", help="Make an account a grader again.")
@click.option("--trust", multiple=True, metavar="LOGIN", help="Let a grader change grades others gave, without asking.")
@click.option("--untrust", multiple=True, metavar="LOGIN", help="Make a grader ask again before changing others' grades.")
def command(
    folder: Path | None,
    group: str | None,
    off: bool,
    instructors: tuple[str, ...],
    graders: tuple[str, ...],
    trust: tuple[str, ...],
    untrust: tuple[str, ...],
) -> None:
    """Share the workspace around FOLDER (default: here) with other accounts
    on this computer, and say who is an instructor.

    Graders give grades where there are none and change their own; a change
    to a grade someone else gave is sent to you to approve (Shift+Q in the
    app). Graders join while your Opengrader runs: it keeps running in the
    background after `opengrader tui` and stops with `opengrader stop`.
    """
    from opengrader.core.folder_access import owner_of
    from opengrader.core.workspace.models import AccountRole, SharingInfo, SharingUpdate
    from opengrader.core.workspace.store import DATABASE, discover_workspace
    from opengrader.server.transport import ServiceError
    from opengrader.server.workspace.client import (
        WorkspaceClient,
        ensure_service,
        owned_by_another_account,
    )

    if group and off:
        raise click.UsageError("give --group or --off, not both")
    root = discover_workspace((folder or Path.cwd()).expanduser().resolve())
    if root is None:
        raise click.UsageError("No workspace here: open the folder with opengrader tui first.")
    if owned_by_another_account(root):
        raise click.UsageError(
            f"Only {owner_of(root / DATABASE) or 'its owner'}, who owns this workspace, can share it."
        )
    client = WorkspaceClient(ensure_service(root))
    try:
        info = SharingInfo.model_validate_json(client.request("GET", "/api/sharing"))
        known = {account.login: account for account in info.accounts}

        def account(login: str) -> AccountRole:
            return known.get(login) or AccountRole(login=login, role="grader")

        changes: dict[str, AccountRole] = {}
        for login in instructors:
            changes[login] = account(login).model_copy(update={"role": "instructor"})
        for login in graders:
            changes[login] = account(login).model_copy(update={"role": "grader"})
        for login in trust:
            changes[login] = changes.get(login, account(login)).model_copy(update={"may_change_others": True})
        for login in untrust:
            changes[login] = changes.get(login, account(login)).model_copy(update={"may_change_others": False})
        if group or off or changes:
            info = SharingInfo.model_validate_json(
                client.request(
                    "PUT",
                    "/api/sharing",
                    SharingUpdate(group=group, off=off, accounts=list(changes.values())),
                )
            )
    except ServiceError as exc:
        click.echo(f"Not changed: {exc}", err=True)
        sys.exit(2)

    click.echo(f"Workspace: {root}")
    click.echo(f"Owner: {info.owner} (instructor)")
    if info.group:
        click.echo(f"Shared with: members of {info.group}")
        click.echo(f"They join from their own accounts with: opengrader tui {root}")
    else:
        click.echo("Shared with: nobody (opengrader share --group <group> to share)")
    for item in info.accounts:
        extra = ", may change others' grades" if item.role == "grader" and item.may_change_others else ""
        click.echo(f"  {item.login}: {item.role}{extra}")
    if info.group and not info.accounts:
        click.echo("  Everyone else who joins is a grader.")
