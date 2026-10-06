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


@pytest.fixture(autouse=True)
def _isolate_clipboard(monkeypatch: pytest.MonkeyPatch) -> None:
    # A Copy button runs the operating system's clipboard tool (pbcopy on a
    # Mac). Without this, running the tests would overwrite whatever the
    # person at the keyboard had copied.
    from opengrader.tui import clipboard

    monkeypatch.setattr(clipboard, "_native_commands", lambda: [])


@pytest.fixture(autouse=True)
def _no_local_ai_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    # The providers screen asks the Ollama server on this computer which
    # models it has. Left alone, what a test sees would depend on whether
    # Ollama happens to be running on the machine the tests run on. A test
    # of the probe itself takes the real function from the module at import
    # time, before this replaces it.
    from opengrader.core.app_config import setup

    monkeypatch.setattr(setup, "local_models", lambda name: None)


@pytest.fixture(autouse=True)
def _isolate_key_store():
    # A provider with an api_key_env looks its key up in the operating
    # system's password store (the login Keychain on a Mac). No test may read
    # or write the real one, so every test gets an empty in-memory store.
    from opengrader.core.app_config import keystore

    keystore.set_backend(keystore.MemoryBackend())
    try:
        yield
    finally:
        keystore.set_backend(None)
