"""Open or stop the browser service for the current workspace."""

from argparse import ArgumentParser
import os
from pathlib import Path


def _root(raw: str | None) -> Path:
    from opengrader.core.workspace.store import discover_workspace

    root = (
        Path(raw).expanduser().resolve()
        if raw
        else discover_workspace(Path.cwd()) or Path.cwd()
    )
    return root


def open_web(argv: list[str] | None = None) -> None:
    parser = ArgumentParser(
        description="Open the browser interface for this workspace."
    )
    parser.add_argument("--workspace")
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Print the address without opening a browser.",
    )
    args = parser.parse_args(argv)
    from opengrader.server.workspace.client import ensure_service

    try:
        connection = ensure_service(_root(args.workspace))
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"error: {exc}\n")
    url = connection.url
    print(url)
    if not args.no_open and not os.environ.get("SSH_CONNECTION"):
        import webbrowser

        webbrowser.open(url)
    else:
        print(
            "This address is local to this host. For SSH access, forward its port to the same port on your computer."
        )


def stop(argv: list[str] | None = None) -> None:
    parser = ArgumentParser(
        description="Stop the local workspace service after its active feedback job finishes."
    )
    parser.add_argument("--workspace")
    args = parser.parse_args(argv)
    from opengrader.server.workspace.client import WorkspaceClient, running_service

    try:
        connection = running_service(_root(args.workspace))
        if connection:
            WorkspaceClient(connection).request("POST", "/api/shutdown")
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"error: {exc}\n")
