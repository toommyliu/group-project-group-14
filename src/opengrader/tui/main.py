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
    parser = ArgumentParser(description="Launch the Opengrader TUI.")
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Workspace directory. Defaults to the current workspace.",
    )
    parser.add_argument(
        "--assignment",
        help="Assignment ID. Defaults to the most recently created assignment.",
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

    from pydantic import TypeAdapter
    from opengrader.core.workspace.store import discover_workspace
    from opengrader.core.workspace.models import Assignment
    from opengrader.server.workspace.client import WorkspaceClient, ensure_service
    from opengrader.tui.data.workspace_provider import WorkspaceProvider
    from opengrader.tui.workspace_home import (
        PROCESS_WORKSPACE,
        WorkspaceHome,
        process_workspace_argv,
    )

    try:
        root = args.workspace or discover_workspace(Path.cwd()) or Path.cwd()
        client = WorkspaceClient(ensure_service(root))
        assignments = TypeAdapter(list[Assignment]).validate_json(
            client.request("GET", "/assignments")
        )
        selected = args.assignment
        if selected is None and assignments:
            selected = assignments[-1].id
        while True:
            if selected is None:
                selected = WorkspaceHome(client).run()
                if selected is None:
                    return
                if selected == PROCESS_WORKSPACE:
                    from opengrader.cli.run import run_cli

                    run_cli(process_workspace_argv(str(root)))
                    assignments = TypeAdapter(list[Assignment]).validate_json(
                        client.request("GET", "/assignments")
                    )
                    selected = assignments[-1].id if assignments else None
                    continue
                assignments = TypeAdapter(list[Assignment]).validate_json(
                    client.request("GET", "/assignments")
                )
            assignment = next(
                (item for item in assignments if item.id == selected), None
            )
            if assignment is None:
                raise ValueError("Assignment not found in this workspace.")
            provider = WorkspaceProvider(client, assignment)
            try:
                result = TuiApp(data_provider=provider).run()
            finally:
                provider.close()
            if result != "switch_assignment":
                return
            selected = None
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"error: {exc}\n")


def run_app_dev() -> None:
    _enable_textual_feature(_DEVTOOLS_FEATURE)
    run_app()


if __name__ == "__main__":
    run_app()
