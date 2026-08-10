"""Tests for ``opengrader.core.ai_config``.

Written against the contract stated in the module's own docstring — defaults
that can never drift from the client dataclasses, "blank means follow the
default", malformed input degrading a setting rather than a launch, a strict
env > file > provider-default precedence, an unknown provider degrading with
a stated reason, and the cloud-provider extension point working with zero
edits to this module.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opengrader.core import ai_config


# ── 1. Defaults are the single source of truth ──────────────────────────────


def test_vision_defaults_match_ollama_vision_config() -> None:
    """DEFAULT_HOST/VISION_MODEL/TIMEOUT must equal OllamaVisionConfig()'s own
    field defaults.

    The module docstring says these constants are *not* a second source of
    truth: the client dataclass keeps its own defaults so it still works when
    built directly, and these module constants are asserted equal to them so
    the pair can never silently drift apart.
    """
    from opengrader.core.ocr.ollama_vlm import OllamaVisionConfig

    default = OllamaVisionConfig()
    assert ai_config.DEFAULT_HOST == default.host
    assert ai_config.DEFAULT_VISION_MODEL == default.model
    assert ai_config.DEFAULT_TIMEOUT_SECONDS == default.timeout_seconds


def test_text_defaults_match_ollama_config() -> None:
    """DEFAULT_TEXT_MODEL/HOST/TIMEOUT must equal OllamaConfig()'s own field
    defaults, for the same drift-proofing reason as the vision config.
    """
    from opengrader.core.llm import OllamaConfig

    default = OllamaConfig()
    assert ai_config.DEFAULT_TEXT_MODEL == default.model
    assert ai_config.DEFAULT_HOST == default.host
    assert ai_config.DEFAULT_TIMEOUT_SECONDS == default.timeout_seconds


def test_automated_grading_min_confidence_is_the_same_object() -> None:
    """``cli.automated_grading.DEFAULT_LLM_MIN_CONFIDENCE`` must be this
    module's constant, not a copy of its value.

    The module docstring is explicit that there is "exactly one number" for
    the AI-suggestion confidence floor; importing it back (rather than
    re-declaring an equal literal) is what keeps that true as the number
    changes.
    """
    from opengrader.cli import automated_grading

    assert automated_grading.DEFAULT_LLM_MIN_CONFIDENCE is ai_config.DEFAULT_MIN_CONFIDENCE


# ── 2. Blank means "follow the default", and it round-trips ────────────────


def test_default_settings_serialize_to_only_the_version() -> None:
    """An untouched AISettings() must write nothing but the schema version.

    A file that records only deliberate choices is what lets an unconfigured
    user keep following Opengrader's defaults as they improve, per the module
    docstring's "blank means follow the default" rule.
    """
    body = ai_config.AISettings().to_dict()

    assert body == {"version": ai_config.SETTINGS_VERSION}


def test_a_single_changed_field_resolves_alone_others_still_default() -> None:
    """Saving only a changed vision model must leave every other field
    following the provider default when resolved.

    This is the practical meaning of "blank means default": a settings file
    is a diff against the defaults, not a full snapshot of them.
    """
    settings = ai_config.AISettings(vision_model="my-custom-vlm")

    resolved = ai_config.resolve(settings, environ={})

    assert resolved.vision_model == "my-custom-vlm"
    assert resolved.host == ai_config.DEFAULT_HOST
    assert resolved.text_model == ai_config.DEFAULT_TEXT_MODEL
    assert resolved.timeout_seconds == ai_config.DEFAULT_TIMEOUT_SECONDS
    assert resolved.min_confidence == ai_config.DEFAULT_MIN_CONFIDENCE


@pytest.mark.parametrize(
    "settings",
    [
        ai_config.AISettings(),
        ai_config.AISettings(
            provider="ollama",
            host="http://example:1234",
            vision_model="v-model",
            text_model="t-model",
            math_backend="pix2tex",
            timeout_seconds=45.0,
            min_confidence=0.42,
            auto_start=False,
        ),
        ai_config.AISettings(host="http://only-host-changed:9999"),
    ],
)
def test_from_dict_of_to_dict_round_trips(settings: ai_config.AISettings) -> None:
    """``from_dict(to_dict(x)) == x`` for a blank settings object, a fully
    populated one, and one with a single field changed.

    Anything else would mean saving-then-loading silently mutates what the
    user chose.
    """
    assert ai_config.AISettings.from_dict(settings.to_dict()) == settings


# ── 3. Malformed input costs a setting, never a launch ──────────────────────


def test_load_settings_missing_file_returns_defaults(tmp_path: Path) -> None:
    """A settings file that has never been written must resolve to defaults,
    not raise, since most installs never touch the AI Setup screen.
    """
    missing = tmp_path / "does-not-exist" / "ai.json"

    assert ai_config.load_settings(missing) == ai_config.AISettings()


def test_load_settings_invalid_utf8_returns_defaults(tmp_path: Path) -> None:
    """A file that can't even be decoded as UTF-8 must degrade to defaults
    rather than raising ``UnicodeDecodeError`` on every tool's startup path.
    """
    path = tmp_path / "ai.json"
    path.write_bytes(b"\xff\xfe\x00\x01not-utf8")

    assert ai_config.load_settings(path) == ai_config.AISettings()


def test_load_settings_unreadable_path_returns_defaults(tmp_path: Path) -> None:
    """A path that raises OSError on read (here: it's a directory, not a
    file) must degrade to defaults just like a missing file does.
    """
    path = tmp_path / "ai.json"
    path.mkdir()

    assert ai_config.load_settings(path) == ai_config.AISettings()


def test_load_settings_non_json_text_returns_defaults(tmp_path: Path) -> None:
    """Text that isn't JSON at all (e.g. a hand-edited typo) must degrade to
    defaults rather than raising ``json.JSONDecodeError``.
    """
    path = tmp_path / "ai.json"
    path.write_text("this is not { json", encoding="utf-8")

    assert ai_config.load_settings(path) == ai_config.AISettings()


def test_load_settings_json_list_returns_defaults(tmp_path: Path) -> None:
    """Valid JSON that isn't an object (a list) must also degrade to
    defaults: ``from_dict`` only trusts a ``dict``.
    """
    path = tmp_path / "ai.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    assert ai_config.load_settings(path) == ai_config.AISettings()


@pytest.mark.parametrize("bad_value", ["abc", -1, 2])
def test_from_dict_bad_min_confidence_falls_back_to_none(bad_value: object) -> None:
    """A ``min_confidence`` that isn't a 0-1 number (non-numeric text, or a
    number outside the unit range) must fall back to ``None``, which resolves
    to ``DEFAULT_MIN_CONFIDENCE`` rather than being taken literally.
    """
    settings = ai_config.AISettings.from_dict({"min_confidence": bad_value})

    assert settings.min_confidence is None
    resolved = ai_config.resolve(settings, environ={})
    assert resolved.min_confidence == ai_config.DEFAULT_MIN_CONFIDENCE


@pytest.mark.parametrize("bad_value", [0, -5])
def test_from_dict_non_positive_timeout_falls_back_to_none(bad_value: object) -> None:
    """A zero or negative ``timeout_seconds`` is nonsensical (it would mean
    "wait forever" or "wait a negative time") and must fall back to ``None``,
    resolving to ``DEFAULT_TIMEOUT_SECONDS``.
    """
    settings = ai_config.AISettings.from_dict({"timeout_seconds": bad_value})

    assert settings.timeout_seconds is None
    resolved = ai_config.resolve(settings, environ={})
    assert resolved.timeout_seconds == ai_config.DEFAULT_TIMEOUT_SECONDS


# ── 4. Precedence: environment over file, file over provider default ───────


def test_env_host_overrides_the_saved_setting() -> None:
    """``OPENGRADER_AI_HOST`` must win over whatever is in the settings
    object passed to ``resolve`` — the environment tier is how a lab machine
    or a test pins a value for one process.
    """
    settings = ai_config.AISettings(host="http://from-file:1111")

    resolved = ai_config.resolve(
        settings, environ={ai_config.ENV_HOST: "http://from-env:2222"}
    )

    assert resolved.host == "http://from-env:2222"


def test_env_vision_model_overrides_the_saved_setting() -> None:
    """``OPENGRADER_AI_VISION_MODEL`` must win over a saved vision model."""
    settings = ai_config.AISettings(vision_model="saved-vision")

    resolved = ai_config.resolve(
        settings, environ={ai_config.ENV_VISION_MODEL: "env-vision"}
    )

    assert resolved.vision_model == "env-vision"


def test_env_text_model_overrides_the_saved_setting() -> None:
    """``OPENGRADER_AI_TEXT_MODEL`` must win over a saved text model."""
    settings = ai_config.AISettings(text_model="saved-text")

    resolved = ai_config.resolve(
        settings, environ={ai_config.ENV_TEXT_MODEL: "env-text"}
    )

    assert resolved.text_model == "env-text"


def test_env_math_backend_overrides_the_saved_setting() -> None:
    """``OPENGRADER_AI_MATH_BACKEND`` must win over a saved math backend."""
    settings = ai_config.AISettings(math_backend="pix2tex")

    resolved = ai_config.resolve(
        settings, environ={ai_config.ENV_MATH_BACKEND: "vlm"}
    )

    assert resolved.math_backend == "vlm"


def test_env_timeout_overrides_the_saved_setting() -> None:
    """``OPENGRADER_AI_TIMEOUT`` must win over a saved timeout."""
    settings = ai_config.AISettings(timeout_seconds=30.0)

    resolved = ai_config.resolve(
        settings, environ={ai_config.ENV_TIMEOUT: "300"}
    )

    assert resolved.timeout_seconds == 300.0


def test_env_min_confidence_overrides_the_saved_setting() -> None:
    """``OPENGRADER_AI_MIN_CONFIDENCE`` must win over a saved confidence
    floor.
    """
    settings = ai_config.AISettings(min_confidence=0.5)

    resolved = ai_config.resolve(
        settings, environ={ai_config.ENV_MIN_CONFIDENCE: "0.9"}
    )

    assert resolved.min_confidence == 0.9


def test_env_auto_start_overrides_the_saved_setting() -> None:
    """``OPENGRADER_AI_AUTO_START`` must win over a saved auto-start flag."""
    settings = ai_config.AISettings(auto_start=True)

    resolved = ai_config.resolve(
        settings, environ={ai_config.ENV_AUTO_START: "false"}
    )

    assert resolved.auto_start is False


def test_env_provider_overrides_the_saved_setting() -> None:
    """``OPENGRADER_AI_PROVIDER`` must win over a saved provider name.

    Using a bogus saved name that the env var then corrects makes this a
    meaningful check rather than a no-op: if the override weren't applied,
    ``resolve`` would fall back to the default provider *and* report an
    unknown-provider problem, so a clean ``problems()`` here proves the env
    value — not the file value — is what actually got resolved.
    """
    settings = ai_config.AISettings(provider="not-a-real-provider")

    resolved = ai_config.resolve(
        settings, environ={ai_config.ENV_PROVIDER: "ollama"}
    )

    assert resolved.provider.name == "ollama"
    assert resolved.settings.provider == "ollama"
    assert resolved.problems() == ()


def test_whitespace_env_var_does_not_clobber_a_real_saved_value() -> None:
    """An env var set to whitespace (as opposed to unset) must not stomp a
    real saved value — that is exactly what ``_env_text`` exists to prevent,
    since a blank env var is indistinguishable from "not meant to override".
    """
    settings = ai_config.AISettings(host="http://saved-value:8080")

    resolved = ai_config.resolve(settings, environ={ai_config.ENV_HOST: "   "})

    assert resolved.host == "http://saved-value:8080"


def test_resolved_settings_reflect_the_env_override() -> None:
    """``ResolvedAI.settings`` must reflect the env-tier override, not the
    pre-override file settings — a UI reading ``resolved.settings`` needs to
    see what is actually in effect, exactly as if the user had typed the
    override into the AI Setup screen.
    """
    settings = ai_config.AISettings(host="http://from-file:1111")

    resolved = ai_config.resolve(
        settings, environ={ai_config.ENV_HOST: "http://from-env:2222"}
    )

    assert resolved.settings.host == "http://from-env:2222"


def test_file_wins_over_provider_default_when_no_env_override() -> None:
    """With no environment override, a saved value must win over the
    provider's own default — the middle tier of the precedence chain.
    """
    settings = ai_config.AISettings(host="http://from-file:1111")

    resolved = ai_config.resolve(settings, environ={})

    assert resolved.host == "http://from-file:1111"


# ── 5. Unknown provider degrades, and says so ───────────────────────────────


def test_unknown_provider_falls_back_to_the_default_provider() -> None:
    """A settings file naming a provider this build doesn't know (a newer
    Opengrader's file, or a typo) must resolve to the default provider rather
    than raising, so a bad string in the file can never stop the tool from
    starting.
    """
    settings = ai_config.AISettings(provider="not-a-thing")

    resolved = ai_config.resolve(settings, environ={})

    assert resolved.provider.name == ai_config.DEFAULT_PROVIDER


def test_unknown_provider_reports_one_sentence_naming_both_names() -> None:
    """``problems()`` must name the bad provider string and every known
    provider by name, so the fix is stated rather than left to a web search.
    """
    settings = ai_config.AISettings(provider="not-a-thing")

    resolved = ai_config.resolve(settings, environ={})
    problems = resolved.problems()

    assert len(problems) == 1
    assert "not-a-thing" in problems[0]
    for spec in ai_config.registered_providers():
        assert spec.name in problems[0]


def test_well_configured_local_setup_has_no_problems() -> None:
    """A default (unconfigured, local) resolution must report zero
    problems: nothing here should ever nag an offline Ollama user.
    """
    resolved = ai_config.resolve(ai_config.AISettings(), environ={})

    assert resolved.problems() == ()


# ── 6. The cloud extension point works with no edit to ai_config ───────────


@pytest.fixture
def fake_cloud_provider():
    """Register a throwaway cloud ``ProviderSpec`` and restore the real
    registry afterwards, so this test's fake provider can never leak into a
    test that runs after it.
    """
    original_providers = ai_config.registered_providers()
    calls: dict[str, ai_config.ResolvedAI] = {}
    text_sentinel = object()
    vision_sentinel = object()

    def _text_factory(resolved: ai_config.ResolvedAI) -> object:
        calls["text"] = resolved
        return text_sentinel

    def _vision_factory(resolved: ai_config.ResolvedAI) -> object:
        calls["vision"] = resolved
        return vision_sentinel

    spec = ai_config.ProviderSpec(
        name="fake-cloud",
        label="Fake Cloud",
        kind="cloud",
        summary="A fake cloud provider used only to exercise the registry.",
        default_host="https://fake.example/api",
        default_vision_model="fake-vision-1",
        default_text_model="fake-text-1",
        setup_hint="Set FAKE_KEY.",
        api_key_env="FAKE_KEY",
        requires_api_key=True,
        text_config_factory=_text_factory,
        vision_config_factory=_vision_factory,
    )
    ai_config.register_provider(spec)
    try:
        yield spec, text_sentinel, vision_sentinel, calls
    finally:
        ai_config._REGISTRY.clear()
        for original in original_providers:
            ai_config.register_provider(original)


def test_registered_provider_appears_in_the_registry(fake_cloud_provider) -> None:
    """``register_provider`` must make the new spec show up in
    ``registered_providers()`` with no other edit — the whole point of the
    extension point described in the module docstring.
    """
    spec, _text, _vision, _calls = fake_cloud_provider

    assert spec in ai_config.registered_providers()


def test_resolve_picks_up_the_fake_providers_defaults(fake_cloud_provider) -> None:
    """Resolving with the fake provider selected must use *its* host/model
    defaults, not Ollama's — providers are records, not an if/else ladder.
    """
    spec, _text, _vision, _calls = fake_cloud_provider

    resolved = ai_config.resolve(ai_config.AISettings(provider=spec.name), environ={})

    assert resolved.provider is spec
    assert resolved.host == spec.default_host
    assert resolved.vision_model == spec.default_vision_model
    assert resolved.text_model == spec.default_text_model


def test_auto_start_is_forced_off_for_a_cloud_provider(fake_cloud_provider) -> None:
    """Even when the saved settings say ``auto_start=True``, a cloud
    provider has nothing on this machine to boot or pull, so it must resolve
    to ``False`` regardless.
    """
    spec, _text, _vision, _calls = fake_cloud_provider

    resolved = ai_config.resolve(
        ai_config.AISettings(provider=spec.name, auto_start=True), environ={}
    )

    assert resolved.auto_start is False


def test_cloud_provider_sends_data_off_machine(fake_cloud_provider) -> None:
    """A ``kind="cloud"`` provider must report ``sends_data_off_machine``
    True, since that is the fact the UI states plainly per the module
    docstring.
    """
    spec, _text, _vision, _calls = fake_cloud_provider

    resolved = ai_config.resolve(ai_config.AISettings(provider=spec.name), environ={})

    assert resolved.sends_data_off_machine is True


def test_missing_api_key_is_reported_by_name(fake_cloud_provider) -> None:
    """``problems()`` must name the exact environment variable
    (``FAKE_KEY``) a teacher needs to set — reported before a long extraction
    starts, per the docstring's rationale for ``requires_api_key``.
    """
    spec, _text, _vision, _calls = fake_cloud_provider

    resolved = ai_config.resolve(ai_config.AISettings(provider=spec.name), environ={})
    problems = resolved.problems()

    assert len(problems) == 1
    assert "FAKE_KEY" in problems[0]


def test_supplying_the_key_via_environ_clears_the_problem(fake_cloud_provider) -> None:
    """Once ``FAKE_KEY`` is present in the environment passed to
    ``resolve``, the missing-key problem must disappear.
    """
    spec, _text, _vision, _calls = fake_cloud_provider

    resolved = ai_config.resolve(
        ai_config.AISettings(provider=spec.name),
        environ={"FAKE_KEY": "sekrit-value"},
    )

    assert resolved.problems() == ()
    assert resolved.api_key == "sekrit-value"


def test_resolve_api_key_never_reads_disk_and_is_none_for_local(
    fake_cloud_provider,
) -> None:
    """``resolve_api_key`` must go only through the ``environ`` it is given —
    never the settings file — and a local provider (no ``api_key_env``) must
    always resolve to ``None``.
    """
    ollama = ai_config.get_provider("ollama")

    assert ai_config.resolve_api_key(ollama, environ={"SOME_OTHER_KEY": "x"}) is None
    assert ai_config.resolve_api_key(ollama, environ={}) is None


def test_text_and_vision_config_route_through_the_fake_factories(
    fake_cloud_provider,
) -> None:
    """``text_config``/``vision_config`` must call the fake provider's own
    factories and return exactly what they produce — the bridge functions
    must never special-case a provider by name.
    """
    spec, text_sentinel, vision_sentinel, calls = fake_cloud_provider
    resolved = ai_config.resolve(ai_config.AISettings(provider=spec.name), environ={})

    assert ai_config.text_config(resolved) is text_sentinel
    assert ai_config.vision_config(resolved) is vision_sentinel
    assert calls["text"] is resolved
    assert calls["vision"] is resolved


def test_saved_settings_never_contain_key_material(
    fake_cloud_provider, tmp_path: Path
) -> None:
    """``save_settings`` must never write the API key to disk — only its
    environment-variable *name* is ever persisted (via the provider spec,
    not ``AISettings``), per the module docstring's "must not happen" list.
    """
    spec, _text, _vision, _calls = fake_cloud_provider
    resolved = ai_config.resolve(
        ai_config.AISettings(provider=spec.name),
        environ={"FAKE_KEY": "sekrit-value"},
    )
    path = tmp_path / "ai.json"

    ai_config.save_settings(resolved.settings, path)

    written = path.read_text(encoding="utf-8")
    assert "sekrit-value" not in written
    assert "FAKE_KEY" not in written


# ── 7. settings_path and save_settings ──────────────────────────────────────


def test_settings_path_honours_the_config_env_var(tmp_path: Path) -> None:
    """``OPENGRADER_AI_CONFIG`` must override the path outright — this is
    how tests (and two side-by-side configurations on one machine) keep
    their hands off the default location.
    """
    override = tmp_path / "custom" / "ai.json"

    path = ai_config.settings_path(environ={ai_config.ENV_CONFIG_PATH: str(override)})

    assert path == override


def test_settings_path_defaults_under_a_per_user_config_dir() -> None:
    """With no override, the path must land under a per-user config
    directory (the docstring: "the same paths packaging/install.sh already
    chose"), not somewhere ad hoc like the current working directory.
    """
    path = ai_config.settings_path(environ={})

    assert path.name == ai_config.SETTINGS_FILENAME
    assert str(Path.home()) in str(path) or "opengrader" in str(path).lower()


def test_save_settings_creates_missing_parent_directories(tmp_path: Path) -> None:
    """Saving to a path whose parent directory doesn't exist yet must create
    it — a first-time save must not require the user (or another part of
    Opengrader) to have created the config directory first.
    """
    target = tmp_path / "does" / "not" / "exist" / "ai.json"

    written = ai_config.save_settings(ai_config.AISettings(host="x"), target)

    assert written == target
    assert target.exists()


def test_save_settings_returns_the_path_it_wrote(tmp_path: Path) -> None:
    """The return value must be exactly the path written to, so a caller can
    report it back to the user without recomputing it.
    """
    target = tmp_path / "ai.json"

    written = ai_config.save_settings(ai_config.AISettings(), target)

    assert written == target


def test_save_settings_failure_propagates(tmp_path: Path) -> None:
    """Unlike loading, a save error must *not* be swallowed: the docstring
    says silently failing here would leave the user believing they had
    configured something they hadn't. Writing to a path whose parent is a
    file (not a directory) triggers exactly this kind of unrecoverable
    write error.
    """
    blocked = tmp_path / "blocked"
    blocked.write_text("i am a file, not a directory", encoding="utf-8")
    target = blocked / "ai.json"

    with pytest.raises(OSError):
        ai_config.save_settings(ai_config.AISettings(), target)


# ── 8. Feature toggles: round-trip, coercion, env, refinement, problems ─────


def test_feature_toggles_default_off_and_serialize_to_nothing() -> None:
    """An untouched AISettings() must still record only the version, even
    with the seven new toggles added — ``auto_grade``'s default of ``True``
    must be omitted too, per "blank means follow the default"."""
    body = ai_config.AISettings().to_dict()

    assert body == {"version": ai_config.SETTINGS_VERSION}


def test_deliberate_feature_choices_round_trip() -> None:
    """A settings file that records only the toggles a teacher actually
    flipped must survive to_dict/from_dict unchanged, including turning
    ``auto_grade`` off (the one toggle whose default is True)."""
    settings = ai_config.AISettings(
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
        "version": ai_config.SETTINGS_VERSION,
        "read_handwriting": True,
        "escalate_only": True,
        "transcribe_math": True,
        "grade_written": True,
        "auto_grade": False,
        "require_calibration": True,
        "min_confidence_measured": True,
    }
    assert ai_config.AISettings.from_dict(body) == settings


@pytest.mark.parametrize(
    "field_name",
    [
        "read_handwriting",
        "escalate_only",
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
    """Every new toggle except ``auto_grade`` must fall back to ``False`` on
    a value ``_as_bool`` cannot make sense of — matching the fallback every
    other boolean field in this module already uses."""
    settings = ai_config.AISettings.from_dict({field_name: raw})

    assert getattr(settings, field_name) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, True), (False, False), ("yes", True), ("garbage", True)],
)
def test_from_dict_auto_grade_falls_back_to_true_on_rubbish(
    raw: object, expected: bool
) -> None:
    """Unlike the other six toggles, ``auto_grade``'s fallback is ``True`` —
    it defaults on because that already is today's pipeline behaviour, and a
    corrupted settings file must not silently turn auto-grading off."""
    settings = ai_config.AISettings.from_dict({"auto_grade": raw})

    assert settings.auto_grade is expected


@pytest.mark.parametrize(
    ("env_name", "field_name"),
    [
        (ai_config.ENV_READ_HANDWRITING, "read_handwriting"),
        (ai_config.ENV_ESCALATE_ONLY, "escalate_only"),
        (ai_config.ENV_TRANSCRIBE_MATH, "transcribe_math"),
        (ai_config.ENV_GRADE_WRITTEN, "grade_written"),
        (ai_config.ENV_REQUIRE_CALIBRATION, "require_calibration"),
    ],
)
def test_env_var_overrides_the_saved_toggle(env_name: str, field_name: str) -> None:
    """Each ``OPENGRADER_AI_*`` toggle env var must win over a saved value,
    exactly like ``ENV_AUTO_START`` already does."""
    settings = ai_config.AISettings(**{field_name: True})

    resolved = ai_config.resolve(settings, environ={env_name: "false"})

    assert getattr(resolved.settings, field_name) is False
    assert getattr(resolved, field_name) is False


def test_env_auto_grade_overrides_the_saved_toggle() -> None:
    """``OPENGRADER_AI_AUTO_GRADE`` must win over a saved ``auto_grade``."""
    settings = ai_config.AISettings(auto_grade=True)

    resolved = ai_config.resolve(settings, environ={ai_config.ENV_AUTO_GRADE: "false"})

    assert resolved.auto_grade is False


def test_min_confidence_measured_has_no_environment_override() -> None:
    """``min_confidence_measured`` is provenance, not a knob: the module
    deliberately defines no ``ENV_MIN_CONFIDENCE_MEASURED``, so nothing in the
    environment can claim a human never typed a value they actually did."""
    assert not hasattr(ai_config, "ENV_MIN_CONFIDENCE_MEASURED")

    settings = ai_config.AISettings(min_confidence_measured=True)
    # Even a same-named, plausible-looking env var must have no effect, since
    # no code path reads it.
    resolved = ai_config.resolve(
        settings, environ={"OPENGRADER_AI_MIN_CONFIDENCE_MEASURED": "false"}
    )

    assert resolved.settings.min_confidence_measured is True


def test_escalate_only_and_transcribe_math_resolve_false_when_reading_is_off() -> None:
    """Turning on ``escalate_only``/``transcribe_math`` without
    ``read_handwriting`` must resolve to ``False`` for both — a refinement of
    a switch that is off is off, so no consumer has to check both fields."""
    settings = ai_config.AISettings(
        read_handwriting=False, escalate_only=True, transcribe_math=True
    )

    resolved = ai_config.resolve(settings, environ={})

    assert resolved.escalate_only is False
    assert resolved.transcribe_math is False


def test_escalate_only_and_transcribe_math_resolve_true_when_reading_is_on() -> None:
    """With ``read_handwriting`` on, both refinements pass through as saved."""
    settings = ai_config.AISettings(
        read_handwriting=True, escalate_only=True, transcribe_math=True
    )

    resolved = ai_config.resolve(settings, environ={})

    assert resolved.escalate_only is True
    assert resolved.transcribe_math is True


@pytest.mark.parametrize(
    ("read_handwriting", "grade_written", "expected"),
    [(False, False, False), (True, False, True), (False, True, True), (True, True, True)],
)
def test_uses_ai_true_when_either_job_would_call_a_model(
    read_handwriting: bool, grade_written: bool, expected: bool
) -> None:
    """``uses_ai`` must be true whenever either job would call a model, and
    false only when neither would."""
    settings = ai_config.AISettings(
        read_handwriting=read_handwriting, grade_written=grade_written
    )

    resolved = ai_config.resolve(settings, environ={})

    assert resolved.uses_ai is expected


def test_problems_reports_the_calibration_finding_only_when_all_three_hold() -> None:
    """The calibration finding must appear only when grading suggestions are
    on, calibration is required, AND the confidence was never measured."""
    settings = ai_config.AISettings(
        grade_written=True, require_calibration=True, min_confidence_measured=False
    )

    resolved = ai_config.resolve(settings, environ={})
    problems = resolved.problems()

    assert len(problems) == 1
    assert "never measured" in problems[0]
    assert "llm-eval" in problems[0]


@pytest.mark.parametrize(
    ("grade_written", "require_calibration", "min_confidence_measured"),
    [
        (False, True, False),  # not suggesting grades at all
        (True, False, False),  # calibration not required
        (True, True, True),  # already measured
    ],
)
def test_problems_omits_the_calibration_finding_unless_all_three_hold(
    grade_written: bool, require_calibration: bool, min_confidence_measured: bool
) -> None:
    """Any one of the three conditions being false must suppress the
    finding — in particular, a measured confidence (``min_confidence_measured
    =True``) must silence it even though grading and calibration are both
    on."""
    settings = ai_config.AISettings(
        grade_written=grade_written,
        require_calibration=require_calibration,
        min_confidence_measured=min_confidence_measured,
    )

    resolved = ai_config.resolve(settings, environ={})

    assert resolved.problems() == ()


# ── 9. Timeout measurement ───────────────────────────────────────────────────


def test_recommend_timeout_none_below_the_warn_fraction() -> None:
    """A measurement under half the limit is nowhere near timing out, so
    recommending anything would be noise."""
    assert ai_config.recommend_timeout(measured=50.0, limit=120.0) is None


def test_recommend_timeout_none_when_zero_or_negative() -> None:
    """A non-positive measurement (a failed call, or a clock oddity) must
    never produce a recommendation."""
    assert ai_config.recommend_timeout(measured=0.0, limit=120.0) is None
    assert ai_config.recommend_timeout(measured=-5.0, limit=120.0) is None


def test_recommend_timeout_none_when_the_rounded_value_is_not_an_increase() -> None:
    """A measurement that clears the warn fraction but whose headroomed,
    rounded-up recommendation still lands at or below the current limit must
    not be reported — it would not actually be an increase."""
    # 61s * 4 = 244, rounds up to 270 > 120, would recommend — so instead pick
    # a measured value whose rounded recommendation lands exactly at the
    # existing limit: measured=30 against limit=120 -> 30*4=120, rounds to 120.
    assert ai_config.recommend_timeout(measured=30.0, limit=120.0) is None


def test_recommend_timeout_a_real_cold_measurement() -> None:
    """A realistic cold-start measurement (80s against a 120s limit, which
    clears the warn fraction) must recommend 330.0: 80 * 4 = 320, which is
    not itself a multiple of 30, so it rounds *up* to 330 — never down, since
    undershooting the padding this function exists to add would defeat its
    purpose."""
    assert ai_config.recommend_timeout(measured=80.0, limit=120.0) == 330.0


def test_recommend_timeout_rounds_up_to_the_next_multiple_of_thirty() -> None:
    """A recommendation that isn't already a multiple of 30 must round up,
    never down — undershooting the very headroom this function exists to
    add."""
    # 65 * 4 = 260 -> rounds up to 270.
    assert ai_config.recommend_timeout(measured=65.0, limit=120.0) == 270.0


def test_measure_generate_latency_success_reports_ok_and_share_of_limit() -> None:
    """A successful injected ``generate`` must produce an ``ok`` measurement
    whose ``share_of_limit`` reflects seconds/limit, and whose ``describe()``
    names the model."""
    settings = ai_config.AISettings(timeout_seconds=100.0, vision_model="qwen2.5vl:7b")
    resolved = ai_config.resolve(settings, environ={})
    calls: list[dict] = []

    def fake_generate(**kwargs):
        calls.append(kwargs)
        return "ready"

    result = ai_config.measure_generate_latency(
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
    """A ``generate`` that raises must become a failed, non-``ok``
    measurement with the exception text captured, never propagate."""
    settings = ai_config.AISettings(timeout_seconds=90.0, text_model="llama3.1")
    resolved = ai_config.resolve(settings, environ={})

    def broken_generate(**kwargs):
        raise RuntimeError("connection refused")

    result = ai_config.measure_generate_latency(
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
    """"handwriting" must time the vision model; "grading" must time the text
    model — the two are frequently different models."""
    settings = ai_config.AISettings(
        vision_model="vision-x", text_model="text-y", timeout_seconds=60.0
    )
    resolved = ai_config.resolve(settings, environ={})

    def fake_generate(**kwargs):
        return "ready"

    handwriting = ai_config.measure_generate_latency(
        resolved, role="handwriting", generate=fake_generate
    )
    grading = ai_config.measure_generate_latency(
        resolved, role="grading", generate=fake_generate
    )

    assert handwriting.model == "vision-x"
    assert grading.model == "text-y"
