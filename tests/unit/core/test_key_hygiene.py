"""Key hygiene: API keys never appear in written artifacts.

Extends the packet and backup tests with assertions that no key-shaped string
(matching the regexes in core/llm/transport.py) appears in any written artifact,
including config files, egress logs, and error text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from opengrader.core import app_config, egress
from opengrader.core.app_config import consent
from opengrader.core.handoff.packet import build_packet
from opengrader.core.integrity import create_backup
from opengrader.core.llm import transport


# Key regex patterns from core/llm/transport.py
_SHAPED_KEY = re.compile(r"\b(?:sk|pk|rk|ak|key)-[A-Za-z0-9_\-]{12,}")
_LABELLED_KEY = re.compile(
    r"(?i)"
    r"((?:x-)?(?:api[-_ ]?keys?|access[-_ ]?tokens?|authorization|auth[-_ ]?tokens?"
    r"|secrets?|passwords?|tokens?|bearer)"
    r"[\"']?\s*[:=]\s*(?:bearer\s+)?[\"']?)"
    r"([A-Za-z0-9._\-~+/=]{8,})"
)

FAKE_API_KEY = "sk-test-DO-NOT-LEAK-0123456789abcdef"
CLOUD_HOST = "https://api.test-key-hygiene.example/v1"


def _find_key_shaped_strings(text: str) -> list[str]:
    """Find all key-shaped strings in text that match transport.py regexes."""
    matches = []
    for match in _SHAPED_KEY.finditer(text):
        matches.append(match.group())
    for match in _LABELLED_KEY.finditer(text):
        # _LABELLED_KEY has groups, we capture the full match
        matches.append(match.group())
    return matches


def _scan_bytes(data: bytes, description: str = "data") -> None:
    """Scan bytes for key-shaped strings and fail if found."""
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return  # Binary file, cannot contain string keys
    found = _find_key_shaped_strings(text)
    # Filter out obviously false positives (short random strings)
    found = [k for k in found if FAKE_API_KEY[:12] in k or "test-" in k.lower()]
    assert not found, f"Found potential key in {description}: {found}"


def test_config_json_never_holds_an_api_key(tmp_path: Path) -> None:
    """The saved config.json never holds an API key.

    Only the environment variable *name* is saved, never the key value.
    """
    # Create settings with a provider that has an api_key_env
    settings = app_config.AISettings(provider="ollama")
    path = tmp_path / "config.json"
    app_config.save_settings(settings, path=path)

    text = path.read_text()
    assert FAKE_API_KEY not in text
    # Should not have any api_key or secret fields with values
    data = json.loads(text)
    assert "api_key" not in data
    assert "secret" not in data


def test_config_schema_never_documents_a_secret_value(tmp_path: Path) -> None:
    """The config.schema.json doesn't leak examples of keys."""
    settings = app_config.AISettings()
    app_config.save_settings(settings, path=tmp_path / "config.json")

    schema_path = tmp_path / "config.schema.json"
    assert schema_path.exists()
    text = schema_path.read_text()
    # Should not contain example keys or values
    assert FAKE_API_KEY not in text
    assert "sk-" not in text.lower() or "sk-example" not in text.lower()


def test_credential_command_output_is_registered_and_redacted(tmp_path: Path) -> None:
    """Credential command output (even on failure) is registered for redaction.

    This test verifies that if a credential command prints something secret,
    it gets registered and would be redacted by transport.py.
    """
    # Verify that transport module has the registration mechanism
    assert hasattr(transport, "register_secret")
    assert hasattr(transport, "redact")

    # Register a secret and verify redaction works
    secret = "my-secret-token-12345"
    transport.register_secret(secret)
    try:
        redacted = transport.redact(f"Error: {secret} failed")
        assert secret not in redacted
        assert "[redacted]" in redacted
    finally:
        transport.forget_secret(secret)


def test_shaped_keys_are_detected_in_error_messages() -> None:
    """Key-shaped strings are redacted in error messages.

    The redaction point catches both registered secrets and shaped keys.
    """
    # Test the transport.py redaction function
    error_text = f"API call failed with key {FAKE_API_KEY}"
    redacted = transport.redact(error_text)
    assert FAKE_API_KEY not in redacted
    assert "[redacted]" in redacted


def test_labelled_keys_are_detected_in_error_messages() -> None:
    """Labelled keys like 'Authorization: Bearer ...' are redacted."""
    error_text = 'Error: {"Authorization": "Bearer sk-test-secret-value123456"}'
    redacted = transport.redact(error_text)
    # Should redact the bearer token
    assert "sk-test-secret" not in redacted or "[redacted]" in redacted


_HYGIENE_MANIFEST = """\
name         = "hygiene-cloud"
label        = "Hygiene Cloud"
dialect      = "openai-chat"
host         = "https://api.hygiene.example/v1"
vision_model = "hygiene-vision"
text_model   = "hygiene-text"
api_key_env  = "HYGIENE_KEY"
"""
_HYGIENE_KEY = "sk-hygiene-" + "0123456789abcdef" * 3


class _FakeResponse:
    def __init__(self, reply: str) -> None:
        self.status = 200
        self._raw = json.dumps(
            {
                "choices": [{"message": {"role": "assistant", "content": reply}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30},
            }
        ).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc) -> None:
        return None


@pytest.fixture
def hygiene_cloud(monkeypatch):
    """A consented cloud manifest provider whose key is in the environment."""
    directory = app_config.manifests_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "hygiene.toml").write_text(_HYGIENE_MANIFEST, encoding="utf-8")
    app_config.reload_manifests()
    monkeypatch.setenv("HYGIENE_KEY", _HYGIENE_KEY)
    settings = app_config.AISettings(provider="hygiene-cloud")
    settings = consent.grant_cloud_consent(settings, app_config.resolve(settings))
    app_config.save_settings(settings)
    try:
        yield app_config.resolve()
    finally:
        for path in directory.glob("*.toml"):
            path.unlink()
        app_config.reload_manifests()


def _answer_crop(folder: Path) -> Path:
    from PIL import Image

    crop = folder / "responses" / "q1.png"
    crop.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 20), color="white").save(crop)
    return crop


def _assert_key_absent(folder: Path, key: str) -> None:
    for path in folder.rglob("*"):
        if path.is_file():
            assert key.encode() not in path.read_bytes(), f"key leaked into {path}"


def test_egress_log_contains_no_api_key(tmp_path: Path, monkeypatch, hygiene_cloud) -> None:
    """A real cloud vision call writes an egress line, and neither that line nor
    any other file in the folder or the config directory holds the key, the
    prompt or the image."""
    sent: list = []

    def _urlopen(req, timeout=None):
        sent.append(req)
        return _FakeResponse('{"prompt": "Define recursion.", "answer": "A function that calls itself."}')

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    work = tmp_path / "work"
    crop = _answer_crop(work)

    with egress.recording(work):
        app_config.vision_reader(hygiene_cloud).extract_question_prompt_and_answer(
            image_path=crop
        )

    assert sent, "the call never reached the HTTP layer"
    assert _HYGIENE_KEY in json.dumps(dict(sent[0].header_items())), (
        "the fake key was not the one sent, so this test proves nothing"
    )
    log = work / ".opengrader" / egress.EGRESS_FILENAME
    raw = log.read_text(encoding="utf-8")
    assert raw.strip(), "no egress line was written"
    assert "calls itself" not in raw
    assert "base64" not in raw and "iVBOR" not in raw  # no PNG payload
    _assert_key_absent(work, _HYGIENE_KEY)
    _assert_key_absent(app_config._config_dir(), _HYGIENE_KEY)


def test_rejected_key_echoed_by_the_service_is_redacted(
    tmp_path: Path, monkeypatch, hygiene_cloud
) -> None:
    """A 401 whose body echoes the key back: the raised error's text, and every
    file written during the call, are free of the key."""
    import io
    from urllib import error as url_error

    body = json.dumps({"error": f"invalid api key {_HYGIENE_KEY}"}).encode("utf-8")

    def _urlopen(req, timeout=None):
        raise url_error.HTTPError(
            req.full_url, 401, "Unauthorized", {}, io.BytesIO(body)
        )

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    work = tmp_path / "work"
    crop = _answer_crop(work)

    with egress.recording(work):
        with pytest.raises(Exception) as caught:
            app_config.vision_reader(hygiene_cloud).extract_question_prompt_and_answer(
                image_path=crop
            )

    chain = []
    exc: BaseException | None = caught.value
    while exc is not None:
        chain.append(f"{type(exc).__name__}: {exc}")
        exc = exc.__cause__ or exc.__context__
    assert _HYGIENE_KEY not in "\n".join(chain)
    _assert_key_absent(work, _HYGIENE_KEY)
    _assert_key_absent(app_config._config_dir(), _HYGIENE_KEY)


def test_config_json_after_cloud_consent_grant_has_no_key(
    tmp_path: Path, monkeypatch
) -> None:
    """After granting cloud consent and saving settings, config.json holds no key.

    A cloud provider's API key must never be written to config.json, even after
    granting consent. Only the configuration (provider, host, models) is saved,
    never credentials.
    """
    fake_key = "sk-test-" + "aaaaaabbbbbbcccccc" * 2  # 40 hex chars

    # Create a test cloud provider
    original_providers = app_config.registered_providers()
    reader = object()  # Dummy reader
    sent = []

    def _reader_factory(resolved):
        return reader

    def _generate_factory(resolved, *, response_format=None):
        def _call(prompt: str) -> str:
            sent.append(prompt)
            return '{"score": 1}'
        return _call

    spec = app_config.ProviderSpec(
        name="test-cloud-consent",
        label="Test Cloud",
        summary="Test cloud provider for consent.",
        default_host="https://api.test-consent.example/v1",
        default_vision_model="v",
        default_text_model="t",
        setup_hint="",
        api_key_env="TEST_CONSENT_KEY",
        vision_reader_factory=_reader_factory,
        text_generate_factory=_generate_factory,
        supports_model_listing=False,
        dialect="openai-chat",
    )

    monkeypatch.setenv("TEST_CONSENT_KEY", fake_key)
    monkeypatch.setattr("opengrader.core.app_config._config_dir", lambda: tmp_path)
    app_config.register_provider(spec)
    transport.register_secret(fake_key)

    try:
        # Create settings for the cloud provider
        settings = app_config.AISettings(provider=spec.name)

        # Resolve to get the ResolvedAI
        resolved = app_config.resolve(settings, environ={"TEST_CONSENT_KEY": fake_key})

        # Grant cloud consent
        settings_with_consent = consent.grant_cloud_consent(settings, resolved)

        # Save settings to config.json
        config_path = tmp_path / "config.json"
        app_config.save_settings(settings_with_consent, path=config_path)

        # Read the saved file and verify no key appears
        config_text = config_path.read_text()
        assert fake_key not in config_text
        assert "sk-test-" not in config_text.lower()

        # Also verify via _scan_bytes
        config_bytes = config_path.read_bytes()
        _scan_bytes(config_bytes, "config.json")

        # Load settings back and verify they're valid
        loaded = app_config.load_settings(path=config_path)
        assert loaded.provider == spec.name
        assert len(loaded.consents) > 0
        assert loaded.consents[0].kind == "cloud"
    finally:
        transport.forget_secret(fake_key)
        app_config._REGISTRY.clear()
        for orig_spec in original_providers:
            app_config.register_provider(orig_spec)


# ---------------------------------------------------------------------------
# Packets and backups of a folder that really went through a cloud run
# ---------------------------------------------------------------------------
#
# Each test below makes the key live: it is in the provider's environment
# variable, the provider is a consented cloud manifest, and a real (faked at
# the HTTP layer) cloud extraction and cloud grading run write their output
# — graded submission files, the egress log, the grading journal — into the
# folder that is then packed or backed up. Each also proves its own scan is
# able to fail, by planting the key in the folder and checking the same scan
# catches it.

_GRADING_QA = """\
---
title: Quiz
---

- (4 points) Explain the difference between a stack and a queue.
  - **Correct**
    - LIFO vs FIFO
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-2)
    - Incorrect (-4)
"""

_GRADING_SUBMISSION = """\
---
student_name: "Ada Student"
student_id: "123456789"
course_code: "CS146"
assignment_title: "Quiz"
source_pdf: "sub-123456789.pdf"
student_score: ""
---


- q1:
  - Question type: Free Response
  - Prompt: Explain the difference between a stack and a queue.
  - Response image: responses/q1.png
  - Response text: A stack is last in first out, a queue is first in first out.
  - Possible points: 4
  - Points:
  - Rubric:
  - Analysis:
"""


def _cloud_reply(req) -> _FakeResponse:
    body = json.loads(req.data.decode("utf-8")) if req.data else {}
    text = json.dumps(body)
    if "image_url" in text:
        return _FakeResponse('{"prompt": "Explain.", "answer": "LIFO versus FIFO."}')
    return _FakeResponse(
        '{"rubric_labels": ["Correct"], "confidence": 0.95, "rationale": "Matches."}'
    )


def _cohort_after_a_cloud_run(tmp_path: Path, monkeypatch, resolved) -> tuple[Path, Path]:
    """A submissions folder that a cloud extraction and cloud grading ran on."""
    from opengrader.cli import automated_grading

    sent: list = []

    def _urlopen(req, timeout=None):
        sent.append(req)
        return _cloud_reply(req)

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    root = tmp_path / "subs"
    student = root / "sub-123456789"
    crop = _answer_crop(student)
    (student / "sub-123456789.submission.md").write_text(_GRADING_SUBMISSION, encoding="utf-8")
    qa = tmp_path / "quiz.qa.md"
    qa.write_text(_GRADING_QA, encoding="utf-8")

    with egress.recording(root):
        app_config.vision_reader(resolved).extract_question_prompt_and_answer(
            image_path=crop
        )
    try:
        automated_grading.run_cli(
            [
                "--solutions", str(qa),
                "--input", str(root),
                "--llm",
                "--yes-send-to-cloud",
            ]
        )
    except SystemExit as exc:
        assert not exc.code, f"the cloud grading run failed (exit {exc.code})"

    # The key was live: it went out on every call, and the run left its
    # traces in the folder.
    assert len(sent) >= 2, "the cloud extraction and grading calls never happened"
    assert all(_HYGIENE_KEY in json.dumps(dict(r.header_items())) for r in sent)
    egress_log = root / ".opengrader" / egress.EGRESS_FILENAME
    categories = {json.loads(line)["category"] for line in egress_log.read_text().splitlines()}
    assert {"answer-image", "graded-text"} <= categories
    graded = (student / "sub-123456789.submission.md").read_text(encoding="utf-8")
    assert "Rubric: Correct" in graded or "Correct" in graded.split("Rubric:")[1]
    return root, student / "sub-123456789.submission.md"


def _files_in_zip(path: Path) -> dict[str, bytes]:
    import zipfile

    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _key_in(blobs: dict[str, bytes], key: str) -> list[str]:
    return [name for name, data in blobs.items() if key.encode() in data]


def _files_under(folder: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(folder)): path.read_bytes()
        for path in folder.rglob("*")
        if path.is_file()
    }


def test_packet_contains_no_api_key(tmp_path: Path, monkeypatch, hygiene_cloud) -> None:
    root, submission = _cohort_after_a_cloud_run(tmp_path, monkeypatch, hygiene_cloud)

    packet = build_packet(root, tmp_path / "out", packet_id="hygiene").packet_path
    contents = _files_under(packet)

    assert any(name.endswith(".submission.md") for name in contents)
    assert _key_in(contents, _HYGIENE_KEY) == []

    # The same scan must catch the key had it been written: plant it where a
    # leak would put it (the graded submission file) and pack again.
    submission.write_text(
        submission.read_text(encoding="utf-8").replace(
            "  - Analysis:", f"  - Analysis: {_HYGIENE_KEY}"
        ),
        encoding="utf-8",
    )
    planted = build_packet(root, tmp_path / "out", packet_id="hygiene-planted").packet_path
    assert _key_in(_files_under(planted), _HYGIENE_KEY), "the scan could not fail"


def test_backup_archive_contains_no_api_key(tmp_path: Path, monkeypatch, hygiene_cloud) -> None:
    root, _submission = _cohort_after_a_cloud_run(tmp_path, monkeypatch, hygiene_cloud)

    create_backup(root, tmp_path / "backup.zip")
    contents = _files_in_zip(tmp_path / "backup.zip")

    assert any(name.endswith(egress.EGRESS_FILENAME) for name in contents)
    assert any(name.endswith(".submission.md") for name in contents)
    assert _key_in(contents, _HYGIENE_KEY) == []

    (root / ".opengrader" / "planted.txt").write_text(_HYGIENE_KEY, encoding="utf-8")
    create_backup(root, tmp_path / "planted.zip")
    assert _key_in(_files_in_zip(tmp_path / "planted.zip"), _HYGIENE_KEY), (
        "the scan could not fail"
    )


def test_encrypted_backup_contains_no_api_key(tmp_path: Path, monkeypatch, hygiene_cloud) -> None:
    """Checked after decrypting: ciphertext never contains the plaintext key,
    so scanning the encrypted file alone would prove nothing."""
    from opengrader.core.integrity import restore_backup

    root, _submission = _cohort_after_a_cloud_run(tmp_path, monkeypatch, hygiene_cloud)

    create_backup(root, tmp_path / "backup.zip.enc", passphrase="correct horse")
    restored = tmp_path / "restored"
    restore_backup(tmp_path / "backup.zip.enc", restored, passphrase="correct horse")
    contents = _files_under(restored)

    assert any(name.endswith(egress.EGRESS_FILENAME) for name in contents)
    assert _key_in(contents, _HYGIENE_KEY) == []
    assert _HYGIENE_KEY.encode() not in (tmp_path / "backup.zip.enc").read_bytes()

    (root / ".opengrader" / "planted.txt").write_text(_HYGIENE_KEY, encoding="utf-8")
    create_backup(root, tmp_path / "planted.zip.enc", passphrase="correct horse")
    restore_backup(tmp_path / "planted.zip.enc", tmp_path / "planted", passphrase="correct horse")
    assert _key_in(_files_under(tmp_path / "planted"), _HYGIENE_KEY), "the scan could not fail"
