"""Server storage location, independent of the selected grading files."""

import os
from pathlib import Path
import sys


def resolve_data_home() -> Path:
    override = os.environ.get("OPENGRADER_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        home = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        home = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return (home / "opengrader").expanduser().resolve()
