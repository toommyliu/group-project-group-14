from __future__ import annotations

import ctypes
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from opengrader.core import app_config
from opengrader.core.app_config import keystore
from opengrader.core.app_config import manifests as ai_manifests

OPENAI = ai_manifests.parse_text(ai_manifests.example_text("openai"))


# --------------------------------------------------------------------------
# The public functions, over the in-memory store every test starts with
# --------------------------------------------------------------------------


def test_a_saved_key_is_read_back_and_can_be_forgotten() -> None:
    account = keystore.account_for(OPENAI)

    assert keystore.lookup(account) is None
    keystore.store(account, "  sk-live-123  ")

    assert keystore.lookup(account) == "sk-live-123"
    assert keystore.delete(account) is True
    assert keystore.delete(account) is False
    assert keystore.lookup(account) is None


def test_the_entry_is_tied_to_the_host_the_provider_sends_to() -> None:
    elsewhere = ai_manifests.parse_text(
        ai_manifests.example_text("openai").replace(
            "https://api.openai.com/v1", "https://collector.example.net/v1"
        )
    )
    keystore.store(keystore.account_for(OPENAI), "sk-live-123")

    assert keystore.account_for(OPENAI) == "openai|api.openai.com"
    assert keystore.account_for(elsewhere) != keystore.account_for(OPENAI)
    # Same provider name, different destination: the key does not follow it.
    assert app_config.resolve_api_key(elsewhere, environ={}) is None
    assert app_config.resolve_api_key(OPENAI, environ={}) == "sk-live-123"


@pytest.mark.parametrize("bad", ["", "   ", "sk-one\nsk-two", "sk-\tx", "sk-\x7f"])
def test_a_key_that_is_empty_or_not_one_line_is_refused(bad: str) -> None:
    with pytest.raises(keystore.KeyStoreError):
        keystore.store("openai|api.openai.com", bad)
    assert keystore.lookup("openai|api.openai.com") is None


def test_a_store_that_cannot_be_used_refuses_a_save_and_reads_as_no_key() -> None:
    keystore.set_backend(keystore.MemoryBackend(reason="no keyring is running"))

    status = keystore.status()
    assert not status.available and "no keyring" in status.reason
    with pytest.raises(keystore.KeyStoreError, match="no keyring"):
        keystore.store("a|b", "sk-1")
    assert keystore.lookup("a|b") is None
    assert keystore.delete("a|b") is False


def test_a_store_that_fails_while_reading_never_raises() -> None:
    class Broken(keystore.MemoryBackend):
        def get(self, account: str) -> str | None:
            raise OSError("keychain is locked")

    keystore.set_backend(Broken())

    assert keystore.lookup("a|b") is None
    assert app_config.resolve_api_key(OPENAI, environ={}) is None


def test_a_lookup_is_asked_of_the_store_once_per_process_until_it_changes() -> None:
    class Counting(keystore.MemoryBackend):
        reads = 0

        def get(self, account: str) -> str | None:
            Counting.reads += 1
            return super().get(account)

    keystore.set_backend(Counting())
    for _ in range(5):
        keystore.lookup("a|b")
    assert Counting.reads == 1

    keystore.store("a|b", "sk-1")
    assert keystore.lookup("a|b") == "sk-1"
    assert Counting.reads == 2

    app_config.clear_credential_cache()
    keystore.lookup("a|b")
    assert Counting.reads == 3


# --------------------------------------------------------------------------
# How app_config uses it
# --------------------------------------------------------------------------


def test_the_environment_variable_wins_over_a_saved_key() -> None:
    keystore.store(keystore.account_for(OPENAI), "sk-saved")

    assert app_config.resolve_api_key(OPENAI, environ={"OPENAI_API_KEY": "sk-env"}) == "sk-env"
    assert app_config.api_key_source(OPENAI, environ={"OPENAI_API_KEY": "sk-env"}) == "environment"
    assert app_config.resolve_api_key(OPENAI, environ={}) == "sk-saved"
    assert app_config.api_key_source(OPENAI, environ={}) == "keystore"


def test_no_key_anywhere_says_how_to_give_one() -> None:
    assert app_config.api_key_source(OPENAI, environ={}) == ""
    help_text = app_config.missing_key_help(OPENAI)
    assert "Set up a provider" in help_text and "OPENAI_API_KEY" in help_text


def test_the_cloud_kill_switch_also_withholds_a_saved_key() -> None:
    keystore.store(keystore.account_for(OPENAI), "sk-saved")

    off = {app_config.ALLOW_CLOUD_ENV: "0"}
    assert app_config.resolve_api_key(OPENAI, environ=off) is None


def test_the_local_default_never_touches_the_key_store() -> None:
    class Forbidden(keystore.MemoryBackend):
        def get(self, account: str) -> str | None:
            raise AssertionError("the local provider looked for a key")

        def unavailable_reason(self) -> str:
            raise AssertionError("the local provider asked about the key store")

    keystore.set_backend(Forbidden())

    resolved = app_config.resolve(app_config.AISettings(), environ={})
    assert resolved.provider.name == app_config.DEFAULT_PROVIDER
    assert resolved.api_key is None


def test_a_saved_key_is_registered_for_redaction() -> None:
    from opengrader.core.llm.transport import redact

    keystore.store(keystore.account_for(OPENAI), "sk-saved-and-secret-0001")
    app_config.resolve_api_key(OPENAI, environ={})

    assert "sk-saved-and-secret-0001" not in redact(
        "401 from api.openai.com: bad key sk-saved-and-secret-0001"
    )


def test_the_provider_details_say_where_the_key_is(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    rows = dict(ai_manifests.describe_provider(OPENAI))
    assert rows["api key"] == "OPENAI_API_KEY (not set)"

    keystore.store(keystore.account_for(OPENAI), "sk-saved")
    rows = dict(ai_manifests.describe_provider(OPENAI))
    assert rows["api key"] == "saved in a test key store"
    assert "sk-saved" not in str(rows)


# --------------------------------------------------------------------------
# macOS: the real `security` tool, against a keychain file made for the test
# --------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="needs the macOS security tool")
def test_the_macos_backend_round_trips_through_a_real_keychain(tmp_path: Path) -> None:
    # A keychain file of its own: `create-keychain` does not add it to the
    # user's keychain list, so the login keychain is never read or written.
    keychain = tmp_path / "test.keychain-db"
    subprocess.run(
        ["/usr/bin/security", "create-keychain", "-p", "test", str(keychain)], check=True
    )
    try:
        backend = keystore._MacBackend()
        backend.keychain = str(keychain)
        keystore.set_backend(backend)
        account = "openai|api.openai.com"
        # Quotes, a backslash, a space and a `$`: everything that could break
        # the command line the key is handed over on.
        awkward = 'sk-a\\b"c d$e'

        assert keystore.status().available
        assert keystore.lookup(account) is None
        keystore.store(account, awkward, label="Opengrader: OpenAI API key")
        assert keystore.lookup(account) == awkward
        keystore.store(account, "sk-replacement")
        assert keystore.lookup(account) == "sk-replacement"
        assert keystore.delete(account) is True
        assert keystore.delete(account) is False
        assert keystore.lookup(account) is None
    finally:
        subprocess.run(
            ["/usr/bin/security", "delete-keychain", str(keychain)], check=False
        )


def test_the_macos_backend_never_puts_the_key_in_a_command_line(monkeypatch) -> None:
    seen: list[tuple[list[str], str | None]] = []
    saved: dict[str, str] = {}

    def fake_run(argv, *, stdin_text=None):  # noqa: ANN001
        seen.append((list(argv), stdin_text))
        if stdin_text is not None:
            saved["key"] = "sk-secret-value"
            return 0, "", ""
        return 0, saved.get("key", "") + "\n", ""

    monkeypatch.setattr(keystore, "_run", fake_run)
    backend = keystore._MacBackend()
    backend.set("openai|api.openai.com", "sk-secret-value", "label")

    for argv, _stdin in seen:
        assert not any("sk-secret-value" in word for word in argv)
    assert any(stdin and "sk-secret-value" in stdin for _argv, stdin in seen)


# --------------------------------------------------------------------------
# Linux: a stand-in `secret-tool` that follows the real one's manual
# --------------------------------------------------------------------------

_SECRET_TOOL = '''\
import json, pathlib, sys

db = pathlib.Path(__file__).with_name("db.json")
data = json.loads(db.read_text()) if db.exists() else {}
command, args = sys.argv[1], sys.argv[2:]
if command == "store":
    assert args[0].startswith("--label="), args
    args = args[1:]
assert args[0::2] == ["service", "account"], args
key = "|".join(args)
if command == "store":
    data[key] = sys.stdin.read()
    db.write_text(json.dumps(data))
elif command == "lookup":
    if key not in data:
        sys.exit(1)
    sys.stdout.write(data[key])
elif command == "clear":
    data.pop(key, None)
    db.write_text(json.dumps(data))
else:
    sys.exit(2)
'''


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in is a shell script")
def test_the_linux_backend_speaks_secret_tool(tmp_path: Path, monkeypatch) -> None:
    script = tmp_path / "secret_tool.py"
    script.write_text(_SECRET_TOOL, encoding="utf-8")
    tool = tmp_path / "secret-tool"
    # A shell wrapper rather than a `#!python` line: the interpreter's path
    # may hold a space, which a shebang line cannot.
    tool.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    keystore.set_backend(keystore._LinuxBackend())
    account = "openai|api.openai.com"

    assert keystore.status().available
    assert keystore.lookup(account) is None
    keystore.store(account, "sk-linux-1")
    assert keystore.lookup(account) == "sk-linux-1"
    assert keystore.delete(account) is True
    assert keystore.delete(account) is False
    assert keystore.lookup(account) is None


def test_the_linux_backend_names_the_package_when_secret_tool_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    keystore.set_backend(keystore._LinuxBackend())

    status = keystore.status()
    assert not status.available
    assert "libsecret-tools" in status.reason
    with pytest.raises(keystore.KeyStoreError, match="secret-tool"):
        keystore.store("a|b", "sk-1")


def test_the_linux_backend_needs_a_desktop_session(tmp_path: Path, monkeypatch) -> None:
    tool = tmp_path / "secret-tool"
    tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    keystore.set_backend(keystore._LinuxBackend())

    assert "D-Bus" in keystore.status().reason


# --------------------------------------------------------------------------
# Windows: a stand-in advapi32. This checks our side of the calls — the
# struct we fill in and how we read one back — not Windows itself.
# --------------------------------------------------------------------------


class _FakeAdvapi32:
    def __init__(self) -> None:
        self.entries: dict[str, dict] = {}
        self.last_error = 0
        self.freed = 0
        self._alive: list[object] = []

    def CredWriteW(self, reference, flags):  # noqa: ANN001, N802
        credential = reference._obj
        self.entries[credential.TargetName] = {
            "blob": ctypes.string_at(
                credential.CredentialBlob, credential.CredentialBlobSize
            ),
            "type": credential.Type,
            "persist": credential.Persist,
            "comment": credential.Comment,
        }
        return 1

    def CredReadW(self, target, kind, flags, out):  # noqa: ANN001, N802
        entry = self.entries.get(target)
        if entry is None or kind != entry["type"]:
            self.last_error = 1168
            return 0
        pointer = out._obj
        credential = type(pointer)._type_()
        blob = entry["blob"]
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        self._alive += [credential, buffer]
        pointer.contents = credential
        return 1

    def CredDeleteW(self, target, kind, flags):  # noqa: ANN001, N802
        if target not in self.entries:
            self.last_error = 1168
            return 0
        del self.entries[target]
        return 1

    def CredFree(self, pointer):  # noqa: ANN001, N802
        self.freed += 1


def _windows_backend() -> tuple[keystore._WindowsBackend, _FakeAdvapi32]:
    api = _FakeAdvapi32()

    class Backend(keystore._WindowsBackend):
        def _last_error(self) -> int:  # type: ignore[override]
            return api.last_error

    return Backend(api=api), api


def test_the_windows_backend_writes_a_generic_credential_and_reads_it_back() -> None:
    backend, api = _windows_backend()
    keystore.set_backend(backend)
    account = "openai|api.openai.com"

    assert keystore.status().available
    assert keystore.lookup(account) is None
    keystore.store(account, "sk-windows-1", label="Opengrader: OpenAI API key")

    entry = api.entries["Opengrader:openai|api.openai.com"]
    assert entry["blob"] == "sk-windows-1".encode("utf-16-le")
    assert entry["type"] == 1  # CRED_TYPE_GENERIC
    assert entry["persist"] == 2  # CRED_PERSIST_LOCAL_MACHINE
    assert entry["comment"] == "Opengrader: OpenAI API key"

    assert keystore.lookup(account) == "sk-windows-1"
    # Windows allocates what CredReadW returns; it must be handed back.
    assert api.freed == 1
    assert keystore.delete(account) is True
    assert keystore.delete(account) is False
    assert keystore.lookup(account) is None


def test_the_windows_backend_refuses_a_key_too_long_for_credential_manager() -> None:
    backend, api = _windows_backend()
    keystore.set_backend(backend)

    with pytest.raises(keystore.KeyStoreError, match="longer"):
        keystore.store("a|b", "k" * 1281)
    assert api.entries == {}
