from pathlib import Path

import platformdirs

from opengrader.core import (
    get_home_dir,
    get_cache_dir,
    get_config_dir,
    get_database_path,
    get_data_dir,
    get_logs_dir,
    resolve_database_path,
    get_settings_path,
)


def test_default_paths_use_platformdirs(monkeypatch) -> None:
    monkeypatch.delenv("OPENGRADER_HOME", raising=False)

    expected_base = Path(platformdirs.user_data_dir("opengrader"))

    assert get_home_dir() == expected_base
    assert get_data_dir() == expected_base / "data"
    assert get_config_dir() == expected_base / "config"
    assert get_cache_dir() == expected_base / "cache"
    assert get_logs_dir() == expected_base / "logs"
    assert get_settings_path() == expected_base / "config" / "settings.toml"
    assert get_database_path() == expected_base / "data" / "courses.db"


def test_paths_honor_opengrader_home_env_var(monkeypatch) -> None:
    monkeypatch.setenv("OPENGRADER_HOME", "/tmp/custom-opengrader")

    assert get_home_dir() == Path("/tmp/custom-opengrader")
    assert get_database_path() == Path("/tmp/custom-opengrader/data/courses.db")


def test_resolve_database_path_uses_default_when_not_provided(monkeypatch) -> None:
    monkeypatch.delenv("OPENGRADER_HOME", raising=False)

    expected_base = Path(platformdirs.user_data_dir("opengrader"))
    assert resolve_database_path() == expected_base / "data" / "courses.db"


def test_resolve_database_path_prefers_explicit_argument(monkeypatch) -> None:
    monkeypatch.setenv("OPENGRADER_HOME", "/tmp/custom-opengrader")

    assert resolve_database_path("/tmp/db.sqlite") == Path("/tmp/db.sqlite")
    assert resolve_database_path(Path("/tmp/explicit.db")) == Path("/tmp/explicit.db")
