from __future__ import annotations

import pytest

from opengrader.core import app_config


@pytest.fixture
def fake_provider_registry():
    original_providers = app_config.registered_providers()

    def _register(spec: app_config.ProviderSpec) -> app_config.ProviderSpec:
        app_config.register_provider(spec)
        return spec

    try:
        yield _register
    finally:
        app_config._REGISTRY.clear()
        for original in original_providers:
            app_config.register_provider(original)


def _make_spec(
    *,
    name: str = "fake-bridge",
    requires_api_key: bool = False,
    api_key_env: str = "",
    supports_model_listing: bool = True,
    list_models_factory=None,
) -> app_config.ProviderSpec:
    return app_config.ProviderSpec(
        name=name,
        label="Fake Bridge Provider",
        summary="A fake provider used only to exercise with_overrides/available_models/ensure_ready.",
        default_host="https://fake-bridge.example/api",
        default_vision_model="fake-vision-1",
        default_text_model="fake-text-1",
        setup_hint="Set FAKE_BRIDGE_KEY.",
        api_key_env=api_key_env,
        requires_api_key=requires_api_key,
        supports_model_listing=supports_model_listing,
        list_models_factory=list_models_factory,
    )


def _resolve(registry, spec: app_config.ProviderSpec, **settings_kwargs) -> app_config.ResolvedAI:
    registry(spec)
    settings = app_config.AISettings(provider=spec.name, **settings_kwargs)
    resolved = app_config.resolve(settings, environ={})
    if resolved.kind == "cloud":
        # These tests exercise the pre-flight itself, past the cloud consent check.
        settings = app_config.consent.grant_cloud_consent(settings, resolved)
        resolved = app_config.resolve(settings, environ={})
    return resolved


# with_overrides

class TestWithOverrides:
    def test_explicit_host_wins_over_resolved_value(self, fake_provider_registry) -> None:
        spec = _make_spec()
        resolved = _resolve(fake_provider_registry, spec)

        overridden = app_config.with_overrides(resolved, host="http://from-flag:9999")

        assert overridden.host == "http://from-flag:9999"

    def test_explicit_vision_model_wins_over_resolved_value(self, fake_provider_registry) -> None:
        spec = _make_spec()
        resolved = _resolve(fake_provider_registry, spec)

        overridden = app_config.with_overrides(resolved, vision_model="flag-vision")

        assert overridden.vision_model == "flag-vision"

    def test_explicit_text_model_wins_over_resolved_value(self, fake_provider_registry) -> None:
        spec = _make_spec()
        resolved = _resolve(fake_provider_registry, spec)

        overridden = app_config.with_overrides(resolved, text_model="flag-text")

        assert overridden.text_model == "flag-text"

    def test_explicit_timeout_wins_over_resolved_value(self, fake_provider_registry) -> None:
        spec = _make_spec()
        resolved = _resolve(fake_provider_registry, spec)

        overridden = app_config.with_overrides(resolved, timeout_seconds=45.0)

        assert overridden.timeout_seconds == 45.0

    def test_none_keeps_the_resolved_value_for_every_field(self, fake_provider_registry) -> None:
        spec = _make_spec()
        resolved = _resolve(
            fake_provider_registry,
            spec,
            host="http://saved:1111",
            vision_model="saved-vision",
            text_model="saved-text",
            timeout_seconds=77.0,
        )

        overridden = app_config.with_overrides(
            resolved,
            host=None,
            vision_model=None,
            text_model=None,
            timeout_seconds=None,
        )

        assert overridden.host == resolved.host
        assert overridden.vision_model == resolved.vision_model
        assert overridden.text_model == resolved.text_model
        assert overridden.timeout_seconds == resolved.timeout_seconds

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
    def test_blank_or_whitespace_string_keeps_the_resolved_value(
        self, fake_provider_registry, blank: str
    ) -> None:
        spec = _make_spec()
        resolved = _resolve(
            fake_provider_registry,
            spec,
            host="http://saved:1111",
            vision_model="saved-vision",
            text_model="saved-text",
        )

        overridden = app_config.with_overrides(
            resolved, host=blank, vision_model=blank, text_model=blank
        )

        assert overridden.host == resolved.host
        assert overridden.vision_model == resolved.vision_model
        assert overridden.text_model == resolved.text_model

    @pytest.mark.parametrize("bad_timeout", [0, 0.0, -1, -30.0])
    def test_zero_or_negative_timeout_keeps_the_resolved_value(
        self, fake_provider_registry, bad_timeout: float
    ) -> None:
        spec = _make_spec()
        resolved = _resolve(fake_provider_registry, spec, timeout_seconds=77.0)

        overridden = app_config.with_overrides(resolved, timeout_seconds=bad_timeout)

        assert overridden.timeout_seconds == resolved.timeout_seconds

    def test_provider_api_key_and_toggles_are_untouched(self, fake_provider_registry) -> None:
        spec = _make_spec(requires_api_key=True, api_key_env="FAKE_BRIDGE_KEY")
        fake_provider_registry(spec)
        settings = app_config.AISettings(
            provider=spec.name,
            read_handwriting=True,
            escalate_only=True,
            transcribe_math=True,
            grade_written=True,
            require_calibration=True,
        )
        resolved = app_config.resolve(
            settings, environ={"FAKE_BRIDGE_KEY": "sekrit"}
        )

        overridden = app_config.with_overrides(
            resolved,
            host="http://from-flag:9999",
            vision_model="flag-vision",
            text_model="flag-text",
            timeout_seconds=45.0,
        )

        assert overridden.provider is resolved.provider
        assert overridden.api_key == resolved.api_key
        assert overridden.read_handwriting == resolved.read_handwriting
        assert overridden.escalate_only == resolved.escalate_only
        assert overridden.transcribe_math == resolved.transcribe_math
        assert overridden.grade_written == resolved.grade_written
        assert overridden.require_calibration == resolved.require_calibration

    def test_values_are_stripped(self, fake_provider_registry) -> None:
        spec = _make_spec()
        resolved = _resolve(fake_provider_registry, spec)

        overridden = app_config.with_overrides(
            resolved,
            host="  http://padded:1234  ",
            vision_model="  padded-vision  ",
            text_model="  padded-text  ",
        )

        assert overridden.host == "http://padded:1234"
        assert overridden.vision_model == "padded-vision"
        assert overridden.text_model == "padded-text"

    def test_called_with_no_arguments_is_equivalent_to_resolve(
        self, fake_provider_registry
    ) -> None:
        spec = _make_spec()
        fake_provider_registry(spec)
        settings = app_config.AISettings(provider=spec.name, host="http://saved:1111")

        # with_overrides(None) re-runs resolve() internally with no explicit
        # settings/environ, matching a bare resolve() call in the same
        # process. We pin equivalence by calling with_overrides on an
        # already-resolved instance and confirming every override defaults
        # to a no-op, i.e. the result equals the input.
        resolved = app_config.resolve(settings, environ={})
        overridden = app_config.with_overrides(resolved)

        assert overridden == resolved


# available_models

class TestAvailableModels:
    def test_returns_empty_list_when_no_list_models_factory(self, fake_provider_registry) -> None:
        spec = _make_spec(list_models_factory=None, supports_model_listing=True)
        resolved = _resolve(fake_provider_registry, spec)

        assert app_config.available_models(resolved) == []

    def test_returns_empty_list_when_listing_unsupported_even_with_a_factory(
        self, fake_provider_registry
    ) -> None:
        calls: list[app_config.ResolvedAI] = []

        def factory(resolved: app_config.ResolvedAI) -> list[str]:
            calls.append(resolved)
            return ["should-not-be-returned"]

        spec = _make_spec(supports_model_listing=False, list_models_factory=factory)
        resolved = _resolve(fake_provider_registry, spec)

        result = app_config.available_models(resolved)

        assert result == []
        assert calls == []  # the factory must not even be called

    def test_returns_the_factorys_list_and_passes_resolved_through(
        self, fake_provider_registry
    ) -> None:
        calls: list[app_config.ResolvedAI] = []

        def factory(resolved: app_config.ResolvedAI) -> list[str]:
            calls.append(resolved)
            return ["model-a", "model-b"]

        spec = _make_spec(supports_model_listing=True, list_models_factory=factory)
        resolved = _resolve(fake_provider_registry, spec)

        result = app_config.available_models(resolved)

        assert result == ["model-a", "model-b"]
        assert calls == [resolved]


# ensure_ready

class TestEnsureReady:
    def test_raises_naming_the_env_var_when_key_required_and_missing(
        self, fake_provider_registry
    ) -> None:
        spec = _make_spec(requires_api_key=True, api_key_env="FAKE_BRIDGE_KEY")
        resolved = _resolve(fake_provider_registry, spec)

        with pytest.raises(app_config.AIUnavailableError) as excinfo:
            app_config.ensure_ready(resolved)

        assert "FAKE_BRIDGE_KEY" in str(excinfo.value)

    def test_returns_none_and_does_not_list_when_provider_cannot_enumerate(
        self, fake_provider_registry
    ) -> None:
        calls: list[app_config.ResolvedAI] = []

        def factory(resolved: app_config.ResolvedAI) -> list[str]:
            calls.append(resolved)
            return ["irrelevant"]

        spec = _make_spec(supports_model_listing=False, list_models_factory=factory)
        resolved = _resolve(fake_provider_registry, spec)

        result = app_config.ensure_ready(resolved)

        assert result is None
        assert calls == []

    def test_raises_naming_wanted_and_available_when_model_absent(
        self, fake_provider_registry
    ) -> None:
        def factory(resolved: app_config.ResolvedAI) -> list[str]:
            return ["model-a", "model-b"]

        spec = _make_spec(supports_model_listing=True, list_models_factory=factory)
        resolved = _resolve(
            fake_provider_registry, spec, vision_model="missing-model"
        )

        with pytest.raises(app_config.AIUnavailableError) as excinfo:
            app_config.ensure_ready(resolved, purpose="vision")

        message = str(excinfo.value)
        assert "missing-model" in message
        assert "model-a" in message
        assert "model-b" in message

    def test_returns_quietly_when_wanted_model_present(self, fake_provider_registry) -> None:
        def factory(resolved: app_config.ResolvedAI) -> list[str]:
            return ["model-a", "fake-vision-1"]

        spec = _make_spec(supports_model_listing=True, list_models_factory=factory)
        resolved = _resolve(fake_provider_registry, spec)

        assert app_config.ensure_ready(resolved, purpose="vision") is None

    def test_returns_quietly_when_list_comes_back_empty(self, fake_provider_registry) -> None:
        def factory(resolved: app_config.ResolvedAI) -> list[str]:
            return []

        spec = _make_spec(supports_model_listing=True, list_models_factory=factory)
        resolved = _resolve(
            fake_provider_registry, spec, vision_model="anything-at-all"
        )

        # An empty list means the service answered but told us nothing
        # useful; that is deliberately not evidence the model is missing.
        assert app_config.ensure_ready(resolved, purpose="vision") is None

    def test_wraps_provider_exception_in_ai_unavailable_error_chained(
        self, fake_provider_registry
    ) -> None:
        class FakeProviderClientError(RuntimeError):
            pass

        def factory(resolved: app_config.ResolvedAI) -> list[str]:
            raise FakeProviderClientError("connection refused")

        spec = _make_spec(supports_model_listing=True, list_models_factory=factory)
        resolved = _resolve(fake_provider_registry, spec)

        with pytest.raises(app_config.AIUnavailableError) as excinfo:
            app_config.ensure_ready(resolved)

        assert isinstance(excinfo.value.__cause__, FakeProviderClientError)
        assert "connection refused" in str(excinfo.value)

    def test_purpose_vision_checks_vision_model(self, fake_provider_registry) -> None:
        def factory(resolved: app_config.ResolvedAI) -> list[str]:
            return ["only-the-text-model"]

        spec = _make_spec(supports_model_listing=True, list_models_factory=factory)
        resolved = _resolve(
            fake_provider_registry,
            spec,
            vision_model="the-vision-model",
            text_model="only-the-text-model",
        )

        with pytest.raises(app_config.AIUnavailableError) as excinfo:
            app_config.ensure_ready(resolved, purpose="vision")
        assert "the-vision-model" in str(excinfo.value)

        # text purpose should pass since text_model IS in the list
        assert app_config.ensure_ready(resolved, purpose="text") is None

    def test_purpose_text_checks_text_model(self, fake_provider_registry) -> None:
        def factory(resolved: app_config.ResolvedAI) -> list[str]:
            return ["only-the-vision-model"]

        spec = _make_spec(supports_model_listing=True, list_models_factory=factory)
        resolved = _resolve(
            fake_provider_registry,
            spec,
            vision_model="only-the-vision-model",
            text_model="the-text-model",
        )

        with pytest.raises(app_config.AIUnavailableError) as excinfo:
            app_config.ensure_ready(resolved, purpose="text")
        assert "the-text-model" in str(excinfo.value)

        # vision purpose should pass since vision_model IS in the list
        assert app_config.ensure_ready(resolved, purpose="vision") is None


# ensure_ready when Ollama does not answer

def _ollama_down(monkeypatch, *, installed: bool, host: str = app_config.DEFAULT_HOST) -> str:
    import shutil

    def refused(resolved: app_config.ResolvedAI) -> list[str]:
        raise OSError("failed to reach Ollama at /api/tags: [Errno 61] Connection refused")

    monkeypatch.setattr(app_config, "available_models", refused)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/ollama" if installed else None)
    monkeypatch.setattr(app_config.sys, "platform", "linux")  # no /Applications/Ollama.app check
    with pytest.raises(app_config.AIUnavailableError) as excinfo:
        app_config.ensure_ready(app_config.for_provider("ollama", host=host), purpose="vision")
    return str(excinfo.value)


def test_ollama_not_running_says_so_and_how_to_start_it(monkeypatch) -> None:
    message = _ollama_down(monkeypatch, installed=True)
    assert message.startswith("Ollama isn't running.")
    assert "ollama serve" in message
    assert "Errno" not in message and "/api/tags" not in message


def test_ollama_not_installed_says_where_to_get_it(monkeypatch) -> None:
    message = _ollama_down(monkeypatch, installed=False)
    assert message.startswith("Ollama isn't installed.")
    assert "https://ollama.com" in message


def test_ollama_on_another_computer_names_that_host(monkeypatch) -> None:
    message = _ollama_down(monkeypatch, installed=False, host="http://gpu-box:11434")
    assert "http://gpu-box:11434" in message
    assert "installed" not in message
