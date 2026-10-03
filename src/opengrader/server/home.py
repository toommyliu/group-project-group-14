"""Server storage location, independent of the selected grading files."""

import os
from pathlib import Path


def resolve_data_home() -> Path:
    override = os.environ.get("OPENGRADER_SERVER_HOME")
    return Path(override or Path.home() / ".opengrader").expanduser().resolve()


def default_workspaces_dir() -> Path:
    return Path.home() / "Opengrader"
