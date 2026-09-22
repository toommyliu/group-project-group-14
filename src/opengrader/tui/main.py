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


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Launch the Opengrader TUI. Open an assignment folder directly, "
            "or use Home to choose a folder, workspace, or tool."
        )
    )
    parser.add_argument("folder", nargs="?", type=Path, help="Assignment or workspace folder.")
    parser.add_argument("--qa-file", help="Path to a QA markdown file.")
    parser.add_argument(
        "--submissions",
        help="Path to a .submission.md file or directory containing submission files.",
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
    return parser


def run_app(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Probe graphics before Textual owns stdin and image widgets snapshot the protocol.
    try:
        capability = configure_graphics(args.graphics)
    except GraphicsOverrideError as exc:
        parser.error(str(exc))
        return

    if args.check_graphics:
        parser.exit(0, graphics_report(capability) + "\n")
        return

    if bool(args.qa_file) != bool(args.submissions):
        parser.error("--qa-file and --submissions must be provided together")
    if args.qa_file and (args.folder or args.workspace or args.assignment):
        parser.error("--qa-file/--submissions cannot be combined with a workspace selection")

    try:
        if args.qa_file:
            result = _grade_folder(Path(args.qa_file), Path(args.submissions))
            if result != SWITCH:
                return
        elif args.folder or args.workspace or args.assignment or _discovered_workspace():
            target = (args.folder or args.workspace or Path.cwd()).expanduser().resolve()
            root = (args.workspace or _workspace_for(target) or target).resolve()
            if _run_workspace(root, assignment=args.assignment, target=target) != HOME:
                return
        _home_loop()
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"error: {exc}\n")


SWITCH = "switch_assignment"
HOME = "home"


def _home_loop() -> None:
    from opengrader.tui.home import Home

    start = _default_start_path()
    message = ""
    while True:
        choice = Home(start_path=start, message=message).run()
        message = ""
        if choice is None:
            return
        if choice.folder is not None:
            start = str(choice.folder)
        if choice.kind == "folder":
            try:
                result = _grade_folder(
                    choice.qa_file, choice.submissions, folder=choice.folder
                )
            except (OSError, ValueError) as exc:
                message = f"Could not open that folder: {exc}"
                continue
        elif choice.kind in ("workspace", "new_workspace"):
            try:
                if choice.kind == "new_workspace":
                    root, assignment = _import_as_workspace(
                        choice.qa_file, choice.submissions
                    )
                    start = str(root)
                else:
                    root, assignment = choice.folder, None
                if _run_workspace(root, assignment=assignment) == HOME:
                    continue
                return
            except (OSError, ValueError, RuntimeError) as exc:
                message = f"Could not open the workspace: {exc}"
                continue
        else:
            result = TuiApp(pipeline_folder=choice.pipeline_folder).run()
        if result != SWITCH:
            return


def _default_start_path() -> str:
    from opengrader.core.open_target import detect_target

    here = Path.cwd()
    return str(here) if detect_target(here).openable else ""


def _discovered_workspace() -> Path | None:
    from opengrader.core.workspace.store import discover_workspace

    return discover_workspace(Path.cwd())


def _workspace_for(folder: Path) -> Path | None:
    from opengrader.core.workspace.store import discover_workspace

    return discover_workspace(folder)


def _grade_folder(qa_file, submissions, *, folder: Path | None = None):
    app = TuiApp(qa_file_path=qa_file, submissions_path=submissions)
    _remember_folder(qa_file, submissions, folder=folder)
    return app.run()


def _remember_folder(qa_file, submissions, *, folder: Path | None = None) -> None:
    from opengrader.core.recents import record_recent

    where = Path(folder or Path(submissions).parent)
    record_recent(
        "folder",
        where,
        title=_assignment_title(qa_file) or where.name,
        qa_file=qa_file,
        submissions=submissions,
    )


def _assignment_title(qa_file) -> str:
    try:
        from opengrader.core.qa_file import load_qa_markdown

        return load_qa_markdown(Path(qa_file)).title or ""
    except Exception:  # noqa: BLE001 - a title is a label, never a reason to fail
        return ""


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
        assignment_id=assignment_id,
        qa_markdown=qa.read_text(encoding="utf-8"),
        submissions_dir=str(subs),
    )
    result = PipelinePublication.model_validate_json(
        client.request("POST", "/api/pipeline-results", output, timeout=300)
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
) -> str | None:
    from pydantic import TypeAdapter
    from opengrader.core.recents import record_recent
    from opengrader.core.workspace.context import assignment_for_directory
    from opengrader.core.workspace.models import Assignment
    from opengrader.core.workspace.store import Workspace
    from opengrader.server.workspace.client import WorkspaceClient, ensure_service
    from opengrader.tui.data.workspace_provider import WorkspaceProvider
    from opengrader.tui.workspace_home import (
        GO_HOME,
        OPEN_TOOLS,
        PROCESS_WORKSPACE,
        WorkspaceHome,
        process_workspace_argv,
    )

    target = (target or root).expanduser().resolve()
    client = WorkspaceClient(ensure_service(root))
    root = Path(client.connection.root).resolve()
    record_recent("workspace", root, title=root.name)

    def fetch() -> list[Assignment]:
        return TypeAdapter(list[Assignment]).validate_json(
            client.request("GET", "/assignments")
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

    while True:
        if selected is None:
            selected = WorkspaceHome(client, target).run()
            if selected is None:
                return None
            if selected == GO_HOME:
                return HOME
            if selected == OPEN_TOOLS:
                if TuiApp().run() != SWITCH:
                    return None
                selected = None
                continue
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
        provider = WorkspaceProvider(client, chosen)
        try:
            result = TuiApp(data_provider=provider).run()
        finally:
            provider.close()
        if result != SWITCH:
            return None
        selected = None


def run_app_dev() -> None:
    _enable_textual_feature(_DEVTOOLS_FEATURE)
    run_app()


if __name__ == "__main__":
    run_app()
