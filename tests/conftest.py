from __future__ import annotations

import pytest

from opengrader.core import app_config


@pytest.fixture(autouse=True)
def _isolate_ai_settings(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path_factory.mktemp("ai-settings")
    monkeypatch.setattr(app_config, "_config_dir", lambda: directory)



@pytest.fixture(autouse=True)
def _isolate_server_home(tmp_path_factory, monkeypatch):
    directory = tmp_path_factory.mktemp("server-home")
    monkeypatch.setenv("OPENGRADER_SERVER_HOME", str(directory))
