from __future__ import annotations

import os

import pytest

from opengrader.core import app_config


@pytest.fixture(autouse=True)
def _isolate_ai_settings(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path_factory.mktemp("ai-settings")
    monkeypatch.setattr(app_config, "_config_dir", lambda: directory)


@pytest.fixture(autouse=True)
def _isolate_textual_env() -> None:
    saved = os.environ.get("TEXTUAL")
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("TEXTUAL", None)
        else:
            os.environ["TEXTUAL"] = saved
