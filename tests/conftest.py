from __future__ import annotations

import os
from pathlib import Path

import pytest

from opengrader.core.ai_config import ENV_CONFIG_PATH


@pytest.fixture(autouse=True)
def _isolate_ai_settings(tmp_path_factory: pytest.TempPathFactory) -> None:
    target = tmp_path_factory.mktemp("ai-settings") / "ai.json"
    saved = os.environ.get(ENV_CONFIG_PATH)
    os.environ[ENV_CONFIG_PATH] = str(Path(target))
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop(ENV_CONFIG_PATH, None)
        else:
            os.environ[ENV_CONFIG_PATH] = saved


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
