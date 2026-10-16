"""Join two submissions the splitter cut apart, after they were read.

The terminal app's `X` does the same from the grade screen. This command is
for when the app cannot ask what it needs to (a cloud reading needs a yes
typed here), or for a folder nobody has open.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS

EPILOG = """\b
Example (a six-page paper split 5 + 1 as submission-0001 and submission-0002):
  uv run merge-submissions submission-0001 submission-0002 --folder classes/quiz3
"""


def _submissions_dir(folder: Path) -> Path:
    from opengrader.core.submissions.manifest import resolve_manifest_path

    for candidate in (folder / "submissions", folder):
        if resolve_manifest_path(candidate).is_file():
            return candidate
    raise click.UsageError(
        f"No read submissions found in {folder}: give the class folder the "
        "pipeline wrote (it holds split/ and submissions/)."
    )


def _workspace_assignment(submissions: Path) -> tuple[Path, str] | None:
    """The workspace assignment published from this submissions folder, if any."""
    from opengrader.core.workspace.store import DATABASE, Workspace, discover_workspace
    from opengrader.core.workspace.writeback import locate_submissions_dir

    root = discover_workspace(submissions)
    if root is None or not (root / DATABASE).is_file():
        return None
    workspace = Workspace(root)
    for assignment in workspace.assignments():
        folder = locate_submissions_dir(workspace, assignment.id)
        if folder is not None and folder.resolve() == submissions.resolve():
            return root, assignment.id
    return None


@click.command("merge-submissions", context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.argument("first")
@click.argument("second")
@click.option(
    "--folder",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="The class folder (holding split/ and submissions/), or its submissions folder.",
)
def command(first: str, second: str, folder: Path) -> None:
    """Join SECOND onto FIRST: two pieces of one paper the splitter cut apart
    (FIRST scanned just before SECOND, e.g. submission-0001 submission-0002).

    The two split PDFs become one, the paper is read again with the command
    the pipeline used, and every grade given while its answer was on screen
    is kept, with every reading a person corrected. A question marked wrong
    because its page was in the other piece starts again. In a workspace both submissions are replaced by the joined
    one, which gets a new ID.
    """
    from opengrader.core.history.journal import resolve_history_dir
    from opengrader.core.submissions.merge_split import (
        MergeUnavailable,
        merge_in_folder,
        neighbours,
        pieces,
    )

    submissions = _submissions_dir(folder)
    names = [Path(name).stem for name in (first, second)]
    found = {piece.stem: piece for piece in pieces(submissions)}
    for name in names:
        if name not in found:
            raise click.UsageError(f"{name} is not a submission read from a split scan in {submissions}.")
    if neighbours(submissions, names[0])[1] != found[names[1]]:
        if neighbours(submissions, names[1])[1] == found[names[0]]:
            names.reverse()
        else:
            raise click.UsageError(
                f"{names[0]} and {names[1]} were not scanned one after the other, "
                "so they cannot be two pieces of one paper."
            )
    first_piece, second_piece = found[names[0]], found[names[1]]

    def status(message: str) -> None:
        click.echo(message)

    try:
        published = _workspace_assignment(submissions)
        if published is None:
            result = merge_in_folder(
                submissions,
                first_piece,
                second_piece,
                history_dir=resolve_history_dir(submissions),
                status=status,
            )
            click.echo(f"Joined {second_piece.stem} onto {first_piece.stem}: {result.path}")
        else:
            from opengrader.server.workspace.client import WorkspaceClient, ensure_service
            from opengrader.server.workspace.merge import merge_in_workspace, workspace_pieces

            root, assignment_id = published
            _folder, by_id = workspace_pieces(root, assignment_id)
            ids = {piece.stem: sid for sid, piece in by_id.items()}
            if names[0] not in ids or names[1] not in ids:
                raise MergeUnavailable(
                    "These submissions are not both in the workspace; open the "
                    "assignment once so it is up to date, then join them."
                )
            client = WorkspaceClient(ensure_service(root))
            new_id, result = merge_in_workspace(
                client, assignment_id, ids[names[0]], ids[names[1]], status=status
            )
            click.echo(
                f"Joined {second_piece.stem} onto {first_piece.stem}: now submission "
                f"{new_id} in the workspace."
            )
    except MergeUnavailable as exc:
        click.echo(f"Not joined: {exc}", err=True)
        sys.exit(2)
    click.echo(
        f"{result.grades} grade(s) and {result.readings} corrected reading(s) carried over."
    )
