from contextlib import suppress
from pathlib import Path
import sys

import click

from opengrader.cli import CONTEXT_SETTINGS, run_command
from opengrader.tui.terminal_graphics import (
    ENV_OVERRIDE,
    PROTOCOL_CHOICES,
    GraphicsOverrideError,
    configure_graphics,
    graphics_report,
)
from opengrader.tui.tui import TuiApp


def _graphics_options(function):
    function = click.option(
        "--check-graphics",
        is_flag=True,
        help=(
            "Print which image protocol this terminal will use (and why), "
            "then exit. Paste this into a bug report."
        ),
    )(function)
    return click.option(
        "--graphics",
        type=click.Choice(PROTOCOL_CHOICES),
        default="auto",
        help=(
            "Terminal image protocol for answer crops. 'auto' detects it; "
            "force one when detection guesses wrong (also settable via "
            f"{ENV_OVERRIDE})."
        ),
    )(function)


def _folder_options(function):
    function = click.option(
        "--submissions",
        type=click.Path(path_type=Path),
        help="Path to a .submission.md file or directory containing submission files.",
    )(function)
    return click.option(
        "--qa-file", type=click.Path(path_type=Path), help="Path to a QA markdown file."
    )(function)


def _prepare(
    ctx: click.Context,
    *,
    graphics: str,
    check_graphics: bool,
    qa_file: Path | None,
    submissions: Path | None,
) -> None:
    # Probe graphics before Textual owns stdin and image widgets snapshot the protocol.
    try:
        capability = configure_graphics(graphics)
    except GraphicsOverrideError as exc:
        raise click.UsageError(str(exc)) from None
    if check_graphics:
        click.echo(graphics_report(capability))
        ctx.exit()
    if bool(qa_file) != bool(submissions):
        raise click.UsageError("--qa-file and --submissions must be provided together")


# What a grading session returns on F2 in a workspace: show the list again.
SWITCH = "switch_assignment"

_NO_COURSES = """\
error: no course in {here} or any folder above it, and no courses yet.
  opengrader web            create a course in the browser
  opengrader tui FOLDER     open FOLDER as a course (created if needed)
  opengrader classic        Tools & Settings, and grading folders without a course
"""


@click.command("tui", context_settings=CONTEXT_SETTINGS)
@click.argument("folder", required=False, type=click.Path(path_type=Path))
@click.option(
    "--workspace",
    type=click.Path(path_type=Path),
    help=(
        "Open this workspace directly (shared with the web app through a "
        "local service; created if it does not exist yet)."
    ),
)
@click.option(
    "--assignment",
    help="Assignment ID. Required at a workspace root with several assignments.",
)
@_folder_options
@_graphics_options
@click.pass_context
def tui_command(
    ctx: click.Context,
    folder: Path | None,
    workspace: Path | None,
    assignment: str | None,
    qa_file: Path | None,
    submissions: Path | None,
    graphics: str,
    check_graphics: bool,
) -> None:
    """Open a workspace in the terminal grading app: its assignment list,
    shared live with the web app through a local service. For Tools &
    Settings and grading plain folders, use `opengrader classic`.

    FOLDER is an assignment or workspace folder. Defaults to the workspace
    around the current directory. A folder outside any workspace becomes one;
    a class folder that is ready to grade is imported with the grades already
    in its files.
    """
    _prepare(
        ctx,
        graphics=graphics,
        check_graphics=check_graphics,
        qa_file=qa_file,
        submissions=submissions,
    )
    if qa_file and (folder or workspace or assignment):
        raise click.UsageError(
            "--qa-file/--submissions cannot be combined with a workspace selection"
        )

    try:
        if qa_file and submissions:
            # Kept for scripts: a folder named outright is graded the classic
            # way, in place and with no service.
            _after_classic(_grade_folder(qa_file, submissions))
            return
        target = (folder or workspace or Path.cwd()).expanduser().resolve()
        root = workspace or _workspace_for(target)
        if root is None and folder is None:
            root = _choose_course(target)
            if root is None:
                return
            target = root
        elif root is None:
            root, imported = _open_folder_as_workspace(target)
            assignment = assignment or imported
        _run_workspace(Path(root).expanduser().resolve(), assignment=assignment, target=target)
    except (OSError, ValueError, RuntimeError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)


@click.command("classic", context_settings=CONTEXT_SETTINGS)
@_folder_options
@_graphics_options
@click.pass_context
def classic_command(
    ctx: click.Context,
    qa_file: Path | None,
    submissions: Path | None,
    graphics: str,
    check_graphics: bool,
) -> None:
    """Open the classic grading app: Tools & Settings with a Start grading
    card, grading class folders in place. Never uses a workspace or starts a
    service, except through the opt-in Open in browser button (Tools →
    Browser app). This is what the desktop icon opens.
    """
    _prepare(
        ctx,
        graphics=graphics,
        check_graphics=check_graphics,
        qa_file=qa_file,
        submissions=submissions,
    )
    try:
        if qa_file and submissions:
            result = _grade_folder(qa_file, submissions)
        else:
            result = TuiApp().run()
        _after_classic(result)
    except (OSError, ValueError, RuntimeError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)


def _after_classic(result: object) -> None:
    """Act on the Start grading card's workspace button, if it was pressed.

    Classic never opens a workspace itself: the button closes the app with a
    request, and the workspace opens here — made first, or as it is.
    """
    from opengrader.tui.make_workspace import (
        MakeWorkspaceRequest,
        OpenWorkspaceRequest,
        make_workspace,
    )

    if isinstance(result, MakeWorkspaceRequest):
        _run_workspace(make_workspace(result, echo=click.echo))
    elif isinstance(result, OpenWorkspaceRequest):
        _run_workspace(result.root.expanduser().resolve())


def _choose_course(here: Path) -> Path | None:
    from opengrader.server.registry import WorkspaceRegistry
    from opengrader.tui.course_chooser import CourseChooser

    courses = WorkspaceRegistry().list()
    if not courses:
        click.echo(_NO_COURSES.format(here=here), err=True, nl=False)
        sys.exit(2)
    return CourseChooser(courses).run()


def _workspace_for(folder: Path) -> Path | None:
    from opengrader.core.workspace.store import discover_workspace

    return discover_workspace(folder)


def _grade_folder(qa_file: Path, submissions: Path):
    # F2 comes back to the Start grading card inside the app, so one run
    # covers any number of folders.
    return TuiApp(qa_file_path=qa_file, submissions_path=submissions).run()


def _open_folder_as_workspace(folder: Path) -> tuple[Path, str | None]:
    """A folder outside every workspace, named on the command line.

    A class folder that is ready to grade (one answer key, extracted
    submissions) is imported with its grades, and that assignment opens. Any
    other folder simply becomes the workspace root, and the empty assignment
    list offers to process it.
    """
    from opengrader.core.open_target import detect_target

    found = detect_target(folder)
    if found.kind == "folder" and found.openable:
        return _import_as_workspace(found.qa_file, found.submissions)
    return folder, None


def _import_as_workspace(qa_file, submissions):
    import os

    from opengrader.core.workspace.store import Workspace
    from opengrader.server.workspace.client import WorkspaceClient, ensure_service
    from opengrader.tui.make_workspace import import_assignment

    qa = Path(qa_file).expanduser().resolve()
    subs = Path(submissions).expanduser().resolve()
    root = Path(os.path.commonpath([qa.parent, subs]))
    client = WorkspaceClient(ensure_service(root))
    return root, import_assignment(client, Workspace(root), root, qa, subs)


def _run_workspace(
    root: Path, *, assignment: str | None = None, target: Path | None = None
) -> None:
    from pydantic import TypeAdapter
    from opengrader.core.recents import recent_workspace, record_recent
    from opengrader.core.workspace.context import assignment_for_directory
    from opengrader.core.workspace.models import Assignment
    from opengrader.core.workspace.store import Workspace
    from opengrader.server.workspace.client import WorkspaceClient, connect_workspace
    from opengrader.tui.data.workspace_provider import WorkspaceProvider
    from opengrader.tui.workspace_home import (
        PROCESS_WORKSPACE,
        WorkspaceHome,
        process_workspace_argv,
    )

    target = (target or root).expanduser().resolve()
    client = WorkspaceClient(connect_workspace(root))
    root = Path(client.connection.root).resolve()
    joined = bool(client.connection.account)
    # Listed first next time; keeps the assignment it last recorded.
    record_recent("workspace", root, title=root.name)
    last = recent_workspace(root)
    highlight = last.assignment if last and last.assignment else None

    def fetch() -> list[Assignment]:
        return TypeAdapter(list[Assignment]).validate_json(
            client.request("GET", "/api/assignments")
        )

    assignments = fetch()
    try:
        # Read from the database itself, which only its owner can read; a
        # joining account picks from the list instead.
        selected = (
            assignment
            if joined
            else assignment_for_directory(Workspace(root), target, assignment_id=assignment)
        )
    except ValueError:
        if assignment is not None:
            raise
        selected = None
    if selected is not None and all(item.id != selected for item in assignments):
        selected = None

    # The assignment list and grading, back and forth (F2), until one quits.
    while True:
        if selected is None:
            selected = WorkspaceHome(client, target, highlight=highlight).run()
            if selected is None:
                return
            if selected == PROCESS_WORKSPACE and joined:
                click.echo(
                    f"Only {client.connection.owner}, who owns this workspace, can read "
                    "new scans into it.",
                    err=True,
                )
                selected = None
                continue
            if selected == PROCESS_WORKSPACE:
                from opengrader.cli import run

                with suppress(SystemExit):
                    run_command(run.command, process_workspace_argv(str(target), str(root)))
                assignments = fetch()
                try:
                    selected = assignment_for_directory(Workspace(root), target)
                except ValueError:
                    selected = None
                continue
            assignments = fetch()
        chosen = next((item for item in assignments if item.id == selected), None)
        if chosen is None:
            raise ValueError("Assignment not found in this workspace.")
        record_recent(
            "workspace",
            root,
            title=root.name,
            assignment=chosen.id,
            assignment_title=chosen.title,
        )
        highlight = chosen.id  # F2 comes back to the list on this one
        provider = WorkspaceProvider(client, chosen)
        try:
            result = TuiApp(data_provider=provider).run()
        finally:
            provider.close()
        if result != SWITCH:
            return
        selected = None


if __name__ == "__main__":
    tui_command()
