"""Container format: magic line, JSON header, length-prefixed AES-256-GCM chunks.
Each chunk authenticates the header, its index, and whether it is the final chunk.
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

MAGIC = b"OGENC1\n"

ENCRYPTED_SUFFIX = ".enc"

KDF_ITERATIONS = 600_000
_KDF_NAME = "pbkdf2-hmac-sha256"
_SALT_BYTES = 16
_KEY_BYTES = 32

_NONCE_PREFIX_BYTES = 4
_COUNTER_BYTES = 8

CHUNK_SIZE = 1024 * 1024

PASSPHRASE_ENV_VAR = "OPENGRADER_PASSPHRASE"

_INSTALL_HINT = (
    "Encryption needs the 'cryptography' package. Run: uv sync"
)


class EncryptionError(ValueError):
    pass


@dataclass(frozen=True)
class EncryptionHeader:
    kdf: str
    iterations: int
    salt: bytes
    nonce_prefix: bytes
    chunk_size: int
    cipher: str = "aes-256-gcm"


def _load_aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as exc:  # pragma: no cover
        raise EncryptionError(_INSTALL_HINT) from exc
    return AESGCM


def encryption_available() -> bool:
    from importlib.util import find_spec

    try:
        return find_spec("cryptography") is not None
    except Exception:  # pragma: no cover
        return False


def derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    if not passphrase:
        raise EncryptionError("a passphrase is required")
    return hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), salt, iterations, dklen=_KEY_BYTES
    )


def _render_header(header: EncryptionHeader) -> bytes:
    payload = {
        "kdf": header.kdf,
        "iterations": header.iterations,
        "salt": base64.b64encode(header.salt).decode("ascii"),
        "nonce_prefix": base64.b64encode(header.nonce_prefix).decode("ascii"),
        "chunk_size": header.chunk_size,
        "cipher": header.cipher,
    }
    # Serialize deterministically because every chunk authenticates the header bytes.
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
    return header_bytes + index.to_bytes(_COUNTER_BYTES, "big") + (b"\x01" if final else b"\x00")


def is_encrypted(path: str | Path) -> bool:
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
    index = 0
    current = plain.read(chunk_size)
    while True:
        # Read ahead to authenticate whether this is the final chunk.
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
            target.unlink(missing_ok=True)
            raise
    return target


def _read_header_line(handle: BinaryIO) -> bytes:
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

        chunk, saw_final = _open_chunk(
            cipher, header_bytes, nonce_prefix, index, sealed
        )
        plain.write(chunk)
        if saw_final:
            break
        index += 1

    if not saw_final:
        raise EncryptionError(
            "encrypted file is incomplete — the end of it is missing"
        )


def _open_chunk(
    cipher, header_bytes: bytes, nonce_prefix: bytes, index: int, sealed: bytes
) -> tuple[bytes, bool]:
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
    return hmac.compare_digest(first.encode("utf-8"), second.encode("utf-8"))


def passphrase_from_environment() -> str:
    return os.environ.get(PASSPHRASE_ENV_VAR, "")


def read_passphrase(*, confirm: bool = False, prompt: str = "Passphrase: ") -> str:
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
