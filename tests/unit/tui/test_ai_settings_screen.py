"""Tests for the AI Setup modal (AISettingsScreen).

This is the one place every CLI's model/host/confidence defaults come from
(``core/ai_config``), so what matters here is not the widgets but the three
rules the screen's own module docstring names: nothing is written until Save,
blank stays blank on disk (never freezing today's default into the file), and
a failed connection test says what to do about it. A fourth rule — the
provider dropdown is built from the registry, not a hard-coded branch — is
what lets a cloud provider show up here with zero edits to this file.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from textual.app import App
from textual.widgets import Button, Checkbox, Input, RichLog, Select, Static

from opengrader.core import ai_config
from opengrader.core.ai_config import AISettings, ProviderSpec
from opengrader.tui.ui.screens.ai_settings_screen import AISettingsScreen

_SIZE = (120, 44)


class _Host(App):
    """Hosts the modal and records what it dismissed with."""

    def __init__(self, screen: AISettingsScreen) -> None:
        super().__init__()
        self._screen = screen
        self.result: AISettings | None = None
        self.dismissed = False

    def on_mount(self) -> None:
        def _record(value: AISettings | None) -> None:
            self.result = value
            self.dismissed = True

        self.push_screen(self._screen, _record)


async def _settle(pilot, predicate, limit: int = 80) -> None:  # noqa: ANN001
    """Pump the event loop until a thread worker's result lands, or give up."""
    for _ in range(limit):
        await pilot.pause()
        await asyncio.sleep(0.01)
        if predicate():
            return


def _log_text(screen: AISettingsScreen) -> str:
    """The connection-test log, rejoined with spaces so a phrase split across
    two wrapped RichLog lines is still one contiguous string to search."""
    return " ".join(str(line) for line in screen.query_one("#ai-log", RichLog).lines)


@pytest.fixture
def fake_cloud_provider():
    """Register a throwaway cloud provider and remove it again afterward.

    Proves the screen reads ``ai_config.registered_providers()`` rather than
    hard-coding "ollama": if this test needed an edit to the screen to pass,
    the registry claim in the module docstring would be false.
    """
    spec = ProviderSpec(
        name="fake-cloud",
        label="Fake Cloud (test)",
        kind="cloud",
        summary="A hosted test provider used only by this test file.",
        default_host="https://api.fake-cloud.example/v1",
        default_vision_model="fake-vision-1",
        default_text_model="fake-text-1",
        setup_hint="Set FAKE_CLOUD_API_KEY, then re-open AI setup.",
        api_key_env="FAKE_CLOUD_API_KEY",
        requires_api_key=True,
    )
    ai_config.register_provider(spec)
    try:
        yield spec
    finally:
        ai_config._REGISTRY.pop(spec.name, None)


# ── Nothing is written until Save ────────────────────────────────────────────


def test_cancel_writes_nothing_and_dismisses_with_none() -> None:
    """Backing out of the dialog must not commit a half-typed model name.

    A user trying a model name, deciding against it, and hitting Cancel (or
    Escape) has to get their working setup back exactly as it was — including
    "no file at all" for someone who never configured the AI.
    """

    async def run() -> None:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.screen.query_one("#ai-vision-model", Input).value = "typed-not-saved"
            await pilot.pause()
            await pilot.click("#ai-cancel")
            await pilot.pause()

    asyncio.run(run())
    assert not ai_config.settings_path().exists()


def test_cancel_dismisses_with_none() -> None:
    """The caller uses the dismiss value to know whether to refresh anything
    showing a model name; a cancel must hand back nothing to refresh with."""

    async def run() -> tuple[bool, AISettings | None]:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await pilot.click("#ai-cancel")
            await pilot.pause()
        return app.dismissed, app.result

    dismissed, result = asyncio.run(run())
    assert dismissed is True
    assert result is None


# ── Save persists and returns the AISettings ─────────────────────────────────


def test_save_persists_and_returns_the_settings() -> None:
    """The whole point of the screen: what Save returns is what load_settings()
    reads back, because every CLI resolves its defaults from that same file."""

    async def run() -> AISettings | None:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#ai-vision-model", Input).value = "llama-vision:9b"
            screen.query_one("#ai-host", Input).value = "http://box:11434"
            await pilot.pause()
            await pilot.click("#ai-save")
            await pilot.pause()
        return app.result

    result = asyncio.run(run())
    assert isinstance(result, AISettings)
    assert result.vision_model == "llama-vision:9b"
    assert result.host == "http://box:11434"

    reloaded = ai_config.load_settings()
    assert reloaded.vision_model == "llama-vision:9b"
    assert reloaded.host == "http://box:11434"


# ── Blank fields stay blank on disk ──────────────────────────────────────────


def test_saving_with_nothing_typed_records_only_the_version() -> None:
    """"Blank means follow the default" is load-bearing (see core/ai_config's
    docstring): pre-filling today's recommendation into the file would freeze
    an unconfigured user onto it forever instead of letting Opengrader's
    recommendation move them forward. An untouched form must write nothing
    but the schema version."""

    async def run() -> tuple[str, str, str]:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            placeholders = (
                screen.query_one("#ai-vision-model", Input).placeholder,
                screen.query_one("#ai-text-model", Input).placeholder,
                screen.query_one("#ai-host", Input).placeholder,
            )
            await pilot.click("#ai-save")
            await pilot.pause()
        return placeholders

    vision_ph, text_ph, host_ph = asyncio.run(run())
    # The placeholder names exactly what leaving the field blank resolves to.
    assert vision_ph == ai_config.DEFAULT_VISION_MODEL
    assert text_ph == ai_config.DEFAULT_TEXT_MODEL
    assert host_ph == ai_config.DEFAULT_HOST

    raw = json.loads(ai_config.settings_path().read_text(encoding="utf-8"))
    assert raw == {"version": ai_config.SETTINGS_VERSION}


# ── An existing file pre-populates the form ──────────────────────────────────


def test_existing_settings_prefill_the_form_and_resaving_is_a_no_op() -> None:
    """Re-opening the screen must show the user's own choices — and only
    those — so that closing it again without changing anything rewrites the
    file byte-for-byte instead of freezing in whatever else was blank."""
    original = AISettings(
        vision_model="my-vision",
        text_model="my-text",
        host="http://h:1",
        min_confidence=0.55,
        auto_start=False,
    )
    path = ai_config.save_settings(original)
    before = path.read_text(encoding="utf-8")
    before_keys = set(json.loads(before))
    assert before_keys == {
        "version",
        "vision_model",
        "text_model",
        "host",
        "min_confidence",
        "auto_start",
    }

    async def run() -> tuple[str, str, str, str, bool]:
        app = _Host(AISettingsScreen())  # loads from disk, settings=None
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            values = (
                screen.query_one("#ai-vision-model", Input).value,
                screen.query_one("#ai-text-model", Input).value,
                screen.query_one("#ai-host", Input).value,
                screen.query_one("#ai-min-confidence", Input).value,
                screen.query_one("#ai-auto-start", Checkbox).value,
            )
            await pilot.click("#ai-save")
            await pilot.pause()
        return values

    values = asyncio.run(run())
    assert values == ("my-vision", "my-text", "http://h:1", "0.55", False)

    after = path.read_text(encoding="utf-8")
    assert after == before


# ── Test connection touches the typed values, not the saved ones ────────────


def test_connection_test_resolves_the_typed_values_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Testing a connection must reflect what's in the boxes right now, not
    whatever is (or isn't) saved — otherwise trying an unsaved model name
    would either silently test the old one or force a save just to find out
    whether it works, defeating the "nothing written until Save" rule."""
    captured: list = []

    def fake_list_models(config):  # noqa: ANN001
        captured.append(config)
        return ["typed-text-model"]

    monkeypatch.setattr("opengrader.core.llm.list_models", fake_list_models)

    async def run() -> None:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#ai-text-model", Input).value = "typed-text-model"
            screen.query_one("#ai-host", Input).value = "http://typed-host:1"
            await pilot.pause()
            await pilot.click("#ai-test")
            await _settle(
                pilot, lambda: not screen.query_one("#ai-test", Button).disabled
            )

    asyncio.run(run())
    assert captured, "list_models was never called"
    assert captured[0].model == "typed-text-model"
    assert captured[0].host == "http://typed-host:1"
    assert not ai_config.settings_path().exists()


def test_a_failed_connection_test_shows_the_providers_setup_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"The test says what to do, not just that it failed": a failure must
    print the provider's own setup_hint (the fix), the same rule
    core/environment.py follows for every other finding."""

    def boom(_config):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("opengrader.core.llm.list_models", boom)

    async def run() -> str:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.click("#ai-test")
            await _settle(
                pilot, lambda: not screen.query_one("#ai-test", Button).disabled
            )
            return _log_text(screen)

    text = asyncio.run(run())
    assert "connection refused" in text
    hint = ai_config.get_provider(ai_config.DEFAULT_PROVIDER).setup_hint
    assert "ollama pull qwen2.5vl:7b" in hint  # sanity on the fixture text itself
    assert "ollama pull qwen2.5vl:7b" in text


# ── The provider dropdown is built from the registry ─────────────────────────


def test_a_registered_cloud_provider_appears_and_reveals_the_key_row(
    fake_cloud_provider: ProviderSpec,
) -> None:
    """The dropdown, the summary line and the key row's visibility are all
    driven by ``registered_providers()``/``requires_api_key`` — this is the
    test that proves a cloud provider needs no edit to this screen at all."""

    async def run() -> tuple[bool, bool, str]:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            hidden_before = screen.query_one("#ai-key-row").has_class("hidden")

            select = screen.query_one("#ai-provider", Select)
            select.value = fake_cloud_provider.name  # raises if not a real option
            await pilot.pause()

            summary = str(screen.query_one("#ai-provider-summary", Static).content)
            hidden_after = screen.query_one("#ai-key-row").has_class("hidden")
        return hidden_before, hidden_after, summary

    hidden_before, hidden_after, summary = asyncio.run(run())
    assert hidden_before is True  # local Ollama needs no key
    assert hidden_after is False  # the fake cloud provider does
    assert fake_cloud_provider.summary in summary


# ── An API key is never stored ───────────────────────────────────────────────


def test_an_api_key_is_never_written_to_the_settings_file(
    fake_cloud_provider: ProviderSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    """core/ai_config's contract: only the *name* of the environment variable
    is ever recorded, never the key itself — so a settings file is safe to
    hand to a TA or back up without leaking a secret."""
    monkeypatch.setenv(fake_cloud_provider.api_key_env, "sk-super-secret-value")

    async def run() -> None:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#ai-provider", Select).value = fake_cloud_provider.name
            await pilot.pause()
            await pilot.click("#ai-save")
            await pilot.pause()

    asyncio.run(run())
    raw_text = ai_config.settings_path().read_text(encoding="utf-8")
    assert "sk-super-secret-value" not in raw_text
    raw = json.loads(raw_text)
    assert "api_key" not in raw
    assert raw["provider"] == fake_cloud_provider.name


# ── Validation is friendly, not fatal ────────────────────────────────────────


def test_an_out_of_range_confidence_is_refused_with_a_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A confidence outside 0-1 must be refused with a visible notification —
    not saved as-is, and not left to crash resolve() the next time any CLI
    reads the file."""
    notices: list[tuple[str, str]] = []

    async def run() -> bool:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            monkeypatch.setattr(
                app,
                "notify",
                lambda message, **kwargs: notices.append(
                    (str(message), kwargs.get("severity", "information"))
                ),
            )
            app.screen.query_one("#ai-min-confidence", Input).value = "5"
            await pilot.pause()
            await pilot.click("#ai-save")
            await pilot.pause()
        return app.dismissed

    dismissed = asyncio.run(run())
    assert dismissed is False
    assert notices, "an out-of-range confidence must notify, not save silently"
    assert notices[-1][1] == "error"
    assert not ai_config.settings_path().exists()


def test_a_non_numeric_confidence_is_refused_with_a_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same rule for text that isn't a number at all: refused, not a crash."""
    notices: list[tuple[str, str]] = []

    async def run() -> bool:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            monkeypatch.setattr(
                app,
                "notify",
                lambda message, **kwargs: notices.append(
                    (str(message), kwargs.get("severity", "information"))
                ),
            )
            app.screen.query_one("#ai-min-confidence", Input).value = "not-a-number"
            await pilot.pause()
            await pilot.click("#ai-save")
            await pilot.pause()
        return app.dismissed

    dismissed = asyncio.run(run())
    assert dismissed is False
    assert notices, "a non-numeric confidence must notify, not crash"
    assert notices[-1][1] == "error"
    assert not ai_config.settings_path().exists()


def test_a_valid_confidence_is_accepted_and_round_trips() -> None:
    """The counterpart to the two refusals above: a legal value in range must
    actually be saved, or the validation would be indistinguishable from a
    screen that rejects everything."""

    async def run() -> AISettings | None:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.screen.query_one("#ai-min-confidence", Input).value = "0.42"
            await pilot.pause()
            await pilot.click("#ai-save")
            await pilot.pause()
        return app.result

    result = asyncio.run(run())
    assert isinstance(result, AISettings)
    assert result.min_confidence == pytest.approx(0.42)
    assert ai_config.load_settings().min_confidence == pytest.approx(0.42)


# ── The timeout field: a hole this screen used to leave open ────────────────
#
# timeout_seconds used to have no widget at all: collect() passed
# self._settings.timeout_seconds straight through, so a value on disk was
# invisible and permanent, and the only way to change it from the TUI was an
# environment variable or a hand-edited ai.json. These tests are the
# regression coverage for that hole being closed.


def test_timeout_prefills_from_settings_and_round_trips() -> None:
    """A timeout already on disk (set by hand or via OPENGRADER_AI_TIMEOUT)
    must be visible in the form, mirroring exactly how #ai-min-confidence
    renders its own optional float."""
    ai_config.save_settings(AISettings(timeout_seconds=300.0))

    async def run() -> tuple[str, AISettings | None]:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            shown = screen.query_one("#ai-timeout", Input).value
            await pilot.click("#ai-save")
            await pilot.pause()
        return shown, app.result

    shown, result = asyncio.run(run())
    assert shown == "300"
    assert result is not None
    assert result.timeout_seconds == pytest.approx(300.0)
    assert ai_config.load_settings().timeout_seconds == pytest.approx(300.0)


def test_blank_timeout_saves_none() -> None:
    """Blank means "use the recommended default", the same rule every other
    field on this screen follows — clearing the box must not merely fail to
    change anything, it must actually erase a previously saved value."""
    ai_config.save_settings(AISettings(timeout_seconds=300.0))

    async def run() -> AISettings | None:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#ai-timeout", Input).value = ""
            await pilot.pause()
            await pilot.click("#ai-save")
            await pilot.pause()
        return app.result

    result = asyncio.run(run())
    assert result is not None
    assert result.timeout_seconds is None
    assert ai_config.load_settings().timeout_seconds is None


@pytest.mark.parametrize("bad", ["not-a-number", "0", "-5"])
def test_an_invalid_timeout_is_refused_with_a_message(
    bad: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-numeric, zero and negative must all be refused with a visible
    notification, exactly like the confidence field. This is the regression
    test for the hole itself: a saved timeout must be a live, validated field
    a bad edit can't silently leave un-saved-but-unreachable — the file must
    keep the last *good* value, not accept garbage and not vanish it.
    """
    ai_config.save_settings(AISettings(timeout_seconds=300.0))
    notices: list[tuple[str, str]] = []

    async def run() -> bool:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            monkeypatch.setattr(
                app,
                "notify",
                lambda message, **kwargs: notices.append(
                    (str(message), kwargs.get("severity", "information"))
                ),
            )
            app.screen.query_one("#ai-timeout", Input).value = bad
            await pilot.pause()
            await pilot.click("#ai-save")
            await pilot.pause()
        return app.dismissed

    dismissed = asyncio.run(run())
    assert dismissed is False
    assert notices, "an invalid timeout must notify, not save silently"
    assert notices[-1][1] == "error"
    assert ai_config.load_settings().timeout_seconds == pytest.approx(300.0)


# ── "What the AI does here" switches ─────────────────────────────────────────


def test_new_ai_switches_round_trip_through_collect() -> None:
    """Every switch this screen adds (the replacement for the six checkboxes
    deleted from the setup screen) must reach the saved AISettings — this is
    what makes AI Setup "the one place the AI is switched on and off"."""

    async def run() -> AISettings | None:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#ai-read-handwriting", Checkbox).value = True
            await pilot.pause()
            screen.query_one("#ai-escalate-only", Checkbox).value = True
            screen.query_one("#ai-transcribe-math", Checkbox).value = True
            screen.query_one("#ai-grade-written", Checkbox).value = True
            await pilot.pause()
            screen.query_one("#ai-require-calibration", Checkbox).value = True
            screen.query_one("#ai-auto-grade", Checkbox).value = False
            await pilot.pause()
            await pilot.click("#ai-save")
            await pilot.pause()
        return app.result

    result = asyncio.run(run())
    assert result is not None
    assert result.read_handwriting is True
    assert result.escalate_only is True
    assert result.transcribe_math is True
    assert result.grade_written is True
    assert result.require_calibration is True
    assert result.auto_grade is False

    reloaded = ai_config.load_settings()
    assert reloaded.read_handwriting is True
    assert reloaded.escalate_only is True
    assert reloaded.transcribe_math is True
    assert reloaded.grade_written is True
    assert reloaded.require_calibration is True
    assert reloaded.auto_grade is False


def test_auto_grade_defaults_on() -> None:
    """AISettings.auto_grade defaults True (today's opengrader-run behaviour);
    an untouched form must save that, not silently flip it off just because
    the checkbox now exists to be unticked."""

    async def run() -> AISettings | None:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await pilot.click("#ai-save")
            await pilot.pause()
        return app.result

    result = asyncio.run(run())
    assert result is not None
    assert result.auto_grade is True


def test_handwriting_suboptions_hidden_until_ticked() -> None:
    """#ai-handwriting-options starts hidden and is revealed by its parent
    checkbox, mirroring setup_screen's #ext-parallel-options idiom."""

    async def run() -> tuple[bool, bool]:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            hidden_before = screen.query_one(
                "#ai-handwriting-options"
            ).has_class("hidden")
            screen.query_one("#ai-read-handwriting", Checkbox).value = True
            await pilot.pause()
            hidden_after = screen.query_one(
                "#ai-handwriting-options"
            ).has_class("hidden")
        return hidden_before, hidden_after

    hidden_before, hidden_after = asyncio.run(run())
    assert hidden_before is True
    assert hidden_after is False


def test_grading_suboptions_hidden_until_ticked() -> None:
    """Same idiom, gated on #ai-grade-written instead — "require a measured
    confidence" is meaningless until grade suggestions are on at all."""

    async def run() -> tuple[bool, bool]:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            hidden_before = screen.query_one("#ai-grading-options").has_class(
                "hidden"
            )
            screen.query_one("#ai-grade-written", Checkbox).value = True
            await pilot.pause()
            hidden_after = screen.query_one("#ai-grading-options").has_class(
                "hidden"
            )
        return hidden_before, hidden_after

    hidden_before, hidden_after = asyncio.run(run())
    assert hidden_before is True
    assert hidden_after is False


def test_suboptions_shown_at_compose_when_already_saved_on() -> None:
    """The reveal must also happen at compose time from saved settings, not
    only in reaction to a click — otherwise re-opening the screen on a
    machine that already has these switched on would hide options that are
    actually in effect."""
    ai_config.save_settings(AISettings(read_handwriting=True, grade_written=True))

    async def run() -> tuple[bool, bool]:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            return (
                screen.query_one("#ai-handwriting-options").has_class("hidden"),
                screen.query_one("#ai-grading-options").has_class("hidden"),
            )

    handwriting_hidden, grading_hidden = asyncio.run(run())
    assert handwriting_hidden is False
    assert grading_hidden is False


# ── min_confidence_measured: provenance, not a knob ──────────────────────────


def test_min_confidence_measured_is_preserved_when_the_box_is_untouched() -> None:
    """Accepting llm-eval's recommendation (min_confidence_measured=True) and
    then saving the form again — without touching the confidence box — must
    not silently downgrade the value back to "a human typed this"."""
    ai_config.save_settings(
        AISettings(min_confidence=0.81, min_confidence_measured=True)
    )

    async def run() -> AISettings | None:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            # Touch an unrelated field so Save has something to do, without
            # touching the confidence box itself.
            app.screen.query_one("#ai-vision-model", Input).value = "other-model"
            await pilot.pause()
            await pilot.click("#ai-save")
            await pilot.pause()
        return app.result

    result = asyncio.run(run())
    assert result is not None
    assert result.min_confidence == pytest.approx(0.81)
    assert result.min_confidence_measured is True
    assert ai_config.load_settings().min_confidence_measured is True


def test_min_confidence_measured_is_cleared_when_the_value_is_edited() -> None:
    """The whole reason "Only use suggestions I have measured" can mean
    anything: typing a new number turns the confidence back into a guess, so
    the provenance flag must drop to False even though the field itself still
    saves fine."""
    ai_config.save_settings(
        AISettings(min_confidence=0.81, min_confidence_measured=True)
    )

    async def run() -> AISettings | None:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.screen.query_one("#ai-min-confidence", Input).value = "0.9"
            await pilot.pause()
            await pilot.click("#ai-save")
            await pilot.pause()
        return app.result

    result = asyncio.run(run())
    assert result is not None
    assert result.min_confidence == pytest.approx(0.9)
    assert result.min_confidence_measured is False
    assert ai_config.load_settings().min_confidence_measured is False


# ── Test connection also times a real generate call ──────────────────────────


def test_connection_test_times_a_call_and_offers_to_apply_a_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test connection must not just say "connected" — it must time one real
    generate call (so the timeout box is measured rather than guessed, the
    same reason llm-eval exists so nobody guesses --llm-min-confidence) and,
    when the measurement eats most of the configured limit, offer a button
    that fills in a roomier one. Nothing here touches a network:
    list_models and measure_generate_latency are both faked.
    """

    def fake_list_models(config):  # noqa: ANN001
        return [ai_config.DEFAULT_VISION_MODEL, ai_config.DEFAULT_TEXT_MODEL]

    monkeypatch.setattr("opengrader.core.llm.list_models", fake_list_models)

    def fake_measure(resolved, *, role="handwriting", **kwargs):  # noqa: ANN001
        # 100s against the 120s default limit is most of it, so
        # recommend_timeout should recommend something roomier.
        return ai_config.LatencyMeasurement(
            role=role,
            model=resolved.vision_model,
            limit=resolved.timeout_seconds,
            seconds=100.0,
        )

    monkeypatch.setattr(ai_config, "measure_generate_latency", fake_measure)

    async def run() -> tuple[str, bool, str]:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.click("#ai-test")
            await _settle(
                pilot, lambda: not screen.query_one("#ai-test", Button).disabled
            )
            log_text = _log_text(screen)
            apply_hidden = screen.query_one("#ai-apply-timeout").has_class(
                "hidden"
            )
            await pilot.click("#ai-apply-timeout")
            await pilot.pause()
            timeout_value = screen.query_one("#ai-timeout", Input).value
        return log_text, apply_hidden, timeout_value

    log_text, apply_hidden, timeout_value = asyncio.run(run())
    assert "responded in 100.0 s" in log_text
    assert "That is most of your limit" in log_text
    assert apply_hidden is False
    # 100s measured, 4x headroom = 400s, rounded up to the next multiple of
    # 30s = 420s (see ai_config.recommend_timeout / TIMEOUT_HEADROOM_MULTIPLE).
    assert timeout_value == "420"


def test_apply_button_stays_hidden_and_disabled_with_no_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the measured call is nowhere near the limit, recommend_timeout
    returns None and the apply button must neither appear nor be clickable —
    _show()'s blanket "re-enable every button" must not leak onto it."""

    def fake_list_models(config):  # noqa: ANN001
        return [ai_config.DEFAULT_VISION_MODEL, ai_config.DEFAULT_TEXT_MODEL]

    monkeypatch.setattr("opengrader.core.llm.list_models", fake_list_models)

    def fake_measure(resolved, *, role="handwriting", **kwargs):  # noqa: ANN001
        return ai_config.LatencyMeasurement(
            role=role,
            model=resolved.vision_model,
            limit=resolved.timeout_seconds,
            seconds=1.0,
        )

    monkeypatch.setattr(ai_config, "measure_generate_latency", fake_measure)

    async def run() -> tuple[bool, bool]:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.click("#ai-test")
            await _settle(
                pilot, lambda: not screen.query_one("#ai-test", Button).disabled
            )
            button = screen.query_one("#ai-apply-timeout", Button)
            return button.has_class("hidden"), button.disabled

    hidden, disabled = asyncio.run(run())
    assert hidden is True
    assert disabled is True
