from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from textual.app import App
from textual.widgets import Button, Checkbox, Input, RichLog, Select, Static

from opengrader.core import app_config
from opengrader.core.app_config import AISettings, ProviderSpec, local_ai
from opengrader.tui.ui.screens import ai_settings_screen
from opengrader.tui.ui.screens.ai_settings_screen import AISettingsScreen

_SIZE = (120, 44)


class _Host(App):
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
    for _ in range(limit):
        await pilot.pause()
        await asyncio.sleep(0.01)
        if predicate():
            return


def _log_text(screen: AISettingsScreen) -> str:
    return " ".join(str(line) for line in screen.query_one("#ai-log", RichLog).lines)


@pytest.fixture
def fake_cloud_provider():
    spec = ProviderSpec(
        name="fake-cloud",
        label="Fake Cloud (test)",
        summary="A hosted test provider used only by this test file.",
        default_host="https://api.fake-cloud.example/v1",
        default_vision_model="fake-vision-1",
        default_text_model="fake-text-1",
        setup_hint="Set FAKE_CLOUD_API_KEY, then re-open AI setup.",
        api_key_env="FAKE_CLOUD_API_KEY",
        requires_api_key=True,
    )
    app_config.register_provider(spec)
    try:
        yield spec
    finally:
        app_config._REGISTRY.pop(spec.name, None)


def test_cancel_writes_nothing_and_dismisses_with_none() -> None:
    async def run() -> tuple[bool, AISettings | None]:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.screen.query_one("#ai-vision-model", Input).value = "typed-not-saved"
            await pilot.pause()
            await pilot.click("#ai-cancel")
            await pilot.pause()
        return app.dismissed, app.result

    dismissed, result = asyncio.run(run())
    assert dismissed is True
    assert result is None
    assert not app_config.settings_path().exists()


def test_save_persists_and_returns_the_settings() -> None:
    async def run() -> AISettings | None:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#ai-vision-model", Input).value = "llama-vision:9b"
            # "box" isn't a recognized local address, so this host resolves
            # as cloud and Save must pass the consent modal before it writes
            # anything — see test_an_unrecognized_host_needs_consent_at_save.
            screen.query_one("#ai-host", Input).value = "http://box:11434"
            await pilot.pause()
            await pilot.click("#ai-save")
            await _settle(pilot, lambda: app.dismissed)
            if not app.dismissed:
                await pilot.click("#consent-accept")
                await _settle(pilot, lambda: app.dismissed)
        return app.result

    result = asyncio.run(run())
    assert isinstance(result, AISettings)
    assert result.vision_model == "llama-vision:9b"
    assert result.host == "http://box:11434"

    reloaded = app_config.load_settings()
    assert reloaded.vision_model == "llama-vision:9b"
    assert reloaded.host == "http://box:11434"


def test_saving_with_nothing_typed_records_only_the_version() -> None:
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
    assert vision_ph == app_config.DEFAULT_VISION_MODEL
    assert text_ph == app_config.DEFAULT_TEXT_MODEL
    assert host_ph == app_config.DEFAULT_HOST

    raw = json.loads(app_config.settings_path().read_text(encoding="utf-8"))
    assert raw == {
        "$schema": "./config.schema.json",
        "version": app_config.SETTINGS_VERSION,
    }


def test_existing_settings_prefill_the_form_and_resaving_is_a_no_op() -> None:
    original = AISettings(
        vision_model="my-vision",
        text_model="my-text",
        host="http://h:1",
        min_confidence=0.55,
        auto_start=False,
    )
    path = app_config.save_settings(original)
    before = path.read_text(encoding="utf-8")
    before_keys = set(json.loads(before))
    assert before_keys == {
        "$schema",
        "version",
        "vision_model",
        "text_model",
        "host",
        "min_confidence",
        "auto_start",
    }

    async def run() -> tuple[str, str, str, str, bool]:
        app = _Host(AISettingsScreen())
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
            await _settle(pilot, lambda: app.dismissed)
            if not app.dismissed:
                # "h" isn't a recognized local address, so this host resolves
                # as cloud; with no consent on file yet (it was written
                # directly by save_settings() above, bypassing the screen),
                # Save must pass the modal before it can go through.
                await pilot.click("#consent-accept")
                await _settle(pilot, lambda: app.dismissed)
        return values

    values = asyncio.run(run())
    assert values == ("my-vision", "my-text", "http://h:1", "0.55", False)

    after = json.loads(path.read_text(encoding="utf-8"))
    consents = after.pop("consents", None)
    assert after == json.loads(before)
    assert consents and consents[0]["kind"] == "cloud"


def test_connection_test_resolves_the_typed_values_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    assert not app_config.settings_path().exists()


def test_a_failed_connection_test_shows_the_providers_setup_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    hint = app_config.get_provider(app_config.DEFAULT_PROVIDER).setup_hint
    for model in (app_config.DEFAULT_VISION_MODEL, app_config.DEFAULT_TEXT_MODEL):
        assert model in hint
        assert model in text


def test_a_registered_cloud_provider_appears_and_reveals_the_key_row(
    fake_cloud_provider: ProviderSpec,
) -> None:
    async def run() -> tuple[bool, bool, str]:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            hidden_before = screen.query_one("#ai-key-row").has_class("hidden")

            select = screen.query_one("#ai-provider", Select)
            select.value = fake_cloud_provider.name
            await pilot.pause()

            summary = str(screen.query_one("#ai-provider-summary", Static).content)
            hidden_after = screen.query_one("#ai-key-row").has_class("hidden")
        return hidden_before, hidden_after, summary

    hidden_before, hidden_after, summary = asyncio.run(run())
    assert hidden_before is True
    assert hidden_after is False
    assert fake_cloud_provider.summary in summary


def test_an_api_key_is_never_written_to_the_settings_file(
    fake_cloud_provider: ProviderSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(fake_cloud_provider.api_key_env, "sk-super-secret-value")

    async def run() -> None:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#ai-provider", Select).value = fake_cloud_provider.name
            await pilot.pause()
            # Choosing a cloud provider opens the consent modal before the
            # choice sticks.
            await pilot.click("#consent-accept")
            await pilot.pause()
            await pilot.click("#ai-save")
            await pilot.pause()

    asyncio.run(run())
    raw_text = app_config.settings_path().read_text(encoding="utf-8")
    assert "sk-super-secret-value" not in raw_text
    raw = json.loads(raw_text)
    assert "api_key" not in raw
    assert raw["provider"] == fake_cloud_provider.name


def test_an_out_of_range_confidence_is_refused_with_a_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    assert not app_config.settings_path().exists()


def test_a_non_numeric_confidence_is_refused_with_a_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    assert not app_config.settings_path().exists()


def test_a_valid_confidence_is_accepted_and_round_trips() -> None:
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
    assert app_config.load_settings().min_confidence == pytest.approx(0.42)


def test_timeout_prefills_from_settings_and_round_trips() -> None:
    app_config.save_settings(AISettings(timeout_seconds=300.0))

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
    assert app_config.load_settings().timeout_seconds == pytest.approx(300.0)


def test_blank_timeout_saves_none() -> None:
    app_config.save_settings(AISettings(timeout_seconds=300.0))

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
    assert app_config.load_settings().timeout_seconds is None


@pytest.mark.parametrize("bad", ["not-a-number", "0", "-5"])
def test_an_invalid_timeout_is_refused_with_a_message(
    bad: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_config.save_settings(AISettings(timeout_seconds=300.0))
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
    assert app_config.load_settings().timeout_seconds == pytest.approx(300.0)


def test_new_ai_switches_round_trip_through_collect() -> None:
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

    reloaded = app_config.load_settings()
    assert reloaded.read_handwriting is True
    assert reloaded.escalate_only is True
    assert reloaded.transcribe_math is True
    assert reloaded.grade_written is True
    assert reloaded.require_calibration is True
    assert reloaded.auto_grade is False


def test_auto_grade_defaults_on() -> None:
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
    app_config.save_settings(AISettings(read_handwriting=True, grade_written=True))

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


def test_min_confidence_measured_is_preserved_when_the_box_is_untouched() -> None:
    app_config.save_settings(
        AISettings(min_confidence=0.81, min_confidence_measured=True)
    )

    async def run() -> AISettings | None:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.screen.query_one("#ai-vision-model", Input).value = "other-model"
            await pilot.pause()
            await pilot.click("#ai-save")
            await pilot.pause()
        return app.result

    result = asyncio.run(run())
    assert result is not None
    assert result.min_confidence == pytest.approx(0.81)
    assert result.min_confidence_measured is True
    assert app_config.load_settings().min_confidence_measured is True


def test_min_confidence_measured_is_cleared_when_the_value_is_edited() -> None:
    app_config.save_settings(
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
    assert app_config.load_settings().min_confidence_measured is False


def test_connection_test_times_a_call_and_offers_to_apply_a_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_list_models(config):  # noqa: ANN001
        return [app_config.DEFAULT_VISION_MODEL, app_config.DEFAULT_TEXT_MODEL]

    monkeypatch.setattr("opengrader.core.llm.list_models", fake_list_models)

    def fake_measure(resolved, *, role="handwriting", **kwargs):  # noqa: ANN001
        return app_config.LatencyMeasurement(
            role=role,
            model=resolved.vision_model,
            limit=resolved.timeout_seconds,
            seconds=100.0,
        )

    monkeypatch.setattr(app_config, "measure_generate_latency", fake_measure)

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
    assert timeout_value == "420"


def test_apply_button_stays_hidden_and_disabled_with_no_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_list_models(config):  # noqa: ANN001
        return [app_config.DEFAULT_VISION_MODEL, app_config.DEFAULT_TEXT_MODEL]

    monkeypatch.setattr("opengrader.core.llm.list_models", fake_list_models)

    def fake_measure(resolved, *, role="handwriting", **kwargs):  # noqa: ANN001
        return app_config.LatencyMeasurement(
            role=role,
            model=resolved.vision_model,
            limit=resolved.timeout_seconds,
            seconds=1.0,
        )

    monkeypatch.setattr(app_config, "measure_generate_latency", fake_measure)

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


# ---------------------------------------------------------------------------
# Set up local AI
# ---------------------------------------------------------------------------


def _status(missing: tuple[str, ...], wanted: tuple[str, ...]) -> "local_ai.LocalAIStatus":
    return local_ai.LocalAIStatus(
        host=app_config.DEFAULT_HOST,
        wanted=wanted,
        installed=tuple(model for model in wanted if model not in missing),
        missing=missing,
    )


# Both jobs ticked, so both models are in play. A fresh install has only
# "read names and IDs locally" on, which needs the handwriting model alone.
_BOTH_JOBS_ON = AISettings(read_handwriting=True, grade_written=True)


def _prepare_label(screen: AISettingsScreen) -> str:
    return str(screen.query_one("#ai-prepare", Button).label)


def _plain_log(screen: AISettingsScreen) -> str:
    # The log wraps, so a sentence is rarely on one line: join the lines'
    # text and collapse the whitespace before looking for one.
    lines = screen.query_one("#ai-log", RichLog).lines
    return " ".join(" ".join(line.text for line in lines).split())


async def _press_prepare(pilot, screen: AISettingsScreen) -> None:  # noqa: ANN001
    # A button ignores a click for a moment after the last one (the pressed
    # look is still showing). A stand-in that answers instantly gets a test
    # to the second press sooner than any person could.
    button = screen.query_one("#ai-prepare", Button)
    await _settle(pilot, lambda: not button.has_class("-active"))
    await pilot.click("#ai-prepare")


def test_set_up_local_ai_asks_before_it_downloads_then_downloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    release = threading.Event()

    def fake_prepare(host, models, *, emit, on_progress, cancel, install, download):  # noqa: ANN001
        calls.append(
            {"host": host, "models": models, "install": install, "download": download}
        )
        if not download:
            return _status(missing=models, wanted=models)
        emit(f"Downloading {models[0]}…")
        on_progress(
            local_ai.PullProgress(
                models[0], "pulling", completed=3_000_000_000, total=6_000_000_000
            )
        )
        release.wait(timeout=10)
        return _status(missing=(), wanted=models)

    monkeypatch.setattr(local_ai, "prepare", fake_prepare)

    async def run() -> dict:
        seen: dict = {}
        app = _Host(AISettingsScreen(_BOTH_JOBS_ON))
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#ai-text-model", Input).value = "typed-grader:1"
            await pilot.pause()

            await _press_prepare(pilot, screen)
            await _settle(pilot, lambda: _prepare_label(screen) == "Download models")
            seen["offer_label"] = _prepare_label(screen)
            seen["offer_text"] = _plain_log(screen)
            seen["save_enabled_at_offer"] = not screen.query_one("#ai-save", Button).disabled

            await _press_prepare(pilot, screen)
            progress = screen.query_one("#ai-prepare-status", Static)
            await _settle(pilot, lambda: bool(progress.display))
            seen["progress"] = str(progress.render())
            seen["running_label"] = _prepare_label(screen)
            seen["save_disabled_while_running"] = screen.query_one("#ai-save", Button).disabled
            release.set()
            await _settle(pilot, lambda: _prepare_label(screen) == "Set up local AI")
            seen["done_text"] = _plain_log(screen)
            seen["progress_hidden"] = not progress.display
        return seen

    seen = asyncio.run(run())
    vision = app_config.DEFAULT_VISION_MODEL

    # The first press only looks; the second, labelled press is the consent.
    assert [(call["install"], call["download"]) for call in calls] == [
        (False, False),
        (True, True),
    ]
    # What is fetched is what the form says: a typed name, and the
    # recommended model where the field is blank.
    assert calls[0]["models"] == (vision, "typed-grader:1")
    assert calls[0]["host"] == app_config.DEFAULT_HOST
    assert seen["offer_label"] == "Download models"
    assert f"{vision} (handwriting)" in seen["offer_text"]
    assert "typed-grader:1 (grading)" in seen["offer_text"]
    assert seen["save_enabled_at_offer"] is True
    assert seen["progress"] == f"{vision} — 50% · 3.0 GB of 6.0 GB"
    assert seen["running_label"] == "Stop"
    assert seen["save_disabled_while_running"] is True
    assert "The local AI is ready" in seen["done_text"]
    assert seen["progress_hidden"] is True
    # Like Test connection, it works from the form and saves nothing.
    assert not app_config.settings_path().exists()


def test_set_up_local_ai_says_ready_at_once_when_nothing_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    def fake_prepare(host, models, *, emit, on_progress, cancel, install, download):  # noqa: ANN001
        calls.append(download)
        emit(f"The AI server is answering at {host}.")
        return _status(missing=(), wanted=models)

    monkeypatch.setattr(local_ai, "prepare", fake_prepare)

    async def run() -> tuple[str, str]:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _press_prepare(pilot, screen)
            await _settle(pilot, lambda: "ready" in _plain_log(screen))
            return _plain_log(screen), _prepare_label(screen)

    text, label = asyncio.run(run())
    assert calls == [False]
    assert "The AI server is answering" in text
    assert "The local AI is ready" in text
    assert label == "Set up local AI"


def test_set_up_local_ai_can_be_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_prepare(host, models, *, emit, on_progress, cancel, install, download):  # noqa: ANN001
        if not download:
            return _status(missing=models, wanted=models)
        deadline = time.monotonic() + 10
        while not cancel.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        cancel.check()
        raise AssertionError("was never asked to stop")

    monkeypatch.setattr(local_ai, "prepare", fake_prepare)

    async def run() -> tuple[str, str, bool]:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _press_prepare(pilot, screen)
            await _settle(pilot, lambda: _prepare_label(screen) == "Download models")
            await _press_prepare(pilot, screen)
            await _settle(pilot, lambda: _prepare_label(screen) == "Stop")
            await _press_prepare(pilot, screen)
            await _settle(pilot, lambda: _prepare_label(screen) == "Set up local AI")
            return (
                _plain_log(screen),
                _prepare_label(screen),
                screen.query_one("#ai-save", Button).disabled,
            )

    text, label, save_disabled = asyncio.run(run())
    assert "Stopped." in text
    assert "is kept" in text
    assert label == "Set up local AI"
    assert save_disabled is False


@pytest.mark.parametrize(
    ("brew", "label", "expected"),
    [
        ("/stand-in/brew", "Install and download", "press Install and download"),
        (None, "Set up local AI", local_ai.OLLAMA_DOWNLOAD_URL),
    ],
)
def test_set_up_local_ai_offers_to_install_ollama_only_where_homebrew_can(
    monkeypatch: pytest.MonkeyPatch, brew: str | None, label: str, expected: str
) -> None:
    calls: list[bool] = []

    def fake_prepare(host, models, *, emit, on_progress, cancel, install, download):  # noqa: ANN001
        calls.append(install)
        if install:
            return _status(missing=(), wanted=models)
        raise local_ai.OllamaNotInstalled()

    monkeypatch.setattr(local_ai, "prepare", fake_prepare)
    monkeypatch.setattr(ai_settings_screen, "find_homebrew", lambda: brew)

    async def run() -> tuple[str, str, str]:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _press_prepare(pilot, screen)
            await _settle(pilot, lambda: "not installed" in _plain_log(screen))
            first_text, first_label = _plain_log(screen), _prepare_label(screen)
            await _press_prepare(pilot, screen)
            await _settle(pilot, lambda: len(calls) == 2)
            await _settle(
                pilot, lambda: not screen.query_one("#ai-save", Button).disabled
            )
            return first_text, first_label, _plain_log(screen)

    first_text, first_label, second_text = asyncio.run(run())
    assert "Ollama, the program that runs the AI" in first_text
    assert expected in first_text
    assert first_label == label
    # With Homebrew the second press installs; without it there is nothing to
    # confirm, so a second press is simply another look.
    assert calls == [False, bool(brew)]
    assert ("The local AI is ready" in second_text) is bool(brew)


def test_set_up_local_ai_reports_a_failure_in_the_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_prepare(host, models, **_kwargs):  # noqa: ANN001
        raise local_ai.LocalAIError("Ollama did not answer within 30 s.")

    monkeypatch.setattr(local_ai, "prepare", fake_prepare)

    async def run() -> tuple[str, bool]:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _press_prepare(pilot, screen)
            await _settle(pilot, lambda: "did not answer" in _plain_log(screen))
            return _plain_log(screen), screen.query_one("#ai-test", Button).disabled

    text, test_disabled = asyncio.run(run())
    assert "Ollama did not answer within 30 s." in text
    assert test_disabled is False


def test_set_up_local_ai_is_not_offered_for_a_cloud_provider(
    fake_cloud_provider: ProviderSpec,
) -> None:
    async def run() -> tuple[bool, bool]:
        local = _Host(AISettingsScreen())
        async with local.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            shown_for_local = bool(local.screen.query_one("#ai-prepare", Button).display)
        cloud = _Host(AISettingsScreen(AISettings(provider=fake_cloud_provider.name)))
        async with cloud.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            shown_for_cloud = bool(cloud.screen.query_one("#ai-prepare", Button).display)
        return shown_for_local, shown_for_cloud

    shown_for_local, shown_for_cloud = asyncio.run(run())
    assert shown_for_local is True
    assert shown_for_cloud is False


def test_closing_ai_setup_stops_a_download_in_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped = threading.Event()

    def fake_prepare(host, models, *, emit, on_progress, cancel, install, download):  # noqa: ANN001
        if not download:
            return _status(missing=models, wanted=models)
        deadline = time.monotonic() + 10
        while not cancel.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        if cancel.is_set():
            stopped.set()
        cancel.check()

    monkeypatch.setattr(local_ai, "prepare", fake_prepare)

    async def run() -> None:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _press_prepare(pilot, screen)
            await _settle(pilot, lambda: _prepare_label(screen) == "Download models")
            await _press_prepare(pilot, screen)
            await _settle(pilot, lambda: _prepare_label(screen) == "Stop")
            await pilot.press("escape")
            await _settle(pilot, stopped.is_set)

    asyncio.run(run())
    assert stopped.is_set()


def test_set_up_local_ai_fetches_only_the_models_the_ticked_switches_need(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[tuple[str, ...]] = []

    def fake_prepare(host, models, **_kwargs):  # noqa: ANN001
        asked.append(models)
        return _status(missing=models, wanted=models)

    monkeypatch.setattr(local_ai, "prepare", fake_prepare)

    async def run() -> list[str]:
        logs: list[str] = []
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen

            # As installed: only names and IDs are read locally.
            await _press_prepare(pilot, screen)
            await _settle(pilot, lambda: len(asked) == 1 and "Not downloaded" in _plain_log(screen))
            logs.append(_plain_log(screen))

            # Ticked but not saved still counts: the form is what is read.
            screen.query_one("#ai-grade-written", Checkbox).value = True
            await pilot.pause()
            await _press_prepare(pilot, screen)
            await _settle(pilot, lambda: len(asked) == 2 and "Not downloaded" in _plain_log(screen))
            logs.append(_plain_log(screen))

            # Grading only.
            screen.query_one("#ai-read-identity-locally", Checkbox).value = False
            await pilot.pause()
            await _press_prepare(pilot, screen)
            await _settle(pilot, lambda: len(asked) == 3 and "Not downloaded" in _plain_log(screen))
            logs.append(_plain_log(screen))
        return logs

    logs = asyncio.run(run())
    vision, text = app_config.DEFAULT_VISION_MODEL, app_config.DEFAULT_TEXT_MODEL
    assert asked == [(vision,), (vision, text), (text,)]
    assert f"Leaving out the grading model ({text})" in logs[0]
    assert "Leaving out" not in logs[1]
    assert f"Leaving out the handwriting model ({vision})" in logs[2]
    # A changed selection is a new question: no press here was taken as the
    # go-ahead for a download offered for a different set of models.
    assert all("Not downloaded yet" in log for log in logs)


def test_set_up_local_ai_downloads_nothing_when_no_switch_needs_a_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_ai, "prepare", lambda *a, **k: pytest.fail("started the local AI")
    )

    async def run() -> tuple[str, bool, str]:
        settings = AISettings(read_identity_locally=False)
        app = _Host(AISettingsScreen(settings))
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _press_prepare(pilot, screen)
            await _settle(pilot, lambda: "nothing to download" in _plain_log(screen))
            return (
                _plain_log(screen),
                screen.query_one("#ai-save", Button).disabled,
                _prepare_label(screen),
            )

    text, save_disabled, label = asyncio.run(run())
    assert "nothing to download" in text
    assert "Leaving out the handwriting model" in text
    assert "Leaving out the grading model" in text
    assert save_disabled is False
    assert label == "Set up local AI"


def test_one_model_named_for_both_jobs_is_fetched_when_either_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[tuple[str, ...]] = []

    def fake_prepare(host, models, **_kwargs):  # noqa: ANN001
        asked.append(models)
        return _status(missing=(), wanted=models)

    monkeypatch.setattr(local_ai, "prepare", fake_prepare)

    async def run() -> str:
        settings = AISettings(vision_model="shared:1", text_model="shared:1")
        app = _Host(AISettingsScreen(settings))
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _press_prepare(pilot, screen)
            await _settle(pilot, lambda: "ready" in _plain_log(screen))
            return _plain_log(screen)

    text = asyncio.run(run())
    assert asked == [("shared:1",)]
    assert "Leaving out" not in text


def test_start_the_ai_automatically_is_the_first_switch() -> None:
    async def run() -> str | None:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            return app.screen.query(Checkbox).first().id

    assert asyncio.run(run()) == "ai-auto-start"
