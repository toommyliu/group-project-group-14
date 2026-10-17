from __future__ import annotations

import json
from pathlib import Path

import pytest

from opengrader.core import app_config

# The VisionReader a fake cloud provider hands back. Module-level rather
# than yielded, so the fixture's existing 4-tuple shape stays put.
_READER_SENTINEL = object()


def test_default_settings_serialize_to_only_the_version() -> None:
    body = app_config.AISettings().to_dict()

    assert body == {"$schema": "./config.schema.json", "version": app_config.SETTINGS_VERSION}


def test_a_single_changed_field_resolves_alone_others_still_default() -> None:
    settings = app_config.AISettings(vision_model="my-custom-vlm")

    resolved = app_config.resolve(settings, environ={})

    assert resolved.vision_model == "my-custom-vlm"
    assert resolved.host == app_config.DEFAULT_HOST
    assert resolved.text_model == app_config.DEFAULT_TEXT_MODEL
    assert resolved.timeout_seconds == app_config.DEFAULT_TIMEOUT_SECONDS
    assert resolved.min_confidence == app_config.DEFAULT_MIN_CONFIDENCE


@pytest.mark.parametrize(
    "settings",
    [
        app_config.AISettings(),
        app_config.AISettings(
            provider="ollama",
            host="http://example:1234",
            vision_model="v-model",
            text_model="t-model",
            math_backend="pix2tex",
            timeout_seconds=45.0,
            min_confidence=0.42,
        ),
        app_config.AISettings(host="http://only-host-changed:9999"),
    ],
)
def test_from_dict_of_to_dict_round_trips(settings: app_config.AISettings) -> None:
    assert app_config.AISettings.from_dict(settings.to_dict()) == settings


def test_load_settings_missing_file_returns_defaults(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist" / "config.json"

    assert app_config.load_settings(missing) == app_config.AISettings()


def test_load_settings_invalid_utf8_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_bytes(b"\xff\xfe\x00\x01not-utf8")

    assert app_config.load_settings(path) == app_config.AISettings()


def test_load_settings_unreadable_path_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.mkdir()

    assert app_config.load_settings(path) == app_config.AISettings()


def test_load_settings_non_json_text_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("this is not { json", encoding="utf-8")

    assert app_config.load_settings(path) == app_config.AISettings()


def test_load_settings_json_list_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    assert app_config.load_settings(path) == app_config.AISettings()


@pytest.mark.parametrize("bad_value", ["abc", -1, 2])
def test_from_dict_bad_min_confidence_falls_back_to_none(bad_value: object) -> None:
    settings = app_config.AISettings.from_dict({"min_confidence": bad_value})

    assert settings.min_confidence is None
    resolved = app_config.resolve(settings, environ={})
    assert resolved.min_confidence == app_config.DEFAULT_MIN_CONFIDENCE


@pytest.mark.parametrize("bad_value", [0, -5])
def test_from_dict_non_positive_timeout_falls_back_to_none(bad_value: object) -> None:
    settings = app_config.AISettings.from_dict({"timeout_seconds": bad_value})

    assert settings.timeout_seconds is None
    resolved = app_config.resolve(settings, environ={})
    assert resolved.timeout_seconds == app_config.DEFAULT_TIMEOUT_SECONDS


def test_file_wins_over_provider_default_when_no_env_override() -> None:
    settings = app_config.AISettings(host="http://from-file:1111")

    resolved = app_config.resolve(settings, environ={})

    assert resolved.host == "http://from-file:1111"


def test_setting_environment_variables_do_not_override_saved_values() -> None:
    settings = app_config.AISettings(host="http://saved:1111", vision_model="saved-model")

    resolved = app_config.resolve(
        settings,
        environ={
            "OPENGRADER_AI_HOST": "http://ignored:2222",
            "OPENGRADER_AI_VISION_MODEL": "ignored-model",
        },
    )

    assert resolved.host == "http://saved:1111"
    assert resolved.vision_model == "saved-model"


def test_unknown_provider_falls_back_to_the_default_provider() -> None:
    settings = app_config.AISettings(provider="not-a-thing")

    resolved = app_config.resolve(settings, environ={})

    assert resolved.provider.name == app_config.DEFAULT_PROVIDER


def test_unknown_provider_reports_one_sentence_naming_both_names() -> None:
    settings = app_config.AISettings(provider="not-a-thing")

    resolved = app_config.resolve(settings, environ={})
    problems = resolved.problems()

    assert len(problems) == 1
    assert "not-a-thing" in problems[0]
    for spec in app_config.registered_providers():
        assert spec.name in problems[0]


def test_well_configured_local_setup_has_no_problems() -> None:
    resolved = app_config.resolve(app_config.AISettings(), environ={})

    assert resolved.problems() == ()


@pytest.fixture
def fake_cloud_provider():
    original_providers = app_config.registered_providers()
    calls: dict[str, app_config.ResolvedAI] = {}
    text_sentinel = object()
    vision_sentinel = object()

    def _text_factory(resolved: app_config.ResolvedAI) -> object:
        calls["text"] = resolved
        return text_sentinel

    def _vision_factory(resolved: app_config.ResolvedAI) -> object:
        calls["vision"] = resolved
        return vision_sentinel

    def _vision_reader_factory(resolved: app_config.ResolvedAI) -> object:
        calls["vision_reader"] = resolved
        return _READER_SENTINEL

    spec = app_config.ProviderSpec(
        name="fake-cloud",
        label="Fake Cloud",
        summary="A fake cloud provider used only to exercise the registry.",
        default_host="https://fake.example/api",
        default_vision_model="fake-vision-1",
        default_text_model="fake-text-1",
        setup_hint="Set FAKE_KEY.",
        api_key_env="FAKE_KEY",
        requires_api_key=True,
        text_config_factory=_text_factory,
        vision_config_factory=_vision_factory,
        vision_reader_factory=_vision_reader_factory,
    )
    app_config.register_provider(spec)
    try:
        yield spec, text_sentinel, vision_sentinel, calls
    finally:
        app_config._REGISTRY.clear()
        for original in original_providers:
            app_config.register_provider(original)


def test_registered_provider_appears_in_the_registry(fake_cloud_provider) -> None:
    spec, _text, _vision, _calls = fake_cloud_provider

    assert spec in app_config.registered_providers()


def test_resolve_picks_up_the_fake_providers_defaults(fake_cloud_provider) -> None:
    spec, _text, _vision, _calls = fake_cloud_provider

    resolved = app_config.resolve(app_config.AISettings(provider=spec.name), environ={})

    assert resolved.provider is spec
    assert resolved.host == spec.default_host
    assert resolved.vision_model == spec.default_vision_model
    assert resolved.text_model == spec.default_text_model


def test_a_config_from_when_runs_could_start_ollama_still_loads() -> None:
    # `auto_start` is gone; a config.json written before keeps its other
    # settings, and the next save drops the old key.
    settings = app_config.AISettings.from_dict(
        {"auto_start": True, "text_model": "t-model", "read_handwriting": True}
    )

    assert settings.text_model == "t-model" and settings.read_handwriting is True
    assert "auto_start" not in settings.to_dict()


def test_cloud_provider_sends_data_off_machine(fake_cloud_provider) -> None:
    spec, _text, _vision, _calls = fake_cloud_provider

    resolved = app_config.resolve(app_config.AISettings(provider=spec.name), environ={})

    assert resolved.sends_data_off_machine is True


def _consented(spec: app_config.ProviderSpec, **environ: str) -> app_config.AISettings:
    settings = app_config.AISettings(provider=spec.name)
    return app_config.consent.grant_cloud_consent(
        settings, app_config.resolve(settings, environ=dict(environ))
    )


def test_missing_api_key_is_reported_by_name(fake_cloud_provider) -> None:
    spec, _text, _vision, _calls = fake_cloud_provider

    resolved = app_config.resolve(_consented(spec), environ={})
    problems = resolved.problems()

    assert len(problems) == 1
    assert "FAKE_KEY" in problems[0]


def test_supplying_the_key_via_environ_clears_the_problem(fake_cloud_provider) -> None:
    spec, _text, _vision, _calls = fake_cloud_provider

    resolved = app_config.resolve(
        _consented(spec),
        environ={"FAKE_KEY": "sekrit-value"},
    )

    assert resolved.problems() == ()
    assert resolved.api_key == "sekrit-value"


def test_resolve_api_key_never_reads_disk_and_is_none_for_local(
    fake_cloud_provider,
) -> None:
    ollama = app_config.get_provider("ollama")

    assert app_config.resolve_api_key(ollama, environ={"SOME_OTHER_KEY": "x"}) is None
    assert app_config.resolve_api_key(ollama, environ={}) is None


def test_text_and_vision_config_route_through_the_fake_factories(
    fake_cloud_provider,
) -> None:
    spec, text_sentinel, vision_sentinel, calls = fake_cloud_provider
    resolved = app_config.resolve(app_config.AISettings(provider=spec.name), environ={})

    assert app_config.text_config(resolved) is text_sentinel
    assert app_config.vision_config(resolved) is vision_sentinel
    assert calls["text"] is resolved
    assert calls["vision"] is resolved


def test_saved_settings_never_contain_key_material(
    fake_cloud_provider, tmp_path: Path
) -> None:
    spec, _text, _vision, _calls = fake_cloud_provider
    resolved = app_config.resolve(
        app_config.AISettings(provider=spec.name),
        environ={"FAKE_KEY": "sekrit-value"},
    )
    path = tmp_path / "config.json"

    app_config.save_settings(resolved.settings, path)

    written = path.read_text(encoding="utf-8")
    assert "sekrit-value" not in written
    assert "FAKE_KEY" not in written


def test_settings_path_defaults_under_a_per_user_config_dir() -> None:
    path = app_config.settings_path()

    assert path.name == app_config.SETTINGS_FILENAME
    assert path.parent == app_config._config_dir()


def test_save_settings_creates_missing_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "does" / "not" / "exist" / "config.json"

    written = app_config.save_settings(app_config.AISettings(host="x"), target)

    assert written == target
    assert target.exists()


def test_save_settings_returns_the_path_it_wrote(tmp_path: Path) -> None:
    target = tmp_path / "config.json"

    written = app_config.save_settings(app_config.AISettings(), target)

    assert written == target
    config = json.loads(written.read_text(encoding="utf-8"))
    schema = json.loads((tmp_path / "config.schema.json").read_text(encoding="utf-8"))
    assert config["$schema"] == "./config.schema.json"
    assert schema["properties"]["vision_model"]["type"] == "string"
    assert schema["properties"]["version"]["const"] == 1


def test_save_settings_failure_propagates(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("i am a file, not a directory", encoding="utf-8")
    target = blocked / "config.json"

    with pytest.raises(OSError):
        app_config.save_settings(app_config.AISettings(), target)


def test_deliberate_feature_choices_round_trip() -> None:
    settings = app_config.AISettings(
        read_handwriting=True,
        escalate_only=True,
        transcribe_math=True,
        grade_written=True,
        auto_grade=False,
        require_calibration=True,
        min_confidence_measured=True,
    )

    body = settings.to_dict()

    assert body == {
        "$schema": "./config.schema.json",
        "version": app_config.SETTINGS_VERSION,
        "read_handwriting": True,
        "escalate_only": True,
        "transcribe_math": True,
        "grade_written": True,
        "auto_grade": False,
        "require_calibration": True,
        "min_confidence_measured": True,
    }
    assert app_config.AISettings.from_dict(body) == settings


@pytest.mark.parametrize(
    "field_name",
    [
        "read_handwriting",
        "transcribe_math",
        "grade_written",
        "require_calibration",
        "min_confidence_measured",
    ],
)
@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, True), (False, False), ("yes", True), ("off", False), ("garbage", False)],
)
def test_from_dict_coerces_each_bool_toggle_falling_back_to_false(
    field_name: str, raw: object, expected: bool
) -> None:
    settings = app_config.AISettings.from_dict({field_name: raw})

    assert getattr(settings, field_name) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, True), (False, False), ("yes", True), ("garbage", True)],
)
def test_from_dict_auto_grade_falls_back_to_true_on_rubbish(
    raw: object, expected: bool
) -> None:
    settings = app_config.AISettings.from_dict({"auto_grade": raw})

    assert settings.auto_grade is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, True), (False, False), ("yes", True), ("off", False), ("garbage", True)],
)
def test_from_dict_read_identity_locally_falls_back_to_true_on_rubbish(
    raw: object, expected: bool
) -> None:
    settings = app_config.AISettings.from_dict({"read_identity_locally": raw})

    assert settings.read_identity_locally is expected


def test_read_identity_locally_round_trips_and_is_omitted_when_default() -> None:
    default = app_config.AISettings()
    assert "read_identity_locally" not in default.to_dict()

    off = app_config.AISettings(read_identity_locally=False)
    body = off.to_dict()
    assert body["read_identity_locally"] is False
    assert app_config.AISettings.from_dict(body) == off


def test_read_identity_locally_carries_onto_resolved_ai() -> None:
    assert app_config.resolve(app_config.AISettings(), environ={}).read_identity_locally is True
    assert app_config.resolve(
        app_config.AISettings(read_identity_locally=False), environ={}
    ).read_identity_locally is False


def test_read_identity_locally_is_in_the_generated_schema(tmp_path: Path) -> None:
    app_config.save_settings(app_config.AISettings(), tmp_path / "config.json")
    schema = json.loads((tmp_path / "config.schema.json").read_text(encoding="utf-8"))

    assert schema["properties"]["read_identity_locally"]["type"] == "boolean"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, True), (False, False), ("yes", True), ("off", False), ("garbage", None), (None, None)],
)
def test_from_dict_escalate_only_is_tri_state_falling_back_to_unset(
    raw: object, expected: bool | None
) -> None:
    settings = app_config.AISettings.from_dict({"escalate_only": raw})

    assert settings.escalate_only is expected


def test_min_confidence_measured_has_no_environment_override() -> None:
    assert not hasattr(app_config, "ENV_MIN_CONFIDENCE_MEASURED")

    settings = app_config.AISettings(min_confidence_measured=True)
    resolved = app_config.resolve(
        settings, environ={"OPENGRADER_AI_MIN_CONFIDENCE_MEASURED": "false"}
    )

    assert resolved.settings.min_confidence_measured is True


def test_escalate_only_and_transcribe_math_resolve_false_when_reading_is_off() -> None:
    settings = app_config.AISettings(
        read_handwriting=False, escalate_only=True, transcribe_math=True
    )

    resolved = app_config.resolve(settings, environ={})

    assert resolved.escalate_only is False
    assert resolved.transcribe_math is False


def test_escalate_only_and_transcribe_math_resolve_true_when_reading_is_on() -> None:
    settings = app_config.AISettings(
        read_handwriting=True, escalate_only=True, transcribe_math=True
    )

    resolved = app_config.resolve(settings, environ={})

    assert resolved.escalate_only is True
    assert resolved.transcribe_math is True


@pytest.mark.parametrize(
    ("read_handwriting", "grade_written", "expected"),
    [(False, False, False), (True, False, True), (False, True, True), (True, True, True)],
)
def test_uses_ai_true_when_either_job_would_call_a_model(
    read_handwriting: bool, grade_written: bool, expected: bool
) -> None:
    settings = app_config.AISettings(
        read_handwriting=read_handwriting, grade_written=grade_written
    )

    resolved = app_config.resolve(settings, environ={})

    assert resolved.uses_ai is expected


def test_problems_reports_the_calibration_finding_only_when_all_three_hold() -> None:
    settings = app_config.AISettings(
        grade_written=True, require_calibration=True, min_confidence_measured=False
    )

    resolved = app_config.resolve(settings, environ={})
    problems = resolved.problems()

    assert len(problems) == 1
    assert "never measured" in problems[0]
    assert "llm-eval" in problems[0]


@pytest.mark.parametrize(
    ("grade_written", "require_calibration", "min_confidence_measured"),
    [
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ],
)
def test_problems_omits_the_calibration_finding_unless_all_three_hold(
    grade_written: bool, require_calibration: bool, min_confidence_measured: bool
) -> None:
    settings = app_config.AISettings(
        grade_written=grade_written,
        require_calibration=require_calibration,
        min_confidence_measured=min_confidence_measured,
    )

    resolved = app_config.resolve(settings, environ={})

    assert resolved.problems() == ()


def test_recommend_timeout_none_below_the_warn_fraction() -> None:
    assert app_config.recommend_timeout(measured=50.0, limit=120.0) is None


def test_recommend_timeout_none_when_zero_or_negative() -> None:
    assert app_config.recommend_timeout(measured=0.0, limit=120.0) is None
    assert app_config.recommend_timeout(measured=-5.0, limit=120.0) is None


def test_recommend_timeout_none_when_the_rounded_value_is_not_an_increase() -> None:
    assert app_config.recommend_timeout(measured=30.0, limit=120.0) is None


def test_recommend_timeout_a_real_cold_measurement() -> None:
    assert app_config.recommend_timeout(measured=80.0, limit=120.0) == 330.0


def test_recommend_timeout_rounds_up_to_the_next_multiple_of_thirty() -> None:
    assert app_config.recommend_timeout(measured=65.0, limit=120.0) == 270.0


def test_measure_generate_latency_success_reports_ok_and_share_of_limit() -> None:
    settings = app_config.AISettings(timeout_seconds=100.0, vision_model="qwen2.5vl:7b")
    resolved = app_config.resolve(settings, environ={})
    calls: list[dict] = []

    def fake_generate(**kwargs):
        calls.append(kwargs)
        return "ready"

    result = app_config.measure_generate_latency(
        resolved,
        role="handwriting",
        generate=fake_generate,
    )

    assert result.ok is True
    assert result.error == ""
    assert result.model == "qwen2.5vl:7b"
    assert result.limit == 100.0
    assert result.seconds is not None and result.seconds >= 0
    assert result.share_of_limit == pytest.approx(result.seconds / 100.0)
    assert "qwen2.5vl:7b" in result.describe()
    assert calls and calls[0]["num_predict"] == 8


def test_measure_generate_latency_never_raises_on_a_broken_generate() -> None:
    settings = app_config.AISettings(timeout_seconds=90.0, text_model="llama3.1")
    resolved = app_config.resolve(settings, environ={})

    def broken_generate(**kwargs):
        raise RuntimeError("connection refused")

    result = app_config.measure_generate_latency(
        resolved,
        role="grading",
        generate=broken_generate,
    )

    assert result.ok is False
    assert result.seconds is None
    assert "connection refused" in result.error
    assert result.share_of_limit is None
    assert "llama3.1" in result.describe()
    assert "did not answer" in result.describe()


def test_measure_generate_latency_picks_the_model_for_the_role() -> None:
    settings = app_config.AISettings(
        vision_model="vision-x", text_model="text-y", timeout_seconds=60.0
    )
    resolved = app_config.resolve(settings, environ={})

    def fake_generate(**kwargs):
        return "ready"

    handwriting = app_config.measure_generate_latency(
        resolved, role="handwriting", generate=fake_generate
    )
    grading = app_config.measure_generate_latency(
        resolved, role="grading", generate=fake_generate
    )

    assert handwriting.model == "vision-x"
    assert grading.model == "text-y"


def test_vision_reader_routes_through_the_provider_not_through_ollama(
    fake_cloud_provider,
) -> None:
    spec, _text, _vision, calls = fake_cloud_provider
    resolved = app_config.resolve(_consented(spec), environ={})

    reader = app_config.vision_reader(resolved)
    assert reader.inner is _READER_SENTINEL  # wrapped by GuardedVisionReader
    assert calls["vision_reader"] is resolved


def test_the_local_provider_hands_back_a_real_vision_reader() -> None:
    from opengrader.core.ocr.vision_reader import (
        GuardedVisionReader,
        OllamaVisionReader,
        VisionReader,
    )

    reader = app_config.vision_reader(app_config.for_provider("ollama"))

    assert isinstance(reader, GuardedVisionReader)
    assert isinstance(reader, VisionReader)
    assert isinstance(reader.inner, OllamaVisionReader)
    assert reader.inner.config.model == app_config.DEFAULT_VISION_MODEL
    assert reader.cloud is False and reader.local_identity_config is None


def test_a_vision_reader_survives_the_process_boundary() -> None:
    import pickle

    reader = app_config.vision_reader(app_config.for_provider("ollama"))

    assert pickle.loads(pickle.dumps(reader)) == reader
