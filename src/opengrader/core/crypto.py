"""Passphrase encryption for the artifacts that leave this machine.

Everything Opengrader keeps is a plain file in one folder: student names, ids,
scans of their handwriting, their grades. That is the right design for a tool
with no server — but it means the two artifacts that *travel* carry all of it in
the clear. A grader packet is emailed or copied onto a stick and ends up on
someone else's laptop; a backup archive is the thing an instructor puts on an
external drive and then leaves in a bag.

Redaction already covers the packet's ordinary case (aliases instead of names,
scans left behind). Encryption covers what redaction cannot: a ``--no-redact``
working copy, a packet carrying annotated PDFs, and a backup, which by
definition holds everything.

Format
------
A small, self-describing container so a file found years later can be identified
without this source::

    OGENC1\\n                     magic line
    {json header}\\n              kdf, iterations, salt, nonce prefix, chunk size
    [4-byte length][chunk] ...   AES-256-GCM chunks, each with its 16-byte tag

Design decisions worth keeping:

* **AES-256-GCM, chunked.** Authenticated, so a damaged or tampered file is
  *detected* rather than silently decrypted into garbage that looks like grades.
  Chunking bounds memory: a backup of a 200-student exam is hundreds of
  megabytes of scans, and reading one into RAM twice to encrypt it is how a
  laptop starts swapping mid-backup.
* **Every chunk is bound to its position.** The chunk index and a final-chunk
  flag go into each chunk's authenticated data, so an attacker cannot reorder
  chunks, drop the tail, or splice two archives together — each of which would
  otherwise produce a file that decrypts cleanly and is wrong.
* **Key derivation is stdlib.** ``hashlib.pbkdf2_hmac`` with a random salt and a
  deliberately slow iteration count, so a weak passphrase costs an attacker real
  time. Only the cipher comes from a third-party library, keeping the amount of
  other people's code in the trust path as small as it can be.
* **The passphrase is never a command-line argument.** It would land in shell
  history and in every process listing on the machine. It is prompted for, or
  read from an environment variable for scripted use.

What this is *not*: protection against someone who can read the grading folder
itself. Like ``core/policy.py``, the honest scope statement matters — this
protects the copy in transit and at rest, not the working folder it came from.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

# Identifies an Opengrader-encrypted file, and pins the container version so a
# future incompatible format can refuse an old file clearly instead of failing
# somewhere deep in a decrypt.
MAGIC = b"OGENC1\n"

# Appended to a file name when encrypting: `class-backup-2026-08-06.zip.enc`.
ENCRYPTED_SUFFIX = ".enc"

# PBKDF2 work factor. High on purpose — this runs once per file, so a second of
# the instructor's time buys an attacker guessing a weak passphrase the same
# second on every one of their guesses. Recorded in each file's header, so
# raising it later does not make existing archives unreadable.
KDF_ITERATIONS = 600_000
_KDF_NAME = "pbkdf2-hmac-sha256"
_SALT_BYTES = 16
_KEY_BYTES = 32  # AES-256

# A 12-byte GCM nonce is 4 random bytes shared by the file plus an 8-byte chunk
# counter, so no two chunks can ever reuse one — reuse is the single mistake
# that breaks GCM completely.
_NONCE_PREFIX_BYTES = 4
_COUNTER_BYTES = 8

# 1 MiB of plaintext per chunk: small enough to bound memory, large enough that
# the 16-byte tag per chunk is noise.
CHUNK_SIZE = 1024 * 1024

# The environment variable a script or CI run supplies instead of typing.
PASSPHRASE_ENV_VAR = "OPENGRADER_PASSPHRASE"

_INSTALL_HINT = (
    "Encryption needs the 'cryptography' package. Run: uv sync"
)


class EncryptionError(ValueError):
    """Raised for a wrong passphrase, a damaged file, or a missing library."""


@dataclass(frozen=True)
class EncryptionHeader:
    """The plaintext header describing how a file was encrypted."""

    kdf: str
    iterations: int
    salt: bytes
    nonce_prefix: bytes
    chunk_size: int
    cipher: str = "aes-256-gcm"


def _load_aesgcm():
    """Import the AEAD cipher, turning a missing library into a clear message.

    Imported lazily so that every other command — and ``--help`` — keeps working
    on an install where the wheel failed to build, instead of the whole tool
    refusing to start over a feature most users never touch.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as exc:  # pragma: no cover - only on a broken install
        raise EncryptionError(_INSTALL_HINT) from exc
    return AESGCM


def encryption_available() -> bool:
    """True when this install can encrypt and decrypt (for the doctor report)."""
    from importlib.util import find_spec

    try:
        return find_spec("cryptography") is not None
    except Exception:  # pragma: no cover - defensive
        return False


def derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    """Turn a human passphrase into a 32-byte AES key.

    Stdlib PBKDF2-HMAC-SHA256. The salt makes two people who chose the same
    passphrase produce different keys, so one cracked file tells an attacker
    nothing about the next.
    """
    if not passphrase:
        raise EncryptionError("a passphrase is required")
    return hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), salt, iterations, dklen=_KEY_BYTES
    )


def _render_header(header: EncryptionHeader) -> bytes:
    """Serialize the header to the single JSON line stored in the file."""
    payload = {
        "kdf": header.kdf,
        "iterations": header.iterations,
        "salt": base64.b64encode(header.salt).decode("ascii"),
        "nonce_prefix": base64.b64encode(header.nonce_prefix).decode("ascii"),
        "chunk_size": header.chunk_size,
        "cipher": header.cipher,
    }
    # sort_keys so the bytes are reproducible: the header is authenticated as
    # part of every chunk, so it must serialize identically on the way back in.
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _parse_header(raw: bytes) -> EncryptionHeader:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EncryptionError("encrypted file header is unreadable") from exc
    try:
        header = EncryptionHeader(
            kdf=str(payload["kdf"]),
            iterations=int(payload["iterations"]),
            salt=base64.b64decode(payload["salt"]),
            nonce_prefix=base64.b64decode(payload["nonce_prefix"]),
            chunk_size=int(payload["chunk_size"]),
            cipher=str(payload.get("cipher", "aes-256-gcm")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EncryptionError("encrypted file header is incomplete") from exc

    # Refuse anything we do not actually implement rather than guessing: a file
    # written by a future version must fail loudly, not be half-read.
    if header.kdf != _KDF_NAME or header.cipher != "aes-256-gcm":
        raise EncryptionError(
            f"unsupported encryption ({header.kdf}/{header.cipher}) — "
            "this file needs a newer Opengrader"
        )
    if header.chunk_size <= 0 or header.iterations <= 0:
        raise EncryptionError("encrypted file header has impossible values")
    return header


def _chunk_nonce(prefix: bytes, index: int) -> bytes:
    return prefix + index.to_bytes(_COUNTER_BYTES, "big")


def _chunk_aad(header_bytes: bytes, index: int, *, final: bool) -> bytes:
    """Data authenticated alongside each chunk, but not encrypted.

    Binding the header, the chunk's position and whether it is the last one is
    what stops an attacker reordering chunks, cutting the file short, or gluing
    two archives together — all of which would otherwise decrypt without error.
    """
    return header_bytes + index.to_bytes(_COUNTER_BYTES, "big") + (b"\x01" if final else b"\x00")


def is_encrypted(path: str | Path) -> bool:
    """True when this file is an Opengrader-encrypted container.

    Reads only the magic line, so callers can offer a clear "this needs a
    passphrase" message instead of letting a zip reader report a corrupt file.
    Any unreadable path is simply not encrypted as far as this is concerned.
    """
    try:
        with open(path, "rb") as handle:
            return handle.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def encrypt_file(
    source: str | Path,
    destination: str | Path,
    passphrase: str,
    *,
    iterations: int = KDF_ITERATIONS,
    chunk_size: int = CHUNK_SIZE,
) -> Path:
    """Encrypt ``source`` to ``destination``. Returns the written path.

    Refuses to overwrite: the destination of an encryption is frequently the
    only copy that will be kept, and the source is often deleted straight after.
    """
    AESGCM = _load_aesgcm()
    source_path = Path(source)
    target = Path(destination)
    if target.exists():
        raise EncryptionError(f"{target} already exists — refusing to overwrite it")

    salt = os.urandom(_SALT_BYTES)
    nonce_prefix = os.urandom(_NONCE_PREFIX_BYTES)
    header = EncryptionHeader(
        kdf=_KDF_NAME,
        iterations=iterations,
        salt=salt,
        nonce_prefix=nonce_prefix,
        chunk_size=chunk_size,
    )
    header_bytes = _render_header(header)
    cipher = AESGCM(derive_key(passphrase, salt, iterations))

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(source_path, "rb") as plain, open(target, "wb") as out:
            out.write(MAGIC)
            out.write(header_bytes)
            out.write(b"\n")
            _encrypt_stream(plain, out, cipher, header_bytes, nonce_prefix, chunk_size)
    except OSError:
        # A half-written encrypted file is worse than none: it looks like a
        # backup and is not one.
        target.unlink(missing_ok=True)
        raise
    return target


def _encrypt_stream(
    plain: BinaryIO,
    out: BinaryIO,
    cipher,
    header_bytes: bytes,
    nonce_prefix: bytes,
    chunk_size: int,
) -> None:
    """Write every chunk, marking the last one so truncation is detectable."""
    index = 0
    current = plain.read(chunk_size)
    while True:
        # Read one chunk ahead, because "is this the last chunk?" has to be
        # known *before* the chunk is sealed — the answer is authenticated.
        following = plain.read(chunk_size)
        final = not following
        sealed = cipher.encrypt(
            _chunk_nonce(nonce_prefix, index),
            current,
            _chunk_aad(header_bytes, index, final=final),
        )
        out.write(len(sealed).to_bytes(4, "big"))
        out.write(sealed)
        if final:
            return
        current = following
        index += 1


def decrypt_file(
    source: str | Path,
    destination: str | Path,
    passphrase: str,
) -> Path:
    """Decrypt ``source`` to ``destination``. Returns the written path.

    Raises :class:`EncryptionError` for a wrong passphrase or a file that has
    been altered — the two are deliberately reported the same way, since telling
    an attacker which of the two happened is a hint they should not get.
    """
    AESGCM = _load_aesgcm()
    source_path = Path(source)
    target = Path(destination)
    if target.exists():
        raise EncryptionError(f"{target} already exists — refusing to overwrite it")

    with open(source_path, "rb") as encrypted:
        if encrypted.read(len(MAGIC)) != MAGIC:
            raise EncryptionError(
                f"{source_path} is not an Opengrader-encrypted file"
            )
        header_bytes = _read_header_line(encrypted)
        header = _parse_header(header_bytes)
        cipher = AESGCM(derive_key(passphrase, header.salt, header.iterations))

        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(target, "wb") as plain:
                _decrypt_stream(
                    encrypted, plain, cipher, header_bytes, header.nonce_prefix
                )
        except BaseException:
            # Never leave a partial plaintext behind: half a submissions archive
            # extracted from a damaged file would be mistaken for a real one.
            target.unlink(missing_ok=True)
            raise
    return target


def _read_header_line(handle: BinaryIO) -> bytes:
    """Read the newline-terminated JSON header that follows the magic."""
    raw = bytearray()
    while True:
        byte = handle.read(1)
        if not byte:
            raise EncryptionError("encrypted file ends before its header does")
        if byte == b"\n":
            return bytes(raw)
        raw.extend(byte)
        if len(raw) > 4096:
            raise EncryptionError("encrypted file header is implausibly long")


def _decrypt_stream(
    encrypted: BinaryIO,
    plain: BinaryIO,
    cipher,
    header_bytes: bytes,
    nonce_prefix: bytes,
) -> None:
    index = 0
    saw_final = False
    while True:
        length_bytes = encrypted.read(4)
        if not length_bytes:
            break
        if len(length_bytes) != 4:
            raise EncryptionError("encrypted file is truncated")
        length = int.from_bytes(length_bytes, "big")
        sealed = encrypted.read(length)
        if len(sealed) != length:
            raise EncryptionError("encrypted file is truncated")

        # Which chunk this is meant to be is not stored — it is *asserted*. A
        # chunk only opens against its own index, so a reordered or spliced file
        # fails here rather than decrypting into plausible nonsense.
        chunk, saw_final = _open_chunk(
            cipher, header_bytes, nonce_prefix, index, sealed
        )
        plain.write(chunk)
        if saw_final:
            break
        index += 1

    if not saw_final:
        # Every complete file ends with a chunk marked final. Reaching the end
        # without one means bytes were cut off the tail.
        raise EncryptionError(
            "encrypted file is incomplete — the end of it is missing"
        )


def _open_chunk(
    cipher, header_bytes: bytes, nonce_prefix: bytes, index: int, sealed: bytes
) -> tuple[bytes, bool]:
    """Decrypt one chunk, trying it as a middle chunk and then as the last.

    The final-chunk flag is authenticated but not stored anywhere, so both
    possibilities are attempted; at most one can succeed, because the
    authentication tag covers the flag.
    """
    from cryptography.exceptions import InvalidTag

    nonce = _chunk_nonce(nonce_prefix, index)
    for final in (False, True):
        try:
            opened = cipher.decrypt(
                nonce, sealed, _chunk_aad(header_bytes, index, final=final)
            )
        except InvalidTag:
            continue
        return opened, final
    raise EncryptionError(
        "wrong passphrase, or this file has been damaged or altered"
    )


def passphrases_match(first: str, second: str) -> bool:
    """Constant-time comparison of a passphrase and its confirmation.

    Constant time is habit rather than necessity here (both values are already
    in this process), but comparing secrets with ``==`` is the habit worth not
    having.
    """
    return hmac.compare_digest(first.encode("utf-8"), second.encode("utf-8"))


def passphrase_from_environment() -> str:
    """The scripted-use passphrase, or "" when none is set."""
    return os.environ.get(PASSPHRASE_ENV_VAR, "")


def read_passphrase(*, confirm: bool = False, prompt: str = "Passphrase: ") -> str:
    """Obtain a passphrase without it ever appearing on a command line.

    The environment variable wins, so an unattended backup script works; failing
    that the user is prompted with echo off. There is deliberately **no
    ``--passphrase`` flag** anywhere: a secret passed as an argument is written
    into shell history and is visible to every other process on the machine for
    as long as the command runs.

    ``confirm`` asks twice, for the operations that *create* an artifact. A typo
    when encrypting is unrecoverable — nobody can tell you what you typed — and
    the mistake surfaces the day the original is gone.

    Lives in core rather than in one of the CLIs because two commands need it
    identically, and a second copy is how they would end up disagreeing about
    the environment variable's name.
    """
    from getpass import getpass

    from_environment = passphrase_from_environment()
    if from_environment:
        return from_environment

    first = getpass(prompt)
    if not first:
        raise EncryptionError("no passphrase entered")
    if confirm:
        again = getpass("Confirm passphrase: ")
        if not passphrases_match(first, again):
            raise EncryptionError("the two passphrases do not match")
    return first
