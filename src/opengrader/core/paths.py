from __future__ import annotations

import os
from pathlib import Path

import platformdirs

_DB_FILE_NAME = "courses.db"
_ENV_HOME = "OPENGRADER_HOME"


def get_home_dir() -> Path:
    configured_home = os.getenv(_ENV_HOME)
    if configured_home is not None and configured_home.strip():
        return Path(configured_home).expanduser()
    return Path(platformdirs.user_data_dir("opengrader"))


def get_data_dir() -> Path:
    return get_home_dir() / "data"


def get_cache_dir() -> Path:
    return get_home_dir() / "cache"


def get_config_dir() -> Path:
    return get_home_dir() / "config"


def get_logs_dir() -> Path:
    return get_home_dir() / "logs"


def get_templates_dir() -> Path:
    return get_data_dir() / "templates"


def get_settings_path() -> Path:
    return get_config_dir() / "settings.toml"


def get_database_path() -> Path:
    return get_data_dir() / _DB_FILE_NAME


def resolve_database_path(db_path: str | Path | None = None) -> Path:
    if db_path is None:
        return get_database_path()
    return Path(db_path).expanduser()
