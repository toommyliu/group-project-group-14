from argparse import ArgumentParser
import os
from pathlib import Path

from opengrader.tui.terminal_graphics import (
    ENV_OVERRIDE,
    PROTOCOL_CHOICES,
    GraphicsOverrideError,
    configure_graphics,
    graphics_report,
)
from opengrader.tui.tui import TuiApp

_TEXTUAL_ENV_VAR = "TEXTUAL"
_DEVTOOLS_FEATURE = "devtools"


def _enable_textual_feature(feature: str) -> None:
    features = {
        value.strip().lower()
        for value in os.environ.get(_TEXTUAL_ENV_VAR, "").split(",")
        if value.strip()
    }
    features.add(feature)
    os.environ[_TEXTUAL_ENV_VAR] = ",".join(sorted(features))


def _add_graphics_arguments(parser: ArgumentParser) -> None:
    parser.add_argument(
        "--graphics",
        choices=PROTOCOL_CHOICES,
        default="auto",
        help=(
            "Terminal image protocol for answer crops. 'auto' detects it; "
            "force one when detection guesses wrong (also settable via "
            f"{ENV_OVERRIDE})."
        ),
    )
    parser.add_argument(
        "--check-graphics",
        action="store_true",
        help=(
            "Print which image protocol this terminal will use (and why), "
            "then exit. Paste this into a bug report."
        ),
    )


def _add_folder_arguments(parser: ArgumentParser) -> None:
    parser.add_argument("--qa-file", help="Path to a QA markdown file.")
    parser.add_argument(
        "--submissions",
        help="Path to a .submission.md file or directory containing submission files.",
    )


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Open a workspace in the terminal grading app: its assignment list, "
            "shared live with the web app through a local service. For Tools & "
            "Settings and grading plain folders, use `opengrader classic`."
        )
    )
    parser.add_argument(
        "folder",
        nargs="?",
        type=Path,
        help=(
            "Assignment or workspace folder. Defaults to the workspace around "
            "the current directory. A folder outside any workspace becomes "
            "one; a class folder that is ready to grade is imported with the "
            "grades already in its files."
        ),
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help=(
            "Open this workspace directly (shared with the web app through a "
            "local service; created if it does not exist yet)."
        ),
    )
    parser.add_argument(
        "--assignment",
        help="Assignment ID. Required at a workspace root with several assignments.",
    )
    _add_folder_arguments(parser)
    _add_graphics_arguments(parser)
    return parser


def _build_classic_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Open the classic grading app: Tools & Settings with a Start "
            "grading card, grading class folders in place. Never uses a "
            "workspace or starts a service. This is what the desktop icon opens."
        ),
    )
    _add_folder_arguments(parser)
    _add_graphics_arguments(parser)
    return parser


def _parse(parser: ArgumentParser, argv: list[str] | None):
    """Parse argv and settle the image protocol, before Textual owns stdin."""
    args = parser.parse_args(argv)
    # Probe graphics before Textual owns stdin and image widgets snapshot the protocol.
    try:
        capability = configure_graphics(args.graphics)
    except GraphicsOverrideError as exc:
        parser.error(str(exc))
    if args.check_graphics:
        parser.exit(0, graphics_report(capability) + "\n")
    if bool(args.qa_file) != bool(args.submissions):
        parser.error("--qa-file and --submissions must be provided together")
    return args


# What a grading session returns on F2 in a workspace: show the list again.
SWITCH = "switch_assignment"

_NO_WORKSPACE = """\
error: no workspace in {here} or any folder above it.
  opengrader tui FOLDER     open FOLDER as a workspace (created if needed)
  opengrader classic        Tools & Settings, and grading folders without a workspace
"""


def run_app(argv: list[str] | None = None) -> None:
    """`opengrader tui`: a workspace, straight to its assignments."""
    parser = _build_parser()
    args = _parse(parser, argv)
    if args.qa_file and (args.folder or args.workspace or args.assignment):
        parser.error("--qa-file/--submissions cannot be combined with a workspace selection")

    try:
        if args.qa_file:
            # Kept for scripts: a folder named outright is graded the classic
            # way, in place and with no service.
            _grade_folder(Path(args.qa_file), Path(args.submissions))
            return
        target = (args.folder or args.workspace or Path.cwd()).expanduser().resolve()
        root = args.workspace or _workspace_for(target)
        assignment = args.assignment
        if root is None and args.folder is None:
            # A bare `tui` never turns the current directory into a workspace
            # behind the user's back: offer the ones already opened instead.
            root = _choose_recent_workspace(parser, target)
            if root is None:
                return
            target = root
        elif root is None:
            root, imported = _open_folder_as_workspace(target)
            assignment = assignment or imported
        _run_workspace(Path(root).expanduser().resolve(), assignment=assignment, target=target)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"error: {exc}\n")


def run_classic(argv: list[str] | None = None) -> None:
    """`opengrader classic`: Tools & Settings and folder grading, no workspace."""
    parser = _build_classic_parser()
    args = _parse(parser, argv)
    try:
        if args.qa_file:
            _grade_folder(Path(args.qa_file), Path(args.submissions))
        else:
            TuiApp().run()
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")


def _choose_recent_workspace(parser: ArgumentParser, here: Path) -> Path | None:
    """A workspace opened before; with none on record, exit 2 with the pointer."""
    from opengrader.core.recents import load_recents
    from opengrader.tui.recent_workspaces import RecentWorkspaces

    recent = [item for item in load_recents() if item.kind == "workspace"]
    if not recent:
        parser.exit(2, _NO_WORKSPACE.format(here=here))
    return RecentWorkspaces(recent, here=here).run()


def _workspace_for(folder: Path) -> Path | None:
    from opengrader.core.workspace.store import discover_workspace

    return discover_workspace(folder)


def _grade_folder(qa_file: Path, submissions: Path) -> None:
    # F2 comes back to the Start grading card inside the app, so one run
    # covers any number of folders.
    TuiApp(qa_file_path=qa_file, submissions_path=submissions).run()


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

    from opengrader.core.workspace.context import inputs_for_assignment, stored_path
    from opengrader.core.workspace.models import (
        AssignmentInputs,
        PipelineOutput,
        PipelinePublication,
    )
    from opengrader.core.workspace.store import Workspace
    from opengrader.server.workspace.client import WorkspaceClient, ensure_service

    qa = Path(qa_file).expanduser().resolve()
    subs = Path(submissions).expanduser().resolve()
    root = Path(os.path.commonpath([qa.parent, subs]))
    client = WorkspaceClient(ensure_service(root))
    workspace = Workspace(root)
    assignment_id = workspace.assignment_id_for_folder(root)
    output = PipelineOutput(
        qa_markdown=qa.read_text(encoding="utf-8"),
        submissions_dir=str(subs),
    )
    result = PipelinePublication.model_validate_json(
        client.request(
            "POST", f"/api/assignments/{assignment_id}/imports", output, timeout=300
        )
    )
    saved = inputs_for_assignment(workspace, assignment_id)
    if saved is None:
        saved = AssignmentInputs(
            id=assignment_id,
            folder=".",
            scan="",
            output=stored_path(root, subs.parent),
            qa=stored_path(root, qa),
        )
    workspace.save_assignment_inputs(
        saved.model_copy(update={"submissions": stored_path(root, subs)})
    )
    return root, result.assignment.id


def _run_workspace(
    root: Path, *, assignment: str | None = None, target: Path | None = None
) -> None:
    from pydantic import TypeAdapter
    from opengrader.core.recents import recent_workspace, record_recent
    from opengrader.core.workspace.context import assignment_for_directory
    from opengrader.core.workspace.models import Assignment
    from opengrader.core.workspace.store import Workspace
    from opengrader.server.workspace.client import WorkspaceClient, ensure_service
    from opengrader.tui.data.workspace_provider import WorkspaceProvider
    from opengrader.tui.workspace_home import (
        PROCESS_WORKSPACE,
        WorkspaceHome,
        process_workspace_argv,
    )

    target = (target or root).expanduser().resolve()
    client = WorkspaceClient(ensure_service(root))
    root = Path(client.connection.root).resolve()
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
        selected = assignment_for_directory(
            Workspace(root), target, assignment_id=assignment
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
            if selected == PROCESS_WORKSPACE:
                from opengrader.cli.run import run_cli

                run_cli(process_workspace_argv(str(target), str(root)))
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


def run_app_dev() -> None:
    _enable_textual_feature(_DEVTOOLS_FEATURE)
    run_app()


if __name__ == "__main__":
    run_app()
