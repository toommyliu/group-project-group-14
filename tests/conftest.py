"""Shared pytest fixtures for the Opengrader test suite."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from opengrader.core.ai_config import ENV_CONFIG_PATH


@pytest.fixture(autouse=True)
def _isolate_ai_settings(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Point the AI settings file at a throwaway path for every test.

    Every CLI now resolves its model/host/confidence defaults from this file
    (``core/ai_config``), so without this fixture the suite would read whatever
    the person running it happens to have configured — a test asserting the
    default model would pass on a fresh checkout and fail on a machine where
    someone had chosen ``llama3.1`` — and, worse, any test that exercises the
    save path would overwrite their real preferences. The path is per-session
    and deliberately *not* created: an absent file is the "nothing configured"
    case, which is what the defaults are defined against.
    """
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
    """Restore the ``TEXTUAL`` env var after every test.

    ``opengrader.tui.main.run_app_dev`` (and its test) set
    ``os.environ["TEXTUAL"] = "devtools"`` directly. When a test does that
    without a monkeypatch that tracks the key, the value leaks into the rest of
    the process: every later ``App.run_test()`` then builds a Textual
    ``DevtoolsClient`` that opens an aiohttp ``ClientSession`` it never closes
    (surfacing as "Unclosed client session" at interpreter shutdown), and each
    app pointlessly tries to reach a devtools console. Snapshotting and
    restoring the variable around each test keeps that isolated.
    """
    saved = os.environ.get("TEXTUAL")
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("TEXTUAL", None)
        else:
            os.environ["TEXTUAL"] = saved
