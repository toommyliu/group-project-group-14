"""Where a pasted API key is kept: the operating system's own password store.

Opengrader's rule is that a key is never written to one of its files — not
`config.json`, not a provider manifest. Until this module existed that left
one way to supply a key: an environment variable, which someone who starts
Opengrader from a desktop icon has no reasonable way to set. A key saved here
goes to the store the OS already protects with the user's login:

* macOS   — the login Keychain, through the `security` tool.
* Windows — Credential Manager, through `advapi32` (`CredWriteW` & co.).
* Linux   — the Secret Service (GNOME Keyring, KWallet), through `secret-tool`.

No third-party package is involved, on purpose: the installed app has only
`[project].dependencies`, and every one of these is part of the OS (or, on
Linux, one small distro package this module names when it is missing).

An entry is filed under the provider's name AND the host its manifest sends
to (`account_for`). A manifest that is later edited or replaced to point
somewhere else therefore does not inherit the key: it reads as "no key".

Nothing here raises to a caller that is only reading. `lookup` answers None
for "no key", "no store" and "the store failed" alike; `status()` says which.
Only `store` raises (`KeyStoreError`), because the person pressing Save has
to be told it did not work.

Stdlib only, and nothing platform-specific is imported at module scope: this
is reached from `app_config.resolve()`, which runs at TUI startup.
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "KeyStoreError",
    "KeyStoreStatus",
    "MemoryBackend",
    "account_for",
    "clear_cache",
    "delete",
    "lookup",
    "set_backend",
    "status",
    "store",
]

# What the entries are filed under. Changing either orphans every saved key.
SERVICE = "Opengrader AI key"
_LINUX_SERVICE = "opengrader"
_WINDOWS_PREFIX = "Opengrader:"

COMMAND_TIMEOUT_SECONDS = 15.0

# Credential Manager's limit for a generic credential (CRED_MAX_CREDENTIAL_BLOB_SIZE).
_WINDOWS_MAX_BLOB_BYTES = 5 * 512


class KeyStoreError(RuntimeError):
    """The key could not be saved. The message is safe to show: it never holds the key."""


@dataclass(frozen=True)
class KeyStoreStatus:
    available: bool
    # How to name the store to a person, e.g. "the macOS Keychain".
    name: str
    # Why it cannot be used, and what to do about it. Empty when available.
    reason: str = ""


class _Backend(Protocol):
    name: str

    def unavailable_reason(self) -> str: ...

    def get(self, account: str) -> str | None: ...

    def set(self, account: str, secret: str, label: str) -> None: ...

    def remove(self, account: str) -> bool: ...


def _run(argv: list[str], *, stdin_text: str | None = None) -> tuple[int, str, str]:
    """Run a store's own tool. Raises KeyStoreError if it cannot start or hangs.

    A hang is real, not theoretical: a locked keychain asks for the login
    password in a window, and on a machine with no desktop session nobody
    answers it.
    """
    import subprocess

    try:
        proc = subprocess.run(
            argv,
            shell=False,
            input=stdin_text,
            stdin=None if stdin_text is not None else subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise KeyStoreError(f"{argv[0]} was not found on this computer.") from exc
    except subprocess.TimeoutExpired as exc:
        raise KeyStoreError(
            f"{argv[0]} did not answer within {COMMAND_TIMEOUT_SECONDS:g} seconds. "
            "It may be waiting for a password window that is hidden behind this one."
        ) from exc
    except (OSError, ValueError) as exc:
        raise KeyStoreError(f"{argv[0]} could not start: {exc}") from exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _first_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[0][:200] if lines else ""


class _MacBackend:
    """The login Keychain, through `/usr/bin/security`.

    `security` is given its command on stdin (`security -i`) rather than as
    arguments when it carries the key, because another user's `ps` can read a
    process's arguments for as long as it runs.
    """

    name = "the macOS Keychain"
    tool = "/usr/bin/security"
    # A specific keychain file instead of the user's default one. Only a
    # verification script sets this, so that it never touches the real one.
    keychain: str | None = None

    _NOT_FOUND = 44  # errSecItemNotFound, as `security` reports it

    def _target(self) -> list[str]:
        return [self.keychain] if self.keychain else []

    def unavailable_reason(self) -> str:
        if not os.path.exists(self.tool):
            return f"{self.tool} is missing, so the Keychain cannot be reached."
        return ""

    def get(self, account: str) -> str | None:
        code, out, err = _run(
            [self.tool, "find-generic-password", "-s", SERVICE, "-a", account, "-w"]
            + self._target()
        )
        if code == self._NOT_FOUND:
            return None
        if code != 0:
            raise KeyStoreError(_first_line(err) or f"security exited with status {code}.")
        # `-w` prints the password and a newline; a key never ends in one.
        return out.rstrip("\r\n") or None

    @staticmethod
    def _quote(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def set(self, account: str, secret: str, label: str) -> None:
        # -U: replace an existing entry for this service + account.
        words = [
            "add-generic-password",
            "-U",
            "-s",
            self._quote(SERVICE),
            "-a",
            self._quote(account),
            "-l",
            self._quote(label or SERVICE),
            "-w",
            self._quote(secret),
        ] + [self._quote(path) for path in self._target()]
        code, _out, err = _run([self.tool, "-i"], stdin_text=" ".join(words) + "\n")
        if code != 0:
            raise KeyStoreError(_first_line(err) or f"security exited with status {code}.")
        # `security -i` reports a failed command on stderr but still exits 0,
        # so the only proof the key was stored is reading it back.
        if self.get(account) != secret:
            raise KeyStoreError(
                "the Keychain did not keep the key"
                + (f": {_first_line(err)}" if _first_line(err) else ".")
            )

    def remove(self, account: str) -> bool:
        code, _out, err = _run(
            [self.tool, "delete-generic-password", "-s", SERVICE, "-a", account]
            + self._target()
        )
        if code == self._NOT_FOUND:
            return False
        if code != 0:
            raise KeyStoreError(_first_line(err) or f"security exited with status {code}.")
        return True


class _LinuxBackend:
    """The Secret Service (GNOME Keyring, KWallet…), through `secret-tool`.

    NOT YET RUN ON A REAL LINUX DESKTOP. Written from secret-tool's manual:
    `store` reads the secret from stdin, `lookup` prints it, `clear` removes
    it, and both `lookup` and `clear` exit non-zero when nothing matched.
    """

    name = "the system keyring"
    tool = "secret-tool"

    def _attributes(self, account: str) -> list[str]:
        return ["service", _LINUX_SERVICE, "account", account]

    def unavailable_reason(self) -> str:
        import shutil

        if shutil.which(self.tool) is None:
            return (
                "The secret-tool program is not installed. Install it with "
                "`sudo apt install libsecret-tools` (Debian, Ubuntu), "
                "`sudo dnf install libsecret` (Fedora) or "
                "`sudo pacman -S libsecret` (Arch), then try again."
            )
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            return (
                "No desktop keyring is running in this session (there is no "
                "D-Bus session), so there is nowhere safe to keep a key."
            )
        return ""

    def get(self, account: str) -> str | None:
        code, out, err = _run([self.tool, "lookup", *self._attributes(account)])
        if code != 0:
            # secret-tool exits 1 for "nothing matched" and for a real failure
            # alike; only a message on stderr tells them apart.
            if err.strip():
                raise KeyStoreError(_first_line(err))
            return None
        return out.rstrip("\r\n") or None

    def set(self, account: str, secret: str, label: str) -> None:
        code, _out, err = _run(
            [self.tool, "store", f"--label={label or SERVICE}", *self._attributes(account)],
            stdin_text=secret,
        )
        if code != 0:
            raise KeyStoreError(
                _first_line(err) or f"secret-tool exited with status {code}."
            )
        if self.get(account) != secret:
            raise KeyStoreError("the keyring did not keep the key.")

    def remove(self, account: str) -> bool:
        if self.get(account) is None:
            return False
        code, _out, err = _run([self.tool, "clear", *self._attributes(account)])
        if code != 0:
            raise KeyStoreError(
                _first_line(err) or f"secret-tool exited with status {code}."
            )
        return True


class _WindowsBackend:
    """Windows Credential Manager, as a "generic credential".

    NOT YET RUN ON REAL WINDOWS. Written from the Win32 documentation for
    `CredWriteW` / `CredReadW` / `CredDeleteW` / `CredFree` in advapi32.dll.
    The saved entry is visible (and removable) in Control Panel → Credential
    Manager → Windows Credentials, under "Opengrader:<provider>|<host>".

    The key is stored as UTF-16-LE text, which is what Credential Manager's
    own tools and other programs expect of a generic credential.
    """

    name = "Windows Credential Manager"

    _CRED_TYPE_GENERIC = 1
    # Kept for this user on this computer; not roamed to other machines.
    _CRED_PERSIST_LOCAL_MACHINE = 2
    _ERROR_NOT_FOUND = 1168

    def __init__(self, api: Any = None) -> None:
        # `api` is advapi32 itself; a test passes a stand-in.
        self._api = api
        self._credential_type: Any = None

    def unavailable_reason(self) -> str:
        try:
            self._load()
        except Exception as exc:  # noqa: BLE001 - any failure means "no store"
            return f"Windows Credential Manager could not be opened: {exc}"
        return ""

    def _load(self) -> tuple[Any, Any]:
        """advapi32 with its four functions typed, and the CREDENTIALW struct."""
        import ctypes
        from ctypes import wintypes

        if self._credential_type is None:

            class CREDENTIALW(ctypes.Structure):
                _fields_ = [
                    ("Flags", wintypes.DWORD),
                    ("Type", wintypes.DWORD),
                    ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR),
                    ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD),
                    ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR),
                    ("UserName", wintypes.LPWSTR),
                ]

            self._credential_type = CREDENTIALW

        if self._api is None:
            # use_last_error: read the failure code with ctypes.get_last_error(),
            # which nothing else on this thread can overwrite in between.
            api = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
            pointer = ctypes.POINTER(self._credential_type)
            api.CredWriteW.argtypes = [pointer, wintypes.DWORD]
            api.CredWriteW.restype = wintypes.BOOL
            api.CredReadW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(pointer),
            ]
            api.CredReadW.restype = wintypes.BOOL
            api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
            api.CredDeleteW.restype = wintypes.BOOL
            api.CredFree.argtypes = [ctypes.c_void_p]
            api.CredFree.restype = None
            self._api = api
        return self._api, self._credential_type

    @staticmethod
    def _last_error() -> int:
        import ctypes

        getter = getattr(ctypes, "get_last_error", None)
        return int(getter()) if getter is not None else 0

    def _failure(self, action: str) -> KeyStoreError:
        import ctypes

        code = self._last_error()
        formatter = getattr(ctypes, "FormatError", None)
        detail = formatter(code).strip() if formatter is not None else ""
        return KeyStoreError(
            f"Windows could not {action} the key (error {code}"
            + (f": {detail})" if detail else ").")
        )

    def get(self, account: str) -> str | None:
        import ctypes

        api, credential_type = self._load()
        found = ctypes.POINTER(credential_type)()
        ok = api.CredReadW(
            _WINDOWS_PREFIX + account, self._CRED_TYPE_GENERIC, 0, ctypes.byref(found)
        )
        if not ok:
            if self._last_error() == self._ERROR_NOT_FOUND:
                return None
            raise self._failure("read")
        try:
            credential = found.contents
            size = int(credential.CredentialBlobSize)
            if size <= 0 or not credential.CredentialBlob:
                return None
            raw = ctypes.string_at(credential.CredentialBlob, size)
        finally:
            # Windows allocated the struct; it has to be handed back.
            api.CredFree(found)
        try:
            return raw.decode("utf-16-le") or None
        except UnicodeDecodeError:
            return None

    def set(self, account: str, secret: str, label: str) -> None:
        import ctypes

        api, credential_type = self._load()
        blob = secret.encode("utf-16-le")
        if len(blob) > _WINDOWS_MAX_BLOB_BYTES:
            raise KeyStoreError(
                "this key is longer than Windows Credential Manager can hold "
                f"({_WINDOWS_MAX_BLOB_BYTES // 2} characters)."
            )
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        credential = credential_type()
        credential.Type = self._CRED_TYPE_GENERIC
        credential.TargetName = _WINDOWS_PREFIX + account
        credential.Comment = label or SERVICE
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = self._CRED_PERSIST_LOCAL_MACHINE
        # Shown in Credential Manager's "User name" column; not a login.
        credential.UserName = "Opengrader"
        if not api.CredWriteW(ctypes.byref(credential), 0):
            raise self._failure("save")

    def remove(self, account: str) -> bool:
        api, _credential_type = self._load()
        if api.CredDeleteW(_WINDOWS_PREFIX + account, self._CRED_TYPE_GENERIC, 0):
            return True
        if self._last_error() == self._ERROR_NOT_FOUND:
            return False
        raise self._failure("remove")


class MemoryBackend:
    """Keys in a dict, gone when the process ends. For tests and verification scripts."""

    name = "a test key store"

    def __init__(self, *, reason: str = "") -> None:
        self.entries: dict[str, str] = {}
        self._reason = reason

    def unavailable_reason(self) -> str:
        return self._reason

    def get(self, account: str) -> str | None:
        return self.entries.get(account)

    def set(self, account: str, secret: str, label: str) -> None:
        self.entries[account] = secret

    def remove(self, account: str) -> bool:
        return self.entries.pop(account, None) is not None


class _NoBackend:
    name = "a system key store"

    def unavailable_reason(self) -> str:
        return "Opengrader does not know this operating system's key store."

    def get(self, account: str) -> str | None:
        return None

    def set(self, account: str, secret: str, label: str) -> None:
        raise KeyStoreError(self.unavailable_reason())

    def remove(self, account: str) -> bool:
        return False


_LOCK = threading.Lock()
_OVERRIDE: _Backend | None = None
_DEFAULT: _Backend | None = None
# account -> key (or None for "looked, nothing there"). Per process: a TUI
# resolves its AI settings many times a minute and must not start a
# `security` process each time. Dropped by store/delete/clear_cache.
_CACHE: dict[str, str | None] = {}


def _platform_backend() -> _Backend:
    if sys.platform == "darwin":
        return _MacBackend()
    if os.name == "nt":
        return _WindowsBackend()
    if sys.platform.startswith(("linux", "freebsd", "openbsd")):
        return _LinuxBackend()
    return _NoBackend()


def _backend() -> _Backend:
    global _DEFAULT
    with _LOCK:
        if _OVERRIDE is not None:
            return _OVERRIDE
        if _DEFAULT is None:
            _DEFAULT = _platform_backend()
        return _DEFAULT


def set_backend(backend: _Backend | None) -> None:
    """Replace the OS store (tests, verification scripts); None restores it."""
    global _OVERRIDE
    with _LOCK:
        _OVERRIDE = backend
        _CACHE.clear()


def clear_cache() -> None:
    """Forget what was read, so the next lookup asks the store again."""
    with _LOCK:
        _CACHE.clear()


def status() -> KeyStoreStatus:
    backend = _backend()
    try:
        reason = backend.unavailable_reason()
    except Exception as exc:  # noqa: BLE001 - a status check never raises
        reason = str(exc)
    return KeyStoreStatus(available=not reason, name=backend.name, reason=reason)


def account_for(provider: Any) -> str:
    """The entry a provider's key is filed under: its name and the host it sends to.

    Tying the entry to the host is the point. A provider file that is edited
    or replaced to send somewhere else gets a different account, finds
    nothing, and so cannot carry a saved key off to a new address.
    """
    from .consent import hostname_of

    host = hostname_of(getattr(provider, "default_host", "") or "") or "-"
    return f"{provider.name}|{host}"


def lookup(account: str) -> str | None:
    """The saved key, or None. Never raises; a failing store reads as "no key"."""
    with _LOCK:
        if account in _CACHE:
            return _CACHE[account]
    backend = _backend()
    value: str | None
    try:
        value = None if backend.unavailable_reason() else backend.get(account)
    except Exception:  # noqa: BLE001 - reading a key must never break a resolve
        value = None
    value = (value or "").strip() or None
    with _LOCK:
        _CACHE[account] = value
    return value


def store(account: str, secret: str, *, label: str = "") -> None:
    """Save `secret` under `account`, replacing any earlier one. Raises KeyStoreError."""
    cleaned = secret.strip()
    if not cleaned:
        raise KeyStoreError("there is no key to save — the box is empty.")
    if any(ord(char) < 32 or ord(char) == 127 for char in cleaned):
        # A key is one line of printable text. A line break in the middle
        # means two things were pasted, and it would be cut at the break.
        raise KeyStoreError(
            "the key has a line break or another invisible character in it. "
            "Copy the key again, on its own."
        )
    backend = _backend()
    reason = backend.unavailable_reason()
    if reason:
        raise KeyStoreError(reason)
    try:
        backend.set(account, cleaned, label)
    except KeyStoreError:
        raise
    except Exception as exc:  # noqa: BLE001 - one error type for the caller
        raise KeyStoreError(f"{backend.name} refused the key: {exc}") from exc
    finally:
        clear_cache()


def delete(account: str) -> bool:
    """Remove a saved key. True if there was one. Raises KeyStoreError on a real failure."""
    backend = _backend()
    try:
        if backend.unavailable_reason():
            return False
        return bool(backend.remove(account))
    except KeyStoreError:
        raise
    except Exception as exc:  # noqa: BLE001 - one error type for the caller
        raise KeyStoreError(f"{backend.name} could not remove the key: {exc}") from exc
    finally:
        clear_cache()
