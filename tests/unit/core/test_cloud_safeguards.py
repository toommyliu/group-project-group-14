"""Safeguards in app_config: cloud consent, the identity pin, credential
consent, the cloud kill switch, the egress log, and the escalate-only default.

No network anywhere: the cloud provider here is a registry record whose
factories hand back in-memory fakes.
"""

from __future__ import annotations

import json
import pickle
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from opengrader.core import app_config, egress
from opengrader.core.app_config import consent
from opengrader.core.llm import usage
from opengrader.core.ocr.vision_reader import (
    CloudCallBlocked,
    GuardedVisionReader,
    IdentityFieldBlocked,
    TitleMetadataResult,
)

CLOUD_HOST = "https://api.cloud-safeguard.example/v1"
SECRET_ANSWER = "The mitochondria is the powerhouse"


class FakeReader:
    """A picklable VisionReader that records which operations reached it."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def extract_field_text(self, *, image_path, field_kind):
        self.calls.append(f"field:{field_kind}")
        return "Grace Hopper" if field_kind == "name" else "987654321"

    def extract_title_metadata(self, *, image_path):
        self.calls.append("title")
        return TitleMetadataResult(None, "HW1", None, "HW1")

    def extract_question_prompt_and_answer(self, *, image_path, math_latex=False):
        self.calls.append("qa")
        usage.record_last(120, 7)
        return ("Q1", SECRET_ANSWER)

    def extract_handwriting_only(self, *, image_path, math_latex=False):
        self.calls.append("handwriting")
        usage.record_last(100, 5)
        return SECRET_ANSWER

    def extract_page_number(self, *, image_path):
        self.calls.append("page_number")
        return 1

    def page_has_identity_field(self, *, image_path):
        self.calls.append("identity_page")
        return True


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise AssertionError("a safeguard test tried to use the network")

    monkeypatch.setattr("urllib.request.urlopen", _refuse)


@pytest.fixture(autouse=True)
def _fresh_credentials():
    app_config.clear_credential_cache()
    yield
    app_config.clear_credential_cache()


@pytest.fixture
def cloud(monkeypatch):
    """Register a cloud provider; yields (spec, the reader its factory returns, prompts sent)."""
    original = app_config.registered_providers()
    reader = FakeReader()
    sent: list[str] = []

    def _reader_factory(resolved):
        return reader

    def _generate_factory(resolved, *, response_format=None):
        def _call(prompt: str) -> str:
            sent.append(prompt)
            usage.record_last(50, 3)
            return '{"score": 1}'

        return _call

    spec = app_config.ProviderSpec(
        name="safeguard-cloud",
        label="Safeguard Cloud",
        summary="Test-only cloud provider.",
        default_host=CLOUD_HOST,
        default_vision_model="v",
        default_text_model="t",
        setup_hint="",
        api_key_env="SAFEGUARD_KEY",
        vision_reader_factory=_reader_factory,
        text_generate_factory=_generate_factory,
        supports_model_listing=False,
        dialect="openai-chat",
    )
    app_config.register_provider(spec)
    try:
        yield spec, reader, sent
    finally:
        app_config._REGISTRY.clear()
        for spec_ in original:
            app_config.register_provider(spec_)


def _settings(spec, **kwargs) -> app_config.AISettings:
    return app_config.AISettings(provider=spec.name, **kwargs)


def _consent(settings: app_config.AISettings) -> app_config.AISettings:
    return consent.grant_cloud_consent(settings, app_config.resolve(settings, environ={}))


def _egress_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


# ---------------------------------------------------------------------------
# A fresh install
# ---------------------------------------------------------------------------


def test_l0_a_fresh_install_defaults_to_local_ollama() -> None:
    assert not app_config.settings_path().exists()
    app_config.reload_manifests()

    assert app_config.registered_providers()[0].kind == "local"
    resolved = app_config.resolve()
    assert resolved.provider.name == "ollama"
    # Every AI toggle that gates usage defaults to False for local install
    assert resolved.read_handwriting is False
    assert resolved.escalate_only is False
    assert resolved.transcribe_math is False
    assert resolved.grade_written is False
    assert resolved.require_calibration is False
    # And the cloud consent is not required
    assert resolved.cloud_consent_required is False
    assert resolved.problems() == ()


# ---------------------------------------------------------------------------
# Cloud consent, structurally enforced and fingerprinted
# ---------------------------------------------------------------------------


def test_every_client_path_raises_consent_required_without_consent(cloud) -> None:
    spec, reader, sent = cloud
    resolved = app_config.resolve(_settings(spec), environ={"SAFEGUARD_KEY": "k" * 20})

    assert resolved.cloud_consent_required is True
    with pytest.raises(app_config.CloudConsentRequired, match="Nothing has been sent"):
        app_config.vision_reader(resolved)
    with pytest.raises(app_config.CloudConsentRequired):
        app_config.text_generate(resolved)
    with pytest.raises(app_config.AIUnavailableError):  # CloudConsentRequired is one
        app_config.ensure_ready(resolved)
    assert any("not agreed" in p for p in resolved.problems())
    assert reader.calls == [] and sent == []


def test_granting_consent_opens_the_path_and_the_reader_is_guarded(cloud) -> None:
    spec, _reader, _sent = cloud
    settings = _consent(_settings(spec))
    resolved = app_config.resolve(settings, environ={"SAFEGUARD_KEY": "k" * 20})

    assert resolved.cloud_consent_given is True
    assert resolved.problems() == ()
    reader = app_config.vision_reader(resolved)
    assert isinstance(reader, GuardedVisionReader)
    assert reader.cloud is True
    # A cloud reader always gets a local-Ollama fallback for identity crops.
    assert reader.local_identity_config is not None


def test_consent_is_persisted_and_holds_nothing_secret(cloud) -> None:
    spec, _reader, _sent = cloud
    settings = _consent(_settings(spec))
    path = app_config.save_settings(settings)

    raw = json.loads(path.read_text())
    assert raw["consents"][0]["kind"] == "cloud"
    assert raw["consents"][0]["version"] == consent.CLOUD_CONSENT_VERSION
    assert raw["consents"][0]["granted_at"].endswith("+00:00")
    assert app_config.load_settings() == settings
    schema = json.loads(path.with_name("config.schema.json").read_text())
    assert "consents" in schema["properties"]
    assert "ConsentRecord" in schema["$defs"]


def test_changing_the_request_host_re_prompts(cloud) -> None:
    spec, _reader, _sent = cloud
    settings = _consent(_settings(spec))
    assert app_config.resolve(settings, environ={}).cloud_consent_given

    moved = replace(settings, host="https://elsewhere.example/v1")
    resolved = app_config.resolve(moved, environ={})
    assert resolved.cloud_consent_required is True
    assert consent.consent_status(moved, resolved, "cloud") == "changed"
    assert any("changed since you agreed" in p for p in resolved.problems())

    flagged = app_config.with_overrides(
        app_config.resolve(settings, environ={}), host="https://flag.example/v1"
    )
    with pytest.raises(app_config.CloudConsentRequired):
        app_config.vision_reader(flagged)


def test_a_manifest_edit_to_host_dialect_key_or_command_changes_the_fingerprint(cloud) -> None:
    spec, _reader, _sent = cloud
    resolved = app_config.resolve(_settings(spec), environ={})
    base = consent.cloud_fingerprint(resolved)
    for change in (
        {"default_host": "https://other.example/v1"},
        {"dialect": "anthropic-messages"},
        {"api_key_env": "OTHER_KEY"},
        {"credential_command": ("pass", "show", "ai")},
    ):
        edited = replace(spec, **change)
        again = app_config.resolve(_settings(spec), environ={})
        again = replace(again, provider=edited, host=edited.default_host)
        assert consent.cloud_fingerprint(again) != base, change


def test_bumping_the_consent_text_version_re_prompts(cloud, monkeypatch) -> None:
    spec, _reader, _sent = cloud
    settings = _consent(_settings(spec))
    assert app_config.resolve(settings, environ={}).cloud_consent_given

    monkeypatch.setattr(consent, "CLOUD_CONSENT_VERSION", consent.CLOUD_CONSENT_VERSION + 1)

    resolved = app_config.resolve(settings, environ={})
    assert resolved.cloud_consent_required is True
    assert consent.consent_status(settings, resolved, "cloud") == "outdated"
    assert any("wording changed" in p for p in resolved.problems())


def test_local_providers_need_no_consent_and_reject_a_grant() -> None:
    resolved = app_config.resolve(app_config.AISettings(), environ={})

    assert resolved.cloud_consent_given is True
    app_config.vision_reader(resolved)  # no raise
    with pytest.raises(ValueError):
        consent.grant_cloud_consent(app_config.AISettings(), resolved)


def test_the_builtin_ollama_pointed_at_a_remote_host_is_a_cloud_provider() -> None:
    settings = app_config.AISettings(host="https://ollama.remote.example")
    resolved = app_config.resolve(settings, environ={})

    assert resolved.kind == "cloud"
    with pytest.raises(app_config.CloudConsentRequired):
        app_config.vision_reader(resolved)

    reader = app_config.vision_reader(app_config.resolve(_consent(settings), environ={}))
    with pytest.raises(IdentityFieldBlocked):
        reader.extract_field_text(image_path="x.png", field_kind="name")


def test_a_probe_may_send_only_the_connection_test_prompt(cloud) -> None:
    from opengrader.core.app_config.manifests import CONNECTION_TEST_PROMPT

    spec, _reader, sent = cloud
    probe = app_config.for_provider(spec.name)

    assert probe.probe is True and probe.cloud_consent_required
    app_config.ensure_ready(probe, purpose="text")  # a probe is not refused for consent
    generate = app_config.text_generate(probe)
    assert generate(CONNECTION_TEST_PROMPT) == '{"score": 1}'
    with pytest.raises(app_config.CloudConsentRequired):
        generate("Grade this answer: " + SECRET_ANSWER)
    assert sent == [CONNECTION_TEST_PROMPT]
    with pytest.raises(app_config.CloudConsentRequired):
        app_config.vision_reader(probe)  # probes never get a reader


def test_for_provider_carries_saved_consent(cloud) -> None:
    spec, _reader, _sent = cloud
    consented = _consent(_settings(spec))
    # The saved choice is the local default, but the consent record is kept.
    app_config.save_settings(replace(consented, provider="ollama"))

    assert app_config.for_provider(spec.name).cloud_consent_given is True


def test_measure_latency_sends_only_the_test_prompt_without_consent(cloud) -> None:
    spec, _reader, _sent = cloud
    resolved = app_config.resolve(_settings(spec), environ={})
    seen: list[str] = []

    def fake_generate(**kwargs):
        seen.append(kwargs["prompt"])
        return "ready"

    ok = app_config.measure_generate_latency(resolved, role="grading", generate=fake_generate)
    refused = app_config.measure_generate_latency(
        resolved, role="grading", prompt="student text", generate=fake_generate
    )

    assert ok.ok
    assert not refused.ok and "not agreed" in refused.error
    from opengrader.core.app_config.manifests import CONNECTION_TEST_PROMPT

    assert seen == [CONNECTION_TEST_PROMPT]


# ---------------------------------------------------------------------------
# The identity pin — no override exists; identity crops route to a local
# reader instead, whatever the active provider is
# ---------------------------------------------------------------------------


def test_a_cloud_reader_always_carries_a_local_identity_fallback(cloud) -> None:
    spec, _reader, _sent = cloud
    resolved = app_config.resolve(_consent(_settings(spec)), environ={})

    reader = app_config.vision_reader(resolved)

    assert reader.local_identity_config is not None
    # A plain config, not a live client — the reader stays picklable.
    assert pickle.loads(pickle.dumps(reader.local_identity_config)) == reader.local_identity_config


def test_identity_crops_route_to_the_local_reader_even_with_cloud_fully_consented(
    cloud, monkeypatch
) -> None:
    """There is no setting that sends a name or ID to a cloud host."""
    spec, cloud_reader, _sent = cloud
    resolved = app_config.resolve(_consent(_settings(spec)), environ={})
    reader = app_config.vision_reader(resolved)

    from opengrader.core.ocr import ollama_vlm

    monkeypatch.setattr(
        ollama_vlm,
        "extract_field_text",
        lambda *, image_path, field_kind, config: "Grace Hopper",
    )

    assert reader.extract_field_text(image_path="n.png", field_kind="name") == "Grace Hopper"
    assert cloud_reader.calls == []  # the cloud reader never saw it


def test_identity_crops_fall_back_to_blocked_when_the_local_reader_fails(cloud) -> None:
    spec, cloud_reader, _sent = cloud
    resolved = app_config.resolve(_consent(_settings(spec)), environ={})
    reader = app_config.vision_reader(resolved)

    # No local Ollama server is running in this test, so the real attempt
    # fails on its own (connection refused) — nothing needs to be mocked.
    with pytest.raises(IdentityFieldBlocked):
        reader.extract_field_text(image_path="n.png", field_kind="sid")
    assert cloud_reader.calls == []


def test_a_stale_identity_consent_record_loads_and_is_dropped() -> None:
    """A config.json written before the identity override was removed must
    still load; the removed consent kind is dropped, never a crash."""
    settings = app_config.AISettings.from_dict(
        {
            "provider": "acme",
            "consents": [
                {
                    "provider": "acme",
                    "kind": "identity",
                    "fingerprint": "f",
                    "version": 1,
                    "granted_at": "2026-01-01T00:00:00+00:00",
                    "institution_approved": True,
                }
            ],
        }
    )
    assert settings.consents == ()
    assert settings.provider == "acme"


def test_the_stale_identity_kill_switch_env_var_is_harmless(cloud, monkeypatch) -> None:
    spec, _reader, _sent = cloud
    monkeypatch.setenv("OPENGRADER_AI_ALLOW_IDENTITY_TO_CLOUD", "0")

    resolved = app_config.resolve(_consent(_settings(spec)), environ={})
    assert resolved.problems() == ()
    app_config.vision_reader(resolved)  # does not raise


# ---------------------------------------------------------------------------
# The cloud kill switch
# ---------------------------------------------------------------------------

ACME = """\
name         = "acme"
label        = "Acme AI"
dialect      = "openai-chat"
host         = "https://api.acme.example/v1"
vision_model = "acme-vision"
text_model   = "acme-text"
"""


@pytest.fixture
def acme_manifest():
    directory = app_config.manifests_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "acme.toml").write_text(ACME, encoding="utf-8")
    try:
        yield
    finally:
        for path in directory.glob("*.toml"):
            path.unlink()
        app_config.reload_manifests()


@pytest.mark.parametrize("value", ["0", "false", "No", " off "])
def test_l7_stops_manifests_loading_and_falls_back_to_ollama(
    acme_manifest, monkeypatch, value
) -> None:
    monkeypatch.setenv(consent.ALLOW_CLOUD_ENV, value)
    app_config.reload_manifests()

    assert [s.name for s in app_config.registered_providers()] == ["ollama"]
    assert "acme" not in app_config._REGISTRY  # not even loaded
    resolved = app_config.resolve(app_config.AISettings(provider="acme"))
    assert resolved.provider.name == "ollama"
    assert resolved.kind == "local"
    assert any(consent.ALLOW_CLOUD_ENV in p for p in resolved.problems())


def test_l7_is_checked_at_resolve_time_so_the_cache_cannot_defeat_it(
    acme_manifest, monkeypatch
) -> None:
    app_config.reload_manifests()
    assert "acme" in {s.name for s in app_config.registered_providers()}
    settings = consent.grant_cloud_consent(
        app_config.AISettings(provider="acme"),
        app_config.resolve(app_config.AISettings(provider="acme"), environ={}),
    )
    before = app_config.resolve(settings)
    reader = app_config.vision_reader(before)

    monkeypatch.setenv(consent.ALLOW_CLOUD_ENV, "0")

    assert "acme" not in {s.name for s in app_config.registered_providers()}
    assert app_config.resolve(settings).provider.name == "ollama"
    with pytest.raises(app_config.CloudDisabledError):
        app_config.vision_reader(before)  # resolved before the switch was set
    with pytest.raises(app_config.CloudDisabledError):
        app_config.text_generate(before)
    with pytest.raises(CloudCallBlocked):
        reader.extract_handwriting_only(image_path="a.png")  # made before, called after

    monkeypatch.delenv(consent.ALLOW_CLOUD_ENV)
    assert app_config.resolve(settings).provider.name == "acme"


def test_l7_refuses_a_remote_host_override(monkeypatch) -> None:
    environ = {consent.ALLOW_CLOUD_ENV: "0"}
    saved = app_config.resolve(
        app_config.AISettings(host="https://ollama.remote.example"), environ=environ
    )
    assert saved.kind == "local"
    assert any("ignored" in p and consent.ALLOW_CLOUD_ENV in p for p in saved.problems())

    monkeypatch.setenv(consent.ALLOW_CLOUD_ENV, "0")
    flagged = app_config.with_overrides(
        app_config.resolve(app_config.AISettings()), host="https://gpu.remote.example"
    )
    assert flagged.host == app_config.DEFAULT_HOST
    assert any("refused" in p for p in flagged.problems())
    local = app_config.with_overrides(
        app_config.resolve(app_config.AISettings()), host="http://192.168.1.5:11434"
    )
    assert local.host == "http://192.168.1.5:11434"  # a LAN host is still local


def test_l7_refuses_cloud_consent(cloud) -> None:
    spec, _reader, _sent = cloud
    resolved = app_config.resolve(_settings(spec), environ={})

    with pytest.raises(ValueError, match=consent.ALLOW_CLOUD_ENV):
        consent.grant_cloud_consent(
            _settings(spec), resolved, environ={consent.ALLOW_CLOUD_ENV: "0"}
        )


# ---------------------------------------------------------------------------
# credential_command
# ---------------------------------------------------------------------------


def _credential_spec(tmp_path: Path, script: str) -> app_config.ProviderSpec:
    return app_config.ProviderSpec(
        name="cred-cloud",
        label="Cred Cloud",
        summary="",
        default_host="https://cred.example/v1",
        default_vision_model="v",
        default_text_model="t",
        setup_hint="",
        api_key_env="CRED_CLOUD_KEY_UNSET",
        requires_api_key=True,
        supports_model_listing=False,
        dialect="openai-chat",
        credential_command=(sys.executable, "-c", script),
    )


def test_credential_command_runs_only_after_its_own_consent(tmp_path) -> None:
    counter = tmp_path / "runs"
    key = "cred-key-0123456789abcdef"
    spec = _credential_spec(
        tmp_path,
        f"open({str(counter)!r}, 'a').write('x'); print('  {key}  ')",
    )
    settings = app_config.AISettings(provider=spec.name)

    assert app_config.resolve_api_key(spec, environ={}, settings=settings) is None
    assert app_config.resolve_api_key(spec, environ={}) is None
    assert not counter.exists()

    settings = consent.grant_credential_consent(settings, spec)
    assert app_config.resolve_api_key(spec, environ={}, settings=settings) == key
    assert app_config.resolve_api_key(spec, environ={}, settings=settings) == key
    assert counter.read_text() == "x"  # cached per process

    from opengrader.core.llm.transport import redact

    assert key not in redact(f"error: {key}")
    # The environment variable, when set, wins over the command.
    assert (
        app_config.resolve_api_key(
            spec, environ={"CRED_CLOUD_KEY_UNSET": "from-env-000000"}, settings=settings
        )
        == "from-env-000000"
    )


def test_credential_consent_is_bound_to_the_literal_argv(tmp_path) -> None:
    spec = _credential_spec(tmp_path, "print('a-key-0123456789')")
    settings = consent.grant_credential_consent(app_config.AISettings(), spec)
    changed = replace(spec, credential_command=(*spec.credential_command, "--extra"))

    assert consent.has_credential_consent(settings, spec) is True
    assert consent.has_credential_consent(settings, changed) is False


def test_an_unapproved_command_is_a_problem_not_an_error(cloud, tmp_path) -> None:
    spec = _credential_spec(tmp_path, "print('never')")
    app_config.register_provider(spec)

    resolved = app_config.resolve(app_config.AISettings(provider=spec.name), environ={})

    assert resolved.api_key is None
    assert resolved.credential_consent_required is True
    assert any("credential_command" in p for p in resolved.problems())


def test_a_failing_command_is_redacted_in_problems(cloud, tmp_path) -> None:
    secret = "hunter2hunter2hunter2"
    spec = _credential_spec(
        tmp_path,
        "import sys; print(%r); print('denied for %s', file=sys.stderr); sys.exit(3)"
        % (secret, secret),
    )
    app_config.register_provider(spec)
    settings = consent.grant_cloud_consent(
        consent.grant_credential_consent(app_config.AISettings(provider=spec.name), spec),
        app_config.resolve(app_config.AISettings(provider=spec.name), environ={}),
    )

    resolved = app_config.resolve(settings, environ={})  # never raises
    problems = resolved.problems()

    assert resolved.api_key is None
    assert any("exited with status 3" in p for p in problems)
    assert secret not in " ".join(problems)
    with pytest.raises(app_config.AIUnavailableError) as excinfo:
        app_config.ensure_ready(resolved)
    assert secret not in str(excinfo.value)


def test_a_missing_or_slow_command_is_a_problem(cloud, tmp_path, monkeypatch) -> None:
    spec = replace(
        _credential_spec(tmp_path, ""),
        credential_command=("definitely-not-a-real-command-xyz",),
    )
    app_config.register_provider(spec)
    settings = consent.grant_credential_consent(app_config.AISettings(provider=spec.name), spec)

    resolved = app_config.resolve(settings, environ={})

    assert any("was not found" in p for p in resolved.problems())

    monkeypatch.setattr(app_config, "CREDENTIAL_COMMAND_TIMEOUT_SECONDS", 0.2)
    slow = replace(
        _credential_spec(tmp_path, "import time; time.sleep(5)"), name="cred-slow"
    )
    app_config.register_provider(slow)
    slow_settings = consent.grant_credential_consent(
        app_config.AISettings(provider=slow.name), slow
    )
    assert any(
        "did not finish" in p
        for p in app_config.resolve(slow_settings, environ={}).problems()
    )


def test_credential_consent_is_bound_to_the_host_its_key_goes_to(tmp_path) -> None:
    """The approval names where the key is sent; a manifest re-pointed at
    another host must ask again before its command runs."""
    counter = tmp_path / "runs"
    spec = _credential_spec(
        tmp_path, f"open({str(counter)!r}, 'a').write('x'); print('a-key-0123456789')"
    )
    settings = consent.grant_credential_consent(app_config.AISettings(), spec)
    moved = replace(spec, default_host="https://collector.elsewhere.example/v1")

    assert consent.has_credential_consent(settings, spec) is True
    assert consent.has_credential_consent(settings, moved) is False
    assert app_config.resolve_api_key(moved, environ={}, settings=settings) is None
    assert not counter.exists()


def test_a_failing_command_whose_stderr_is_long_does_not_leak_half_a_key(
    cloud, tmp_path
) -> None:
    """stderr is cut to 200 characters for display. A key without a
    recognisable shape, cut by that limit, would no longer match the
    registered secret — so it has to be redacted before the cut."""
    secret = "Qm7Zr2Lx9Vt4Nc8Bw1Hy6Jd3Kf5Pg0Sa"
    padding = "e" * (200 - 12)
    spec = replace(
        _credential_spec(
            tmp_path,
            "import sys; print(%r); print(%r + ' ' + %r, file=sys.stderr); sys.exit(4)"
            % (secret, padding, secret),
        ),
        name="cred-long-stderr",
    )
    app_config.register_provider(spec)
    settings = consent.grant_credential_consent(app_config.AISettings(provider=spec.name), spec)

    problems = " ".join(app_config.resolve(settings, environ={}).problems())

    assert "exited with status 4" in problems
    assert secret[:10] not in problems


def test_a_resolved_configuration_never_shows_its_key_in_repr(monkeypatch) -> None:
    key = "sk-repr-check-" + "r" * 24
    spec = replace(
        _credential_spec(Path("."), ""),
        name="repr-cloud",
        api_key_env="REPR_CLOUD_KEY",
        credential_command=(),
    )
    app_config.register_provider(spec)
    try:
        resolved = app_config.resolve(
            app_config.AISettings(provider=spec.name), environ={"REPR_CLOUD_KEY": key}
        )

        assert resolved.api_key == key
        assert key not in repr(resolved)
        assert key not in str(resolved)
    finally:
        app_config._REGISTRY.pop(spec.name, None)


# ---------------------------------------------------------------------------
# escalate_only defaults on for cloud
# ---------------------------------------------------------------------------


def test_escalate_only_defaults_on_for_cloud_off_for_local(cloud) -> None:
    spec, _reader, _sent = cloud

    assert app_config.AISettings().escalate_only is None
    assert app_config.resolve(
        app_config.AISettings(read_handwriting=True), environ={}
    ).escalate_only is False
    assert app_config.resolve(
        _settings(spec, read_handwriting=True), environ={}
    ).escalate_only is True
    # Explicit wins, and it is still ANDed with read_handwriting.
    assert app_config.resolve(
        _settings(spec, read_handwriting=True, escalate_only=False), environ={}
    ).escalate_only is False
    assert app_config.resolve(_settings(spec), environ={}).escalate_only is False
    assert app_config.resolve(
        app_config.AISettings(read_handwriting=True, escalate_only=True), environ={}
    ).escalate_only is True
    # A host flag that makes the request remote flips an unset default.
    flagged = app_config.with_overrides(
        app_config.resolve(app_config.AISettings(read_handwriting=True), environ={}),
        host="https://remote.example",
    )
    assert flagged.escalate_only is True


def test_unset_escalate_only_is_not_written() -> None:
    assert "escalate_only" not in app_config.AISettings().to_dict()
    assert app_config.AISettings(escalate_only=False).to_dict()["escalate_only"] is False


# ---------------------------------------------------------------------------
# Egress lines from the guards
# ---------------------------------------------------------------------------


def test_cloud_calls_write_one_line_each_with_category_and_never_content(
    cloud, tmp_path
) -> None:
    spec, _reader, _sent = cloud
    settings = _consent(_settings(spec))
    resolved = app_config.resolve(settings, environ={"SAFEGUARD_KEY": "sk-" + "z" * 20})

    with egress.recording(tmp_path) as log:
        reader = app_config.vision_reader(resolved)
        reader.extract_handwriting_only(image_path="q1.png")
        reader.extract_question_prompt_and_answer(image_path="q2.png")
        with pytest.raises(IdentityFieldBlocked):
            reader.extract_field_text(image_path="name.png", field_kind="name")
        app_config.text_generate(resolved)("Grade: " + SECRET_ANSWER)

    lines = _egress_lines(log)
    assert [line["category"] for line in lines] == [
        "answer-image",
        "answer-image",
        "graded-text",
    ]
    assert lines[0]["input_tokens"] == 100 and lines[0]["output_tokens"] == 5
    assert lines[2]["input_tokens"] == 50
    assert all(line["provider"] == spec.name and line["host"] == CLOUD_HOST for line in lines)
    raw = log.read_text()
    assert SECRET_ANSWER not in raw and "Grace" not in raw and "sk-" not in raw
    assert set(lines[0]) <= {
        "at", "provider", "host", "category", "count", "input_tokens", "output_tokens"
    }


def test_local_calls_write_nothing_and_clear_usage(tmp_path) -> None:
    reader = GuardedVisionReader(
        inner=FakeReader(), provider="ollama", host=app_config.DEFAULT_HOST, cloud=False,
    )
    with egress.recording(tmp_path) as log:
        reader.extract_handwriting_only(image_path="q.png")
        assert reader.extract_field_text(image_path="n.png", field_kind="name") == "Grace Hopper"

    assert _egress_lines(log) == []
    assert usage.take_last() == (None, None)


# ---------------------------------------------------------------------------
# Single-source wording and the data-flow report
# ---------------------------------------------------------------------------


def test_consent_texts_name_the_host_and_what_leaves(cloud, tmp_path) -> None:
    spec, _reader, _sent = cloud
    resolved = app_config.resolve(_settings(spec), environ={})

    cloud_text = consent.cloud_consent_text(resolved)
    assert cloud_text.hostname == "api.cloud-safeguard.example"
    assert any("handwriting" in item for item in cloud_text.leaves_machine)
    assert any("rubric" in item for item in cloud_text.leaves_machine)
    assert "cannot recall" in " ".join(cloud_text.paragraphs)
    assert "never sent" in " ".join(cloud_text.paragraphs)

    cred = _credential_spec(tmp_path, "print(1)")
    cred_text = consent.credential_consent_text(cred)
    assert cred_text.argv == cred.credential_command


def test_egress_statement_is_one_sentence_for_every_state(cloud) -> None:
    spec, _reader, _sent = cloud
    local = app_config.egress_statement(app_config.resolve(app_config.AISettings(), environ={}))
    unconsented = app_config.egress_statement(app_config.resolve(_settings(spec), environ={}))
    consented = app_config.egress_statement(
        app_config.resolve(_consent(_settings(spec)), environ={})
    )

    assert "no student work leaves" in local
    assert "nothing is sent until then" in unconsented
    assert "api.cloud-safeguard.example" in consented and "names and IDs stay" in consented


def test_data_flow_report_follows_the_configuration(cloud) -> None:
    spec, _reader, _sent = cloud
    settings = _consent(_settings(spec, read_handwriting=True))
    report = app_config.data_flow_report(app_config.resolve(settings, environ={}))
    by = {item.category: item for item in report.items}

    assert report.hostname == "api.cloud-safeguard.example"
    assert by["answer-image"].leaves_machine is True
    assert "escalate-only" in by["answer-image"].reason
    assert by["graded-text"].leaves_machine is False  # grading is off
    # Names, IDs, the title crop and whole pages never leave, under any setting.
    assert by["identity-image"].leaves_machine is False
    assert by["title-image"].leaves_machine is False
    assert by["page-image"].leaves_machine is False
    assert json.loads(json.dumps(report.to_dict()))["provider"] == spec.name
    assert any("LEAVES THIS COMPUTER" in line for line in report.lines())

    local = app_config.data_flow_report(app_config.resolve(app_config.AISettings(), environ={}))
    assert all(
        not item.leaves_machine for item in local.items
    )


def test_data_flow_report_reflects_read_identity_locally_on_a_local_provider() -> None:
    on = app_config.data_flow_report(app_config.resolve(app_config.AISettings(), environ={}))
    off = app_config.data_flow_report(
        app_config.resolve(app_config.AISettings(read_identity_locally=False), environ={})
    )
    by_on = {item.category: item for item in on.items}
    by_off = {item.category: item for item in off.items}

    assert "Tesseract only" not in by_on["identity-image"].reason
    assert by_on["identity-image"].leaves_machine is False
    assert "Tesseract only" in by_off["identity-image"].reason
    assert by_off["identity-image"].leaves_machine is False
    # The setting is specific to name/ID crops: the title and page-image
    # categories are worded exactly the same either way.
    assert by_on["title-image"].reason == by_off["title-image"].reason
    assert by_on["page-image"].reason == by_off["page-image"].reason
    assert any("Tesseract only" in line for line in off.lines())


def test_data_flow_report_reflects_read_identity_locally_on_a_cloud_provider(cloud) -> None:
    spec, _reader, _sent = cloud
    on = app_config.data_flow_report(
        app_config.resolve(_consent(_settings(spec)), environ={})
    )
    off = app_config.data_flow_report(
        app_config.resolve(
            _consent(_settings(spec, read_identity_locally=False)), environ={}
        )
    )
    by_on = {item.category: item for item in on.items}
    by_off = {item.category: item for item in off.items}

    assert "never sent to a cloud" in by_on["identity-image"].reason.lower()
    assert by_on["identity-image"].leaves_machine is False
    assert "Tesseract only" in by_off["identity-image"].reason
    assert by_off["identity-image"].leaves_machine is False
    assert by_on["title-image"].reason == by_off["title-image"].reason


def test_malformed_consent_entries_cost_a_consent_not_a_launch() -> None:
    settings = app_config.AISettings.from_dict(
        {
            "consents": [
                {"provider": "x", "kind": "cloud", "fingerprint": "f", "version": 1,
                 "granted_at": "2026-01-01T00:00:00+00:00"},
                {"provider": "x", "kind": "telepathy", "fingerprint": "f", "version": 1,
                 "granted_at": ""},
                {"provider": "x", "kind": "cloud", "version": "1"},
                "nonsense",
            ]
        }
    )
    assert len(settings.consents) == 1
    assert app_config.AISettings.from_dict(settings.to_dict()) == settings
    assert app_config.AISettings.from_dict({"consents": "not a list"}).consents == ()


def test_the_guarded_reader_survives_the_process_boundary(cloud) -> None:
    spec, _reader, _sent = cloud
    local = app_config.vision_reader(app_config.for_provider("ollama"))
    assert pickle.loads(pickle.dumps(local)) == local

    from opengrader.core.llm.dialect_client import DialectConfig
    from opengrader.core.ocr.vision_reader import DialectVisionReader

    remote = GuardedVisionReader(
        inner=DialectVisionReader(
            config=DialectConfig(dialect="openai-chat", host=CLOUD_HOST, model="m")
        ),
        provider=spec.name,
        host=CLOUD_HOST,
        cloud=True,
    )
    restored = pickle.loads(pickle.dumps(remote))
    assert restored == remote
    with pytest.raises(IdentityFieldBlocked):
        restored.page_has_identity_field(image_path="page.png")


def test_the_approval_text_shows_control_characters_in_a_command_as_escapes(tmp_path) -> None:
    """An escape sequence in a manifest's argument, printed raw, lets a
    terminal show a different command from the one that would run."""
    sneaky = "true\x1b[2K\x1b[1Gecho-harmless"
    spec = replace(
        _credential_spec(tmp_path, ""), credential_command=("/bin/sh", "-c", sneaky)
    )

    text = consent.credential_consent_text(spec).as_text()

    assert "\x1b" not in text
    assert "\\u001b[2K" in text
    assert consent.format_argv(("pass", "show", "ai")) == "pass show ai"
    assert consent.format_argv(("a b",)) == '"a b"'
