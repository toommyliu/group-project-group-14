"""Passphrase encryption for artifacts that leave the machine (`core/crypto.py`).

The interesting cases are all failure cases: encryption that cannot detect a
wrong passphrase, a truncated file or a reordered chunk is worse than none,
because it hands back plausible-looking nonsense instead of an error.

A low iteration count is used throughout — these tests exercise the container,
not the key-stretching, and 600,000 rounds per call would make the suite crawl.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opengrader.core.crypto import (
    CHUNK_SIZE,
    MAGIC,
    EncryptionError,
    decrypt_file,
    derive_key,
    encrypt_file,
    encryption_available,
    is_encrypted,
    passphrase_from_environment,
    passphrases_match,
    read_passphrase,
)

FAST = {"iterations": 1000}


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_round_trip_returns_the_original_bytes(tmp_path: Path) -> None:
    source = _write(tmp_path / "in.bin", b"grades and names" * 100)
    encrypt_file(source, tmp_path / "out.enc", "hunter2", **FAST)
    decrypt_file(tmp_path / "out.enc", tmp_path / "back.bin", "hunter2")
    assert (tmp_path / "back.bin").read_bytes() == source.read_bytes()


def test_an_empty_file_round_trips(tmp_path: Path) -> None:
    # An empty input still has to produce one final-marked chunk, or decryption
    # would report the file as incomplete.
    source = _write(tmp_path / "in.bin", b"")
    encrypt_file(source, tmp_path / "out.enc", "p", **FAST)
    decrypt_file(tmp_path / "out.enc", tmp_path / "back.bin", "p")
    assert (tmp_path / "back.bin").read_bytes() == b""


def test_a_multi_chunk_file_round_trips(tmp_path: Path) -> None:
    # Larger than one chunk, and deliberately not a whole multiple of it, so the
    # last chunk is short — the case the final-chunk marker has to get right.
    data = bytes(range(256)) * ((CHUNK_SIZE * 2) // 256 + 7)
    source = _write(tmp_path / "in.bin", data)
    encrypt_file(source, tmp_path / "out.enc", "p", **FAST)
    decrypt_file(tmp_path / "out.enc", tmp_path / "back.bin", "p")
    assert (tmp_path / "back.bin").read_bytes() == data


def test_the_plaintext_does_not_survive_in_the_encrypted_file(tmp_path: Path) -> None:
    source = _write(tmp_path / "in.bin", b"Ada Lovelace 1001 scored 18/20")
    encrypt_file(source, tmp_path / "out.enc", "p", **FAST)
    blob = (tmp_path / "out.enc").read_bytes()
    assert b"Ada Lovelace" not in blob
    assert b"1001" not in blob


def test_encrypted_files_are_identifiable_without_the_passphrase(tmp_path: Path) -> None:
    # Callers use this to say "this needs a passphrase" instead of letting a zip
    # reader report a corrupt file.
    plain = _write(tmp_path / "plain.zip", b"PK\x03\x04 not encrypted")
    encrypt_file(plain, tmp_path / "out.enc", "p", **FAST)
    assert is_encrypted(tmp_path / "out.enc")
    assert not is_encrypted(plain)
    assert not is_encrypted(tmp_path / "does-not-exist")
    assert (tmp_path / "out.enc").read_bytes().startswith(MAGIC)


def test_the_same_passphrase_twice_produces_different_files(tmp_path: Path) -> None:
    # A fresh random salt and nonce each time: identical ciphertexts would leak
    # that two backups of the same folder are unchanged.
    source = _write(tmp_path / "in.bin", b"same content")
    encrypt_file(source, tmp_path / "a.enc", "p", **FAST)
    encrypt_file(source, tmp_path / "b.enc", "p", **FAST)
    assert (tmp_path / "a.enc").read_bytes() != (tmp_path / "b.enc").read_bytes()


# --- failure cases ----------------------------------------------------------


def test_a_wrong_passphrase_is_refused(tmp_path: Path) -> None:
    source = _write(tmp_path / "in.bin", b"secret")
    encrypt_file(source, tmp_path / "out.enc", "right", **FAST)
    with pytest.raises(EncryptionError):
        decrypt_file(tmp_path / "out.enc", tmp_path / "back.bin", "wrong")


def test_a_failed_decrypt_leaves_no_partial_file(tmp_path: Path) -> None:
    # Half a submissions archive would be mistaken for a real one.
    source = _write(tmp_path / "in.bin", b"secret" * 1000)
    encrypt_file(source, tmp_path / "out.enc", "right", **FAST)
    with pytest.raises(EncryptionError):
        decrypt_file(tmp_path / "out.enc", tmp_path / "back.bin", "wrong")
    assert not (tmp_path / "back.bin").exists()


def test_a_tampered_byte_is_detected(tmp_path: Path) -> None:
    source = _write(tmp_path / "in.bin", b"points: 18" * 500)
    encrypt_file(source, tmp_path / "out.enc", "p", **FAST)
    blob = bytearray((tmp_path / "out.enc").read_bytes())
    blob[len(blob) // 2] ^= 0x01
    (tmp_path / "tampered.enc").write_bytes(bytes(blob))
    with pytest.raises(EncryptionError):
        decrypt_file(tmp_path / "tampered.enc", tmp_path / "back.bin", "p")


def test_a_truncated_file_is_detected(tmp_path: Path) -> None:
    source = _write(tmp_path / "in.bin", b"x" * 5000)
    encrypt_file(source, tmp_path / "out.enc", "p", **FAST)
    blob = (tmp_path / "out.enc").read_bytes()
    (tmp_path / "short.enc").write_bytes(blob[: len(blob) - 40])
    with pytest.raises(EncryptionError):
        decrypt_file(tmp_path / "short.enc", tmp_path / "back.bin", "p")


def test_dropping_a_whole_trailing_chunk_is_detected(tmp_path: Path) -> None:
    """The case the final-chunk marker exists for.

    Cutting a file at a chunk boundary leaves every remaining chunk perfectly
    valid — only the missing "this is the last one" marker reveals the loss.
    """
    source = _write(tmp_path / "in.bin", b"y" * (CHUNK_SIZE * 2 + 100))
    encrypt_file(source, tmp_path / "out.enc", "p", **FAST)
    raw = (tmp_path / "out.enc").read_bytes()

    # Walk the framing and cut exactly after the first chunk.
    end_of_header = raw.index(b"\n", len(MAGIC)) + 1
    first_length = int.from_bytes(raw[end_of_header : end_of_header + 4], "big")
    cut = end_of_header + 4 + first_length
    (tmp_path / "cut.enc").write_bytes(raw[:cut])

    with pytest.raises(EncryptionError, match="incomplete"):
        decrypt_file(tmp_path / "cut.enc", tmp_path / "back.bin", "p")


def test_a_plain_file_is_not_mistaken_for_an_encrypted_one(tmp_path: Path) -> None:
    plain = _write(tmp_path / "plain.zip", b"PK\x03\x04 ordinary zip")
    with pytest.raises(EncryptionError, match="not an Opengrader-encrypted file"):
        decrypt_file(plain, tmp_path / "back.bin", "p")


def test_neither_direction_overwrites_an_existing_file(tmp_path: Path) -> None:
    # The destination of an encryption is often the only copy that gets kept,
    # and the source is frequently deleted straight after.
    source = _write(tmp_path / "in.bin", b"data")
    _write(tmp_path / "taken.enc", b"do not clobber me")
    with pytest.raises(EncryptionError, match="already exists"):
        encrypt_file(source, tmp_path / "taken.enc", "p", **FAST)
    assert (tmp_path / "taken.enc").read_bytes() == b"do not clobber me"

    encrypt_file(source, tmp_path / "ok.enc", "p", **FAST)
    _write(tmp_path / "there.bin", b"existing")
    with pytest.raises(EncryptionError, match="already exists"):
        decrypt_file(tmp_path / "ok.enc", tmp_path / "there.bin", "p")


def test_an_empty_passphrase_is_refused(tmp_path: Path) -> None:
    source = _write(tmp_path / "in.bin", b"data")
    with pytest.raises(EncryptionError):
        encrypt_file(source, tmp_path / "out.enc", "", **FAST)


# --- key derivation and helpers ---------------------------------------------


def test_the_salt_makes_identical_passphrases_produce_different_keys() -> None:
    assert derive_key("p", b"salt-one---------", 1000) != derive_key(
        "p", b"salt-two---------", 1000
    )


def test_key_derivation_is_deterministic_for_the_same_inputs() -> None:
    assert derive_key("p", b"same-salt-------", 1000) == derive_key(
        "p", b"same-salt-------", 1000
    )


def test_passphrase_comparison_matches_only_identical_text() -> None:
    assert passphrases_match("correct horse", "correct horse")
    assert not passphrases_match("correct horse", "correct horses")


def test_encryption_is_available_in_this_install() -> None:
    # A base dependency, so its absence means the install is incomplete.
    assert encryption_available()


def test_the_environment_variable_supplies_a_passphrase_for_scripts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENGRADER_PASSPHRASE", "from-the-environment")
    assert passphrase_from_environment() == "from-the-environment"
    # No prompt is shown when the variable is set, so an unattended run works.
    assert read_passphrase(confirm=True) == "from-the-environment"


def test_no_environment_variable_means_no_passphrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENGRADER_PASSPHRASE", raising=False)
    assert passphrase_from_environment() == ""


def test_a_mismatched_confirmation_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENGRADER_PASSPHRASE", raising=False)
    answers = iter(["first", "second"])
    monkeypatch.setattr("getpass.getpass", lambda *_args, **_kwargs: next(answers))
    with pytest.raises(EncryptionError, match="do not match"):
        read_passphrase(confirm=True)
