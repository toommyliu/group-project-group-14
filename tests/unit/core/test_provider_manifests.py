from __future__ import annotations

import json
from pathlib import Path

import pytest

from opengrader.core import app_config
from opengrader.core.app_config import manifests

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
    directory = app_config.manifests_dir()
    directory.mkdir(parents=True, exist_ok=True)
    app_config.reload_manifests()
    try:
        yield directory
    finally:
        for path in directory.glob("*.toml"):
            path.unlink()
        app_config.reload_manifests()


def _consented_resolve(name: str, environ: dict[str, str]) -> app_config.ResolvedAI:
    """Resolve a cloud manifest provider past the cloud consent check."""
    settings = app_config.AISettings(provider=name)
    settings = app_config.consent.grant_cloud_consent(
        settings, app_config.resolve(settings, environ=environ)
    )
    return app_config.resolve(settings, environ=environ)


def _write(directory: Path, name: str, text: str) -> Path:
    path = directory / f"{name}.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_fresh_install_registers_only_the_local_ollama_provider(providers_dir) -> None:
    providers = app_config.registered_providers()

    assert [spec.name for spec in providers] == ["ollama"]
    assert providers[0].kind == "local"
    assert app_config.resolve(app_config.AISettings(), environ={}).provider.name == "ollama"
    assert app_config.manifest_problems() == ()


def test_shipped_examples_are_never_loaded_on_their_own(providers_dir) -> None:
    assert manifests.example_names(), "the examples directory should ship files"

    names = {spec.name for spec in app_config.registered_providers()}

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


def test_a_manifest_file_becomes_a_registered_provider(providers_dir) -> None:
    path = _write(providers_dir, "acme", ACME)

    app_config.reload_manifests()
    spec = app_config.get_provider("acme")

    assert spec.name == "acme"
    assert spec.source == "user"
    assert spec.manifest_path == str(path)
    assert spec.dialect == "openai-chat"
    assert spec.requires_api_key is True
    assert spec.kind == "cloud"
    assert spec.sends_data_off_machine is True
    assert app_config.registered_providers()[0].name == "ollama"


def test_a_manifest_provider_resolves_to_dialect_clients(providers_dir) -> None:
    from opengrader.core.llm.dialect_client import DialectConfig
    from opengrader.core.ocr.vision_reader import DialectVisionReader

    _write(providers_dir, "acme", ACME)
    app_config.reload_manifests()

    resolved = _consented_resolve("acme", {"ACME_KEY": "k" * 24})
    reader = app_config.vision_reader(resolved).inner  # unwrap GuardedVisionReader
    text = app_config.text_config(resolved)

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
    app_config.reload_manifests()
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
    resolved = _consented_resolve("acme", {"ACME_KEY": "k" * 24})

    reply = app_config.text_generate(resolved)("say ready")
    measured = app_config.measure_generate_latency(resolved, role="grading")

    assert reply == "ready"
    assert measured.ok, measured.error
    assert [r.full_url for r in seen] == [
        "https://api.acme.example/v1/chat/completions"
    ] * 2
    assert all(r.get_header("Authorization") == f"Bearer {'k' * 24}" for r in seen)


def test_a_missing_key_is_a_named_problem_not_a_crash(providers_dir) -> None:
    _write(providers_dir, "acme", ACME)
    app_config.reload_manifests()

    resolved = _consented_resolve("acme", {})

    assert any("ACME_KEY" in problem for problem in resolved.problems())
    with pytest.raises(app_config.AIUnavailableError, match="ACME_KEY"):
        app_config.ensure_ready(resolved)

def test_a_manifest_that_declares_kind_is_rejected_even_for_a_public_host(
    providers_dir,
) -> None:
    _write(providers_dir, "sneaky", ACME.replace('"acme"', '"sneaky"') + 'kind = "local"\n')

    problems = app_config.reload_manifests()

    assert "sneaky" not in {spec.name for spec in app_config.registered_providers()}
    assert len(problems) == 1 and "kind" in problems[0]
    spec = manifests.parse_text(ACME)
    assert spec.kind == "cloud"
    assert "kind" not in app_config.ProviderSpec.__dataclass_fields__


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
        ("https://8.8.8.8/v1", "cloud"),
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
    resolved = app_config.resolve(
        app_config.AISettings(host="https://ollama.example.com"), environ={}
    )

    assert resolved.provider.name == "ollama"
    assert resolved.kind == "cloud"


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

    problems = app_config.reload_manifests()
    names = {spec.name for spec in app_config.registered_providers()}
    resolved = app_config.resolve(app_config.AISettings(), environ={})

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

    problems = app_config.reload_manifests()

    assert app_config.get_provider("acme").default_vision_model == "acme-vision"
    assert len(problems) == 1
    assert str(second) in problems[0] and "already used" in problems[0]


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
    app_config.reload_manifests()

    removed = manifests.remove_manifest(app_config.get_provider("acme"))
    app_config.reload_manifests()

    assert not removed.exists()
    assert "acme" not in {spec.name for spec in app_config.registered_providers()}
    with pytest.raises(manifests.ManifestError, match="built-in"):
        manifests.remove_manifest(app_config.OLLAMA)


def test_describe_states_where_data_goes_and_never_the_key(monkeypatch) -> None:
    secret = "sk-describe-0123456789abcdef"
    monkeypatch.setenv("ACME_KEY", secret)
    spec = manifests.parse_text(ACME)

    rows = dict(manifests.describe_provider(spec))
    local = dict(manifests.describe_provider(app_config.OLLAMA))

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
        pricing=app_config.ProviderPricing(input_per_mtok=3.0, output_per_mtok=15.0),
    )

    spec = manifests.parse_text(text)

    assert spec.label == 'Campus "AI" Gateway'
    assert spec.credential_command == ("op", "read", "op://vault/key")
    assert spec.pricing == app_config.ProviderPricing(3.0, 15.0)


def test_a_probe_of_one_provider_still_finds_its_key(providers_dir) -> None:
    _write(providers_dir, "acme", ACME)
    app_config.reload_manifests()

    probe = app_config.for_provider(
        "acme", environ={"ACME_KEY": "k" * 24, "OPENGRADER_AI_HOST": "http://elsewhere"}
    )

    assert probe.api_key == "k" * 24
    assert probe.host == "https://api.acme.example/v1"  # OPENGRADER_AI_HOST ignored


# ---------------------------------------------------------------------------
# `dialect = "pydantic-ai"` — a backend, not a wire dialect. No test here
# needs the optional `pydantic-ai` package: manifest parsing, the derived
# `kind`, and the factories on the resulting `ProviderSpec` never import it —
# only an actual `generate()` call does (see tests/unit/core/llm/
# test_pydantic_ai_client.py and tests/unit/ocr/test_pydantic_ai_reader.py,
# which do need it and skip themselves when it is missing).
# ---------------------------------------------------------------------------

PYDANTIC_AI_ANTHROPIC = """\
name         = "claude-direct"
label        = "Claude (direct)"
dialect      = "pydantic-ai"
vision_model = "anthropic:claude-haiku-4-5-20251001"
text_model   = "anthropic:claude-haiku-4-5-20251001"
api_key_env  = "CLAUDE_DIRECT_KEY"
"""

PYDANTIC_AI_COHERE = """\
name         = "cohere-direct"
label        = "Cohere (direct)"
dialect      = "pydantic-ai"
vision_model = "cohere:command-a-vision-07-2025"
text_model   = "cohere:command-a-vision-07-2025"
api_key_env  = "COHERE_DIRECT_KEY"
"""

PYDANTIC_AI_BEDROCK = """\
name         = "bedrock-direct"
label        = "Bedrock (direct)"
dialect      = "pydantic-ai"
vision_model = "bedrock:anthropic.claude-haiku-4-5-20251001-v1:0"
text_model   = "bedrock:anthropic.claude-haiku-4-5-20251001-v1:0"
"""

PYDANTIC_AI_VERTEX = """\
name         = "vertex-direct"
label        = "Vertex (direct)"
dialect      = "pydantic-ai"
vision_model = "google-cloud:gemini-2.5-flash"
text_model   = "google-cloud:gemini-2.5-flash"
"""


def test_a_pydantic_ai_manifest_may_omit_host_and_derives_cloud_from_the_prefix() -> None:
    spec = manifests.parse_text(PYDANTIC_AI_ANTHROPIC)

    assert spec.dialect == "pydantic-ai"
    assert spec.kind == "cloud"
    assert spec.default_host == manifests.PYDANTIC_AI_PREFIXES["anthropic"].display_host
    assert spec.default_vision_model == "anthropic:claude-haiku-4-5-20251001"


def test_a_pydantic_ai_manifest_still_cannot_declare_kind() -> None:
    with pytest.raises(manifests.ManifestError, match="derived from `host`"):
        manifests.parse_text(PYDANTIC_AI_ANTHROPIC + 'kind = "local"\n')


def test_a_pydantic_ai_manifest_rejects_an_unknown_prefix() -> None:
    text = PYDANTIC_AI_ANTHROPIC.replace("anthropic:", "not-a-real-prefix:")

    with pytest.raises(manifests.ManifestError, match="unknown Pydantic AI provider prefix"):
        manifests.parse_text(text)


def test_a_pydantic_ai_manifest_rejects_a_model_id_with_no_prefix() -> None:
    text = PYDANTIC_AI_ANTHROPIC.replace(
        'vision_model = "anthropic:claude-haiku-4-5-20251001"',
        'vision_model = "claude-haiku-4-5-20251001"',
    )

    with pytest.raises(manifests.ManifestError, match='"prefix:model-name"'):
        manifests.parse_text(text)


def test_a_pydantic_ai_manifest_requires_the_same_prefix_for_both_models() -> None:
    text = PYDANTIC_AI_ANTHROPIC.replace(
        'text_model   = "anthropic:claude-haiku-4-5-20251001"',
        'text_model   = "openai:gpt-5-mini"',
    )

    with pytest.raises(manifests.ManifestError, match="same Pydantic AI provider prefix"):
        manifests.parse_text(text)


def test_a_pydantic_ai_manifest_rejects_a_host_for_a_prefix_that_ignores_it() -> None:
    """Cohere's Pydantic AI provider does not accept a base URL: a provider's
    `kind` is derived, never declared, and would otherwise be dodgeable by a
    `host` nobody reads, used to claim a `local` kind this backend can't
    deliver."""
    text = PYDANTIC_AI_COHERE + 'host = "http://127.0.0.1:9009"\n'

    with pytest.raises(manifests.ManifestError, match="does not accept a custom `host`"):
        manifests.parse_text(text)


def test_a_pydantic_ai_manifest_with_a_loopback_host_derives_kind_local() -> None:
    """A prefix that really does honour a base URL can be pointed at loopback —
    the one way this backend's `kind` can be "local"."""
    text = PYDANTIC_AI_ANTHROPIC + 'host = "http://127.0.0.1:9009/v1"\n'

    spec = manifests.parse_text(text)

    assert spec.default_host == "http://127.0.0.1:9009/v1"
    assert spec.kind == "local"


def test_a_pydantic_ai_manifest_requires_api_key_env_for_a_key_based_prefix() -> None:
    text = "\n".join(
        line for line in PYDANTIC_AI_ANTHROPIC.splitlines() if not line.startswith("api_key_env")
    )

    with pytest.raises(manifests.ManifestError, match="needs `api_key_env`"):
        manifests.parse_text(text)


@pytest.mark.parametrize("text", [PYDANTIC_AI_BEDROCK, PYDANTIC_AI_VERTEX])
def test_a_pydantic_ai_manifest_does_not_require_api_key_env_for_a_credential_chain_prefix(
    text: str,
) -> None:
    """Bedrock and Vertex authenticate through a platform credential chain
    (AWS / Google ADC); `api_key_env` stays optional for them."""
    spec = manifests.parse_text(text)

    assert spec.requires_api_key is False
    assert spec.api_key_env == ""
    assert spec.kind == "cloud"


def test_a_pydantic_ai_manifest_cannot_claim_model_listing() -> None:
    text = PYDANTIC_AI_ANTHROPIC + "supports_model_listing = true\n"

    with pytest.raises(manifests.ManifestError, match="cannot set supports_model_listing"):
        manifests.parse_text(text)
    assert manifests.parse_text(PYDANTIC_AI_ANTHROPIC).supports_model_listing is False


def test_a_pydantic_ai_manifest_is_skipped_with_a_clear_problem_when_the_package_is_missing(
    providers_dir, monkeypatch
) -> None:
    _write(providers_dir, "claude-direct", PYDANTIC_AI_ANTHROPIC)
    monkeypatch.setattr(manifests, "_pydantic_ai_available", lambda: False)

    problems = app_config.reload_manifests()

    assert "claude-direct" not in {spec.name for spec in app_config.registered_providers()}
    assert len(problems) == 1
    assert "pydantic-ai" in problems[0]
    assert "uv sync --extra pydantic-ai" in problems[0]


def test_a_pydantic_ai_manifest_loads_normally_when_the_package_is_present(
    providers_dir,
) -> None:
    """The other half of the degradation test: with the package available (as
    it is in this dev environment), the same manifest loads like any other."""
    pytest.importorskip("pydantic_ai")
    _write(providers_dir, "claude-direct", PYDANTIC_AI_ANTHROPIC)

    problems = app_config.reload_manifests()

    assert problems == ()
    assert "claude-direct" in {spec.name for spec in app_config.registered_providers()}


def test_a_pydantic_ai_manifest_resolves_to_its_own_config_and_reader_types(
    providers_dir,
) -> None:
    """Wiring only — never imports `pydantic_ai` itself, so it runs either way."""
    from opengrader.core.llm.pydantic_ai_client import PydanticAIConfig
    from opengrader.core.ocr.pydantic_ai_reader import PydanticAIVisionReader

    _write(providers_dir, "claude-direct", PYDANTIC_AI_ANTHROPIC)
    app_config.reload_manifests()

    resolved = _consented_resolve("claude-direct", {"CLAUDE_DIRECT_KEY": "k" * 24})
    reader = app_config.vision_reader(resolved).inner  # unwrap GuardedVisionReader
    text_cfg = app_config.text_config(resolved)
    vision_cfg = app_config.vision_config(resolved)

    assert resolved.problems() == ()
    assert isinstance(reader, PydanticAIVisionReader)
    assert isinstance(reader.config, PydanticAIConfig)
    assert reader.config.model == "anthropic:claude-haiku-4-5-20251001"
    assert reader.config.api_key == "k" * 24
    assert reader.config.max_attempts > 1  # a hosted API gets retries
    assert reader.config.base_url is None  # no host override -> the SDK's own endpoint
    assert isinstance(text_cfg, PydanticAIConfig)
    assert text_cfg.model == "anthropic:claude-haiku-4-5-20251001"
    assert isinstance(vision_cfg, PydanticAIConfig)
    # available_models()/ensure_ready() must never reach for a dialect this
    # backend does not speak.
    assert app_config.available_models(resolved) == []


def test_a_pydantic_ai_manifest_with_a_custom_host_becomes_the_base_url(
    providers_dir,
) -> None:
    """A prefix that honours a base URL and names a real (non-loopback) host
    stays `kind = "cloud"` but the host is still handed to the SDK — e.g. a
    department's own proxy in front of Anthropic's API."""
    from opengrader.core.llm.pydantic_ai_client import PydanticAIConfig

    text = PYDANTIC_AI_ANTHROPIC + 'host = "https://proxy.example.edu/anthropic"\n'
    _write(providers_dir, "claude-direct", text)
    app_config.reload_manifests()

    resolved = _consented_resolve("claude-direct", {"CLAUDE_DIRECT_KEY": "k" * 24})
    config = app_config.text_config(resolved)

    assert resolved.kind == "cloud"
    assert isinstance(config, PydanticAIConfig)
    assert config.base_url == "https://proxy.example.edu/anthropic"


def test_describe_provider_names_the_pydantic_ai_backend_and_its_auth_note() -> None:
    spec = manifests.parse_text(PYDANTIC_AI_ANTHROPIC)

    rows = dict(manifests.describe_provider(spec))

    assert "Pydantic AI" in rows["dialect"]
    assert "not a wire dialect" in rows["dialect"]
    assert rows["auth"] == manifests.PYDANTIC_AI_PREFIXES["anthropic"].auth_note


def test_pydantic_ai_modules_never_import_the_package_at_module_scope() -> None:
    """Lazy and optional. `manifests.py`'s factories import
    `opengrader.core.llm.pydantic_ai_client`/`...ocr.pydantic_ai_reader`
    unconditionally (not gated on the package being installed), so those two
    modules must themselves import `pydantic_ai` only inside functions —
    never at module scope, or importing them at all would fail outright on a
    machine without the optional extra. Checked with `ast` (not by actually
    uninstalling the package, which this dev environment needs for the rest
    of this test session) so this holds regardless of what is installed here.
    """
    import ast

    import opengrader.core.llm.pydantic_ai_client as client_module
    import opengrader.core.ocr.pydantic_ai_reader as reader_module

    for module in (client_module, reader_module):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in tree.body:  # module scope only; a nested import is fine.
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            offenders = [n for n in names if n.split(".")[0] == "pydantic_ai"]
            assert not offenders, (
                f"{module.__file__} imports {offenders} at module scope "
                f"(line {node.lineno}) — pydantic_ai must only be imported "
                "inside a function."
            )


# ---------------------------------------------------------------------------
# A pydantic-ai model must go to the host its kind and consent were decided on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "saved_model",
    [
        # Cohere's SDK ignores a base URL: it would go to api.cohere.com.
        "cohere:command-a-vision-07-2025",
        # A prefix this build does not know: nobody can say where it goes.
        "some-new-vendor:vision-1",
    ],
)
def test_a_saved_model_from_another_prefix_cannot_leave_a_local_pydantic_ai_host(
    providers_dir, saved_model: str
) -> None:
    """A loopback manifest is `local`: no consent, and the guard lets it read
    names and IDs. If a model saved in AI setup (or passed with a flag) sent
    the request to a cloud SDK default instead of that host, those crops
    would go to the cloud with no consent and no identity pin."""
    _write(
        providers_dir,
        "claude-direct",
        PYDANTIC_AI_ANTHROPIC + 'host = "http://127.0.0.1:9009/v1"\n',
    )
    app_config.reload_manifests()
    settings = app_config.AISettings(
        provider="claude-direct", vision_model=saved_model, text_model=saved_model
    )
    resolved = app_config.resolve(settings, environ={"CLAUDE_DIRECT_KEY": "k" * 24})
    assert resolved.kind == "local"

    with pytest.raises(app_config.AIUnavailableError, match="Nothing was sent"):
        app_config.vision_reader(resolved)
    with pytest.raises(app_config.AIUnavailableError, match="Nothing was sent"):
        app_config.text_generate(resolved)


def test_a_flag_override_to_another_prefix_is_refused_too(providers_dir) -> None:
    _write(
        providers_dir,
        "claude-direct",
        PYDANTIC_AI_ANTHROPIC + 'host = "http://127.0.0.1:9009/v1"\n',
    )
    app_config.reload_manifests()
    resolved = app_config.with_overrides(
        app_config.resolve(
            app_config.AISettings(provider="claude-direct"),
            environ={"CLAUDE_DIRECT_KEY": "k" * 24},
        ),
        vision_model="cohere:command-a-vision-07-2025",
    )

    with pytest.raises(app_config.AIUnavailableError):
        app_config.vision_reader(resolved)


def test_a_pydantic_ai_model_that_honours_the_host_is_pointed_at_it(providers_dir) -> None:
    _write(
        providers_dir,
        "claude-direct",
        PYDANTIC_AI_ANTHROPIC + 'host = "http://127.0.0.1:9009/v1"\n',
    )
    app_config.reload_manifests()
    resolved = app_config.resolve(
        app_config.AISettings(provider="claude-direct", vision_model="openai:local-vl"),
        environ={"CLAUDE_DIRECT_KEY": "k" * 24},
    )

    reader = app_config.vision_reader(resolved)

    assert reader.cloud is False
    assert reader.inner.config.base_url == "http://127.0.0.1:9009/v1"


def test_a_cloud_manifest_must_use_https() -> None:
    """Plain http to a cloud host would send answer images and the key in
    the clear."""
    with pytest.raises(manifests.ManifestError, match="must use https://"):
        manifests.parse_text(ACME.replace("https://api.acme.example", "http://api.acme.example"))
    with pytest.raises(manifests.ManifestError, match="must use https://"):
        manifests.parse_text(
            PYDANTIC_AI_ANTHROPIC + 'host = "http://gateway.example.com/v1"\n'
        )
    # A server on this computer or the local network may still use http.
    assert manifests.parse_text(
        ACME.replace("https://api.acme.example/v1", "http://192.168.1.20:8000/v1")
    ).kind == "local"


def test_the_latency_probe_never_times_a_model_that_would_leave_the_host(providers_dir) -> None:
    """Timing the handwriting model used to copy the text model's config and
    swap in the handwriting model's id, skipping the check on where that id
    sends a request."""
    _write(
        providers_dir,
        "claude-direct",
        PYDANTIC_AI_ANTHROPIC + 'host = "http://127.0.0.1:9009/v1"\n',
    )
    app_config.reload_manifests()
    resolved = app_config.resolve(
        app_config.AISettings(
            provider="claude-direct", vision_model="cohere:command-a-vision-07-2025"
        ),
        environ={"CLAUDE_DIRECT_KEY": "k" * 24},
    )
    calls: list = []

    measurement = app_config.measure_generate_latency(
        resolved,
        role="handwriting",
        generate=lambda **kwargs: calls.append(kwargs) or "ready",
    )

    assert calls == []
    assert not measurement.ok
    assert "Nothing was sent" in measurement.error
