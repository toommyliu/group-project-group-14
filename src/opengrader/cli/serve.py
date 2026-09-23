"""Launch the localhost grading API and web app."""

from argparse import ArgumentParser
from copy import deepcopy
import locale
import logging.config

from pathlib import Path

from dotenv import load_dotenv


def _load_project_env(root: Path | None = None) -> None:
    """Load local server settings without replacing the caller's environment."""
    load_dotenv((root or Path.cwd()) / ".env", override=False)


def main(argv: list[str] | None = None) -> None:
    _load_project_env()
    parser = ArgumentParser(description="Run this workspace service on localhost.")
    parser.add_argument(
        "--port", type=int, default=8000, help="Local port, default 8000."
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Hot-reload the frontend and restart API and Vite on Python edits.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Workspace directory. Defaults to the current workspace.",
    )
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")

    from uvicorn.config import LOGGING_CONFIG

    try:
        locale.setlocale(locale.LC_TIME, "")
    except locale.Error:
        # An unavailable shell locale should not prevent the server from starting.
        pass

    log_config = deepcopy(LOGGING_CONFIG)
    for formatter in log_config["formatters"].values():
        formatter["fmt"] = "%(asctime)s " + formatter["fmt"]
        formatter["datefmt"] = "%X"
    log_config["formatters"]["access"]["()"] = (
        "opengrader.server.request_logging.TimedAccessFormatter"
    )
    log_config["loggers"]["opengrader"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    logging.config.dictConfig(log_config)

    if args.dev:
        if args.port == 0:
            parser.error("--dev requires a fixed, nonzero API port")
        from opengrader.server.dev import run_dev

        try:
            code = run_dev(args.port, workspace=args.workspace)
        except (OSError, ValueError) as exc:
            parser.exit(2, f"error: {exc}\n")
        parser.exit(code)

    from opengrader.core.workspace.store import discover_workspace
    from opengrader.server.workspace.__main__ import serve

    try:
        root = args.workspace or discover_workspace(Path.cwd()) or Path.cwd()
        serve(root, port=args.port)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
