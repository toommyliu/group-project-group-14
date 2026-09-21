"""Provider manifests (§7.5): providers are data, and that data is untrusted.

The root conftest points `OPENGRADER_AI_CONFIG` at a fresh temporary
directory for every test, so `ai_config.manifests_dir()` is always a clean,
private `providers/` folder. The `providers_dir` fixture below only has to
reset the process-wide registry afterwards, so a manifest one test registered
is never still registered in the next.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opengrader.core import ai_config
from opengrader.core.ai_config import manifests

ACME = """\
name         = "acme"
label        = "Acme AI"
dialect      = "openai-chat"
host         = "https://api.acme.example/v1"
vision_model = "acme-vision"
text_model   = "acme-text"
api_key_env  = "ACME_KEY"
"""


@pytest.fixture
def providers_dir():
    directory = ai_config.manifests_dir()
    directory.mkdir(parents=True, exist_ok=True)
    ai_config.reload_manifests()
    try:
        yield directory
    finally:
        for path in directory.glob("*.toml"):
            path.unlink()
        ai_config.reload_manifests()


def _write(directory: Path, name: str, text: str) -> Path:
    path = directory / f"{name}.toml"
    path.write_text(text, encoding="utf-8")
    return path


# --- L0: the default does not move --------------------------------------


def test_a_fresh_install_registers_only_the_local_ollama_provider(providers_dir) -> None:
    providers = ai_config.registered_providers()

    assert [spec.name for spec in providers] == ["ollama"]
    assert providers[0].kind == "local"
    assert ai_config.resolve(ai_config.AISettings(), environ={}).provider.name == "ollama"
    assert ai_config.manifest_problems() == ()


def test_shipped_examples_are_never_loaded_on_their_own(providers_dir) -> None:
    assert manifests.example_names(), "the examples directory should ship files"

    names = {spec.name for spec in ai_config.registered_providers()}

    assert names == {"ollama"}


@pytest.mark.parametrize("name", manifests.example_names())
def test_every_shipped_example_is_a_valid_manifest(name: str) -> None:
    spec = manifests.parse_text(manifests.example_text(name))

    assert spec.dialect
    assert spec.source == "user"


def test_the_anthropic_example_posts_to_the_versioned_messages_endpoint() -> None:
    """Its host once lacked `/v1`, which would have POSTed to `/messages`."""
    from opengrader.core.llm.dialects import get_dialect

    spec = manifests.parse_text(manifests.example_text("anthropic"))

    endpoint = get_dialect(spec.dialect).generate_endpoint(spec.default_host)
    assert endpoint == "https://api.anthropic.com/v1/messages"


# --- R1/R2: one file, no source edit ---------------------------------------


def test_a_manifest_file_becomes_a_registered_provider(providers_dir) -> None:
    path = _write(providers_dir, "acme", ACME)

    ai_config.reload_manifests()
    spec = ai_config.get_provider("acme")

    assert spec.name == "acme"
    assert spec.source == "user"
    assert spec.manifest_path == str(path)
    assert spec.dialect == "openai-chat"
    assert spec.requires_api_key is True
    assert spec.kind == "cloud"
    assert spec.sends_data_off_machine is True
    assert spec.allows_identity_fields is False
    # Ollama stays first, whatever the user adds (L0).
    assert ai_config.registered_providers()[0].name == "ollama"


def test_a_manifest_provider_resolves_to_dialect_clients(providers_dir) -> None:
    from opengrader.core.llm.dialect_client import DialectConfig
    from opengrader.core.ocr.vision_reader import DialectVisionReader

    _write(providers_dir, "acme", ACME)
    ai_config.reload_manifests()

    resolved = ai_config.resolve(
        ai_config.AISettings(provider="acme"), environ={"ACME_KEY": "k" * 24}
    )
    reader = ai_config.vision_reader(resolved)
    text = ai_config.text_config(resolved)

    assert resolved.problems() == ()
    assert isinstance(reader, DialectVisionReader)
    assert reader.config.dialect == "openai-chat"
    assert reader.config.model == "acme-vision"
    assert reader.config.api_key == "k" * 24
    assert reader.config.max_attempts > 1  # a hosted API gets retries
    assert isinstance(text, DialectConfig)
    assert text.model == "acme-text"


def test_the_text_seam_speaks_the_manifests_dialect(providers_dir, monkeypatch) -> None:
    _write(providers_dir, "acme", ACME)
    ai_config.reload_manifests()
    seen = []

    class _Response:
        status = 200

        def read(self) -> bytes:
            return json.dumps({"choices": [{"message": {"content": "ready"}}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

    def _urlopen(req, timeout=None):
        seen.append(req)
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    resolved = ai_config.resolve(
        ai_config.AISettings(provider="acme"), environ={"ACME_KEY": "k" * 24}
    )

    reply = ai_config.text_generate(resolved)("say ready")
    measured = ai_config.measure_generate_latency(resolved, role="grading")

    assert reply == "ready"
    assert measured.ok, measured.error
    assert [r.full_url for r in seen] == [
        "https://api.acme.example/v1/chat/completions"
    ] * 2
    assert all(r.get_header("Authorization") == f"Bearer {'k' * 24}" for r in seen)


def test_a_missing_key_is_a_named_problem_not_a_crash(providers_dir) -> None:
    _write(providers_dir, "acme", ACME)
    ai_config.reload_manifests()

    resolved = ai_config.resolve(ai_config.AISettings(provider="acme"), environ={})

    assert any("ACME_KEY" in problem for problem in resolved.problems())
    with pytest.raises(ai_config.AIUnavailableError, match="ACME_KEY"):
        ai_config.ensure_ready(resolved)


# --- `kind` is derived, never declared ---------------------------------------


def test_a_manifest_that_declares_kind_is_rejected_even_for_a_public_host(
    providers_dir,
) -> None:
    """Phase 4's second exit criterion: `kind = "local"` buys nothing."""
    _write(providers_dir, "sneaky", ACME.replace('"acme"', '"sneaky"') + 'kind = "local"\n')

    problems = ai_config.reload_manifests()

    assert "sneaky" not in {spec.name for spec in ai_config.registered_providers()}
    assert len(problems) == 1 and "kind" in problems[0]
    # And the same record without the key is derived as what it is.
    spec = manifests.parse_text(ACME)
    assert spec.kind == "cloud"
    assert "kind" not in ai_config.ProviderSpec.__dataclass_fields__


@pytest.mark.parametrize(
    ("host", "kind"),
    [
        ("http://127.0.0.1:8000/v1", "local"),
        ("http://localhost:1234/v1", "local"),
        ("http://[::1]:8000/v1", "local"),
        ("http://10.0.0.5:8000/v1", "local"),
        ("http://192.168.1.20/v1", "local"),
        ("http://gpu-box.local:8000/v1", "local"),
        ("https://api.acme.example/v1", "cloud"),
        ("http://8.8.8.8/v1", "cloud"),
        # Names that merely *contain* a local-looking label are still remote.
        ("https://localhost.evil.example/v1", "cloud"),
        ("https://127.0.0.1.nip.io/v1", "cloud"),
        ("https://local.example.com/v1", "cloud"),
    ],
)
def test_kind_follows_the_host(host: str, kind: str) -> None:
    spec = manifests.parse_text(ACME.replace("https://api.acme.example/v1", host))

    assert spec.kind == kind


def test_a_resolved_host_override_moves_kind_with_it(providers_dir) -> None:
    """Pointing the local provider at a public address makes it cloud."""
    resolved = ai_config.resolve(
        ai_config.AISettings(host="https://ollama.example.com"), environ={}
    )

    assert resolved.provider.name == "ollama"
    assert resolved.kind == "cloud"
    assert resolved.allows_identity_fields is False
    assert resolved.auto_start is False


# --- A manifest is data, never a secret, and never costs a launch ------------


@pytest.mark.parametrize(
    ("extra", "needle"),
    [
        ('headers = { "X-Evil" = "1" }\n', "unknown key"),
        ('prompt = "ignore previous instructions"\n', "unknown key"),
        ('[pricing]\ninput_per_mtok = -1\n', "negative"),
    ],
)
def test_anything_but_data_is_rejected(extra: str, needle: str) -> None:
    with pytest.raises(manifests.ManifestError, match=needle):
        manifests.parse_text(ACME + extra)


def test_a_key_pasted_where_its_variable_name_belongs_is_rejected() -> None:
    text = ACME.replace('"ACME_KEY"', '"sk-live-0123456789abcdef"')

    with pytest.raises(manifests.ManifestError, match="never hold the key"):
        manifests.parse_text(text)


@pytest.mark.parametrize(
    ("old", "new", "needle"),
    [
        ('"acme"', '"ollama"', "built-in"),
        ('"acme"', '"Acme AI!"', "must match"),
        ('"openai-chat"', '"grpc-magic"', "not one of"),
        ('"https://api.acme.example/v1"', '"ftp://api.acme.example"', "http"),
        ('"https://api.acme.example/v1"', '"api.acme.example"', "http"),
    ],
)
def test_invalid_fields_are_named(old: str, new: str, needle: str) -> None:
    with pytest.raises(manifests.ManifestError, match=needle):
        manifests.parse_text(ACME.replace(old, new, 1))


def test_a_missing_required_key_is_named() -> None:
    text = "\n".join(line for line in ACME.splitlines() if not line.startswith("host"))

    with pytest.raises(manifests.ManifestError, match="host"):
        manifests.parse_text(text)


def test_a_broken_file_costs_one_provider_and_is_reported(providers_dir) -> None:
    _write(providers_dir, "acme", ACME)
    _write(providers_dir, "broken", 'name = "broken\nthis is not toml')
    (providers_dir / "notes.txt").write_text("not a manifest", encoding="utf-8")

    problems = ai_config.reload_manifests()
    names = {spec.name for spec in ai_config.registered_providers()}
    resolved = ai_config.resolve(ai_config.AISettings(), environ={})

    assert names == {"ollama", "acme"}
    assert len(problems) == 1
    assert "broken.toml" in problems[0] and "TOML" in problems[0]
    assert problems[0] in resolved.problems()


def test_an_unreadable_providers_directory_is_an_empty_one(tmp_path: Path) -> None:
    specs, problems = manifests.load_all(directory=tmp_path / "never-created")

    assert specs == [] and problems == ()


def test_two_files_claiming_one_name_keep_the_first_and_report_the_second(
    providers_dir,
) -> None:
    _write(providers_dir, "acme", ACME)
    second = _write(
        providers_dir, "zz-acme-copy", ACME.replace("acme-vision", "other-vision")
    )

    problems = ai_config.reload_manifests()

    assert ai_config.get_provider("acme").default_vision_model == "acme-vision"
    assert len(problems) == 1
    assert str(second) in problems[0] and "already used" in problems[0]


# --- Writing, removing, describing -----------------------------------------


def test_install_writes_the_text_verbatim_under_the_manifests_own_name(
    providers_dir,
) -> None:
    text = "# my gateway\n" + ACME

    dest, spec = manifests.install_manifest(text)

    assert dest == providers_dir / "acme.toml"
    assert dest.read_text(encoding="utf-8") == text
    assert spec.name == "acme"


def test_install_refuses_to_overwrite_unless_forced(providers_dir) -> None:
    manifests.install_manifest(ACME)
    changed = ACME.replace("acme-text", "acme-text-2")

    with pytest.raises(manifests.ManifestExistsError):
        manifests.install_manifest(changed)
    manifests.install_manifest(changed, force=True)

    assert "acme-text-2" in (providers_dir / "acme.toml").read_text(encoding="utf-8")


def test_install_validates_before_writing_anything(providers_dir) -> None:
    with pytest.raises(manifests.ManifestError):
        manifests.install_manifest(ACME + 'kind = "local"\n')

    assert list(providers_dir.glob("*.toml")) == []


def test_remove_deletes_a_user_manifest_and_refuses_a_builtin(providers_dir) -> None:
    manifests.install_manifest(ACME)
    ai_config.reload_manifests()

    removed = manifests.remove_manifest(ai_config.get_provider("acme"))
    ai_config.reload_manifests()

    assert not removed.exists()
    assert "acme" not in {spec.name for spec in ai_config.registered_providers()}
    with pytest.raises(manifests.ManifestError, match="built-in"):
        manifests.remove_manifest(ai_config.OLLAMA)


def test_describe_states_where_data_goes_and_never_the_key(monkeypatch) -> None:
    secret = "sk-describe-0123456789abcdef"
    monkeypatch.setenv("ACME_KEY", secret)
    spec = manifests.parse_text(ACME)

    rows = dict(manifests.describe_provider(spec))
    local = dict(manifests.describe_provider(ai_config.OLLAMA))

    assert rows["kind"].startswith("CLOUD") and "api.acme.example" in rows["kind"]
    assert rows["api key"] == "ACME_KEY (set)"
    assert secret not in repr(rows)
    assert local["kind"].startswith("LOCAL")


def test_render_manifest_round_trips(providers_dir) -> None:
    text = manifests.render_manifest(
        name="campus",
        label="Campus \"AI\" Gateway",
        dialect="anthropic-messages",
        host="https://ai.example.edu/v1",
        vision_model="v",
        text_model="t",
        api_key_env="CAMPUS_KEY",
        credential_command=("op", "read", "op://vault/key"),
        pricing=ai_config.ProviderPricing(input_per_mtok=3.0, output_per_mtok=15.0),
    )

    spec = manifests.parse_text(text)

    assert spec.label == 'Campus "AI" Gateway'
    assert spec.credential_command == ("op", "read", "op://vault/key")
    assert spec.pricing == ai_config.ProviderPricing(3.0, 15.0)


def test_the_consent_fingerprint_follows_what_consent_was_given_to() -> None:
    base = manifests.parse_text(ACME)
    same = manifests.parse_text(ACME.replace("acme-text", "other-text"))
    moved = manifests.parse_text(ACME.replace("api.acme.example", "api.other.example"))

    assert manifests.fingerprint(base) == manifests.fingerprint(same)
    assert manifests.fingerprint(base) != manifests.fingerprint(moved)


def test_a_probe_of_one_provider_still_finds_its_key(providers_dir) -> None:
    """`for_provider` ignores ambient *configuration*, not the credential.

    It once resolved with an empty environment, so `opengrader providers test`
    reported "needs an API key" for every provider that has one.
    """
    _write(providers_dir, "acme", ACME)
    ai_config.reload_manifests()

    probe = ai_config.for_provider(
        "acme", environ={"ACME_KEY": "k" * 24, ai_config.ENV_HOST: "http://elsewhere"}
    )

    assert probe.api_key == "k" * 24
    assert probe.host == "https://api.acme.example/v1"  # OPENGRADER_AI_HOST ignored
