from __future__ import annotations

import asyncio

import pytest
from textual.app import App
from textual.widgets import (
    Button,
    Input,
    RadioButton,
    RadioSet,
    Select,
    Static,
    TextArea,
)

from opengrader.core import app_config
from opengrader.core.app_config import keystore
from opengrader.core.app_config import setup as ai_setup
from opengrader.tui.ui.screens.provider_screen import ProviderScreen
from opengrader.tui.ui.screens.provider_setup_screen import (
    STEP_CHOOSE,
    STEP_DONE,
    STEP_KEY,
    STEP_OTHER,
    ProviderSetupScreen,
)
from opengrader.tui import clipboard

_SIZE = (130, 50)
_UNSET = object()


class _Host(App):
    def __init__(self, screen) -> None:  # noqa: ANN001
        super().__init__()
        self._screen = screen
        self.result = _UNSET
        self.opened: list[str] = []

    def on_mount(self) -> None:
        def _record(value) -> None:  # noqa: ANN001
            self.result = value

        self.push_screen(self._screen, _record)

    def open_url(self, url: str, *, new_tab: bool = True) -> None:
        self.opened.append(url)


async def _settle(pilot, predicate, limit: int = 200) -> None:  # noqa: ANN001
    for _ in range(limit):
        await pilot.pause()
        await asyncio.sleep(0.01)
        if predicate():
            return
    raise AssertionError("the screen never reached the expected state")


def _text(screen, selector: str) -> str:  # noqa: ANN001
    return str(screen.query_one(selector, Static).render())


async def _press(pilot, screen, button_id: str) -> None:  # noqa: ANN001
    screen.query_one(f"#{button_id}", Button).press()
    await pilot.pause()


async def _pick(pilot, screen: ProviderSetupScreen, name: str) -> None:  # noqa: ANN001
    index = [choice.name for choice in screen._choices].index(name)
    screen.query_one(f"#setup-choice-{index}", RadioButton).value = True
    await pilot.pause()


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app_config.reload_manifests()
    try:
        yield
    finally:
        for path in app_config.manifests_dir().glob("*.toml"):
            path.unlink()
        app_config.reload_manifests()


@pytest.fixture
def service(monkeypatch):
    """A stand-in for the service: accepts one key, lists three models."""

    class Service:
        good_key = "sk-good-0001"
        models = ("gpt-4o", "gpt-4o-mini", "o-small")
        probes: list[str | None] = []

    def probe(name: str) -> ai_setup.ProbeResult:
        key = app_config.for_provider(name).api_key
        Service.probes.append(key)
        if key == Service.good_key:
            return ai_setup.ProbeResult(ok=True, lines=("answered in 0.4 s",))
        return ai_setup.ProbeResult(
            ok=False,
            lines=("The service did not accept the key.", "HTTP 401"),
            kind="key-rejected",
        )

    Service.probes = []
    monkeypatch.setattr(ai_setup, "probe_provider", probe)
    monkeypatch.setattr(ai_setup, "list_models", lambda name: (Service.models, ""))
    return Service


def test_the_list_marks_bedrock_and_vertex_advanced_and_starts_on_a_simple_one() -> None:
    async def run() -> tuple[list[str], int, str]:
        app = _Host(ProviderSetupScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            labels = [str(button.label) for button in screen.query(RadioButton)]
            pressed = screen.query_one("#setup-choices", RadioSet).pressed_index
            return labels, pressed, _text(screen, "#setup-progress")

    labels, pressed, progress = asyncio.run(run())

    assert "AWS Bedrock (advanced)" in labels
    assert "Google Vertex AI (advanced)" in labels
    assert "OpenAI" in labels and "Anthropic" in labels
    assert not labels[pressed].endswith("(advanced)")
    assert labels.index("OpenAI") < labels.index("AWS Bedrock (advanced)")
    assert progress.startswith("Step 1 of 3")


def test_pick_paste_test_finish_adds_the_provider_and_keeps_the_key_out_of_files(
    service,
) -> None:
    async def run():  # noqa: ANN202
        app = _Host(ProviderSetupScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _pick(pilot, screen, "openai")
            await _press(pilot, screen, "setup-primary")
            assert screen._step == STEP_KEY
            intro = _text(screen, "#setup-key-intro")
            note = _text(screen, "#setup-key-store-note")

            await _press(pilot, screen, "setup-open-page")

            box = screen.query_one("#setup-key-input", Input)
            assert box.password
            box.value = service.good_key
            await _press(pilot, screen, "setup-primary")
            await _settle(pilot, lambda: screen._step == STEP_DONE)
            # The pasted key does not linger in the widget.
            assert box.value == ""
            await _settle(
                pilot, lambda: screen.query_one("#setup-models").display is True
            )
            result = _text(screen, "#setup-result")
            vision = screen.query_one("#setup-vision-model", Select)
            assert vision.value == "gpt-4o-mini"
            vision.value = "gpt-4o"
            await pilot.pause()
            await _press(pilot, screen, "setup-primary")
            await _settle(pilot, lambda: app.result is not _UNSET)
        return intro, note, result, app.opened, app.result

    intro, note, result, opened, dismissed = asyncio.run(run())

    assert "https://platform.openai.com/api-keys" in intro
    # People expect their chat subscription to cover this; it does not.
    assert "ChatGPT Plus or Pro" in intro and "Claude Pro or Max" in intro
    assert "api.openai.com" in intro and "no student work" in intro
    assert "never in an Opengrader file" in note
    assert opened == ["https://platform.openai.com/api-keys"]
    assert "connected" in result
    assert dismissed == "openai"

    spec = app_config.get_provider("openai")
    assert spec.name == "openai"
    assert spec.default_vision_model == "gpt-4o"
    assert spec.default_text_model == "gpt-4o-mini"
    assert keystore.lookup(keystore.account_for(spec)) == service.good_key
    assert app_config.for_provider("openai").api_key == service.good_key
    # Nothing Opengrader wrote holds the key.
    for path in app_config.manifests_dir().parent.rglob("*"):
        if path.is_file():
            assert service.good_key not in path.read_text(encoding="utf-8", errors="replace")
    # The provider was added, not switched to: that needs AI setup's consent.
    assert app_config.load_settings().provider in ("", app_config.DEFAULT_PROVIDER)


def test_a_rejected_key_stays_on_the_key_step_and_a_second_try_replaces_it(
    service,
) -> None:
    async def run():  # noqa: ANN202
        app = _Host(ProviderSetupScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _pick(pilot, screen, "openai")
            await _press(pilot, screen, "setup-primary")
            box = screen.query_one("#setup-key-input", Input)

            box.value = "sk-typo"
            await _press(pilot, screen, "setup-primary")
            await _settle(pilot, lambda: len(service.probes) == 1 and not screen._busy)
            first = (screen._step, _text(screen, "#setup-status"))

            box.value = service.good_key
            await _press(pilot, screen, "setup-primary")
            await _settle(pilot, lambda: screen._step == STEP_DONE)
        return first

    step, status = asyncio.run(run())

    assert step == STEP_KEY
    assert "did not accept the key" in status
    assert service.probes == ["sk-typo", service.good_key]


def test_an_empty_box_with_no_key_anywhere_asks_for_one_and_sends_nothing(service) -> None:
    async def run() -> tuple[str, str]:
        app = _Host(ProviderSetupScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _pick(pilot, screen, "openai")
            await _press(pilot, screen, "setup-primary")
            await _press(pilot, screen, "setup-primary")
            return screen._step, _text(screen, "#setup-status")

    step, status = asyncio.run(run())

    assert step == STEP_KEY and "Paste the key" in status
    assert service.probes == []


def test_back_then_continue_does_not_trip_over_the_file_it_just_wrote(service) -> None:
    async def run() -> tuple[str, str]:
        app = _Host(ProviderSetupScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _pick(pilot, screen, "openai")
            await _press(pilot, screen, "setup-primary")
            await _press(pilot, screen, "setup-back")
            assert screen._step == STEP_CHOOSE
            await _press(pilot, screen, "setup-primary")
            return screen._step, _text(screen, "#setup-status")

    step, status = asyncio.run(run())

    assert step == STEP_KEY and status == ""


def test_cancelling_after_the_file_was_written_still_reports_the_provider(service) -> None:
    async def run():  # noqa: ANN202
        app = _Host(ProviderSetupScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _pick(pilot, screen, "anthropic")
            await _press(pilot, screen, "setup-primary")
            await _press(pilot, screen, "setup-cancel")
            await _settle(pilot, lambda: app.result is not _UNSET)
        return app.result

    assert asyncio.run(run()) == "anthropic"
    assert (app_config.manifests_dir() / "anthropic.toml").is_file()


def test_cancelling_at_the_first_step_changes_nothing(service) -> None:
    async def run():  # noqa: ANN202
        app = _Host(ProviderSetupScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await _settle(pilot, lambda: app.result is not _UNSET)
        return app.result

    assert asyncio.run(run()) is None
    assert list(app_config.manifests_dir().glob("*.toml")) == []


def test_an_advanced_service_skips_the_key_and_says_how_to_sign_in(service, pydantic_ai_available) -> None:
    async def run() -> tuple[str, str, str, bool]:
        app = _Host(ProviderSetupScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _pick(pilot, screen, "bedrock")
            detail = _text(screen, "#setup-choice-detail")
            await _press(pilot, screen, "setup-primary")
            return (
                screen._step,
                detail,
                _text(screen, "#setup-result"),
                bool(screen.query_one("#setup-models").display),
            )

    step, detail, result, models_shown = asyncio.run(run())

    assert "Advanced" in detail and "cloud account" in detail
    assert step == STEP_DONE
    assert "aws configure" in result
    assert not models_shown
    assert service.probes == []


def test_a_key_in_the_environment_can_be_tested_with_the_box_left_empty(
    service, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", service.good_key)

    async def run() -> tuple[str, str]:
        app = _Host(ProviderSetupScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _pick(pilot, screen, "openai")
            await _press(pilot, screen, "setup-primary")
            note = _text(screen, "#setup-key-store-note")
            await _press(pilot, screen, "setup-primary")
            await _settle(pilot, lambda: screen._step == STEP_DONE)
            return note, screen._step

    note, step = asyncio.run(run())

    assert "already set in the OPENAI_API_KEY" in note
    assert step == STEP_DONE
    # Nothing was pasted, so nothing was saved.
    spec = app_config.get_provider("openai")
    assert keystore.lookup(keystore.account_for(spec)) is None


def test_with_no_key_store_the_box_is_off_and_the_variable_is_named(service) -> None:
    keystore.set_backend(keystore.MemoryBackend(reason="No desktop keyring is running."))

    async def run() -> tuple[bool, str]:
        app = _Host(ProviderSetupScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _pick(pilot, screen, "openai")
            await _press(pilot, screen, "setup-primary")
            return (
                screen.query_one("#setup-key-input", Input).disabled,
                _text(screen, "#setup-key-store-note"),
            )

    disabled, note = asyncio.run(run())

    assert disabled
    assert "No desktop keyring is running." in note and "OPENAI_API_KEY" in note


def test_the_cloud_kill_switch_blocks_the_first_step(service, monkeypatch) -> None:
    monkeypatch.setenv(app_config.ALLOW_CLOUD_ENV, "0")

    async def run() -> tuple[bool, str]:
        app = _Host(ProviderSetupScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            return (
                screen.query_one("#setup-primary", Button).disabled,
                _text(screen, "#setup-status"),
            )

    disabled, status = asyncio.run(run())

    assert disabled and "turned off" in status


# ------------------------------------------------- a service not in the list

REPLY = """\
Here you go:

```toml
name         = "sjsu-gateway"
label        = "SJSU AI Gateway"
dialect      = "openai-chat"
host         = "https://ai.sjsu-gateway.net/v1"
vision_model = "llava-13b"
text_model   = "llama-3-70b"
api_key_env  = "SJSU_AI_KEY"
setup_hint   = "Ask the College of Engineering help desk for a key."
```
"""


async def _open_other(pilot, screen) -> None:  # noqa: ANN001
    screen.query_one("#setup-choice-other", RadioButton).value = True
    await pilot.pause()
    await _press(pilot, screen, "setup-primary")
    assert screen._step == STEP_OTHER


def test_the_prompt_is_copied_with_the_systems_own_clipboard_tool(
    service, monkeypatch
) -> None:
    copied: list[str] = []
    monkeypatch.setattr(clipboard, "_copy_natively", lambda text: copied.append(text) or True)

    async def run() -> tuple[str, str, str]:
        app = _Host(ProviderSetupScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _open_other(pilot, screen)
            progress = _text(screen, "#setup-progress")
            await _press(pilot, screen, "setup-copy-prompt")
            return progress, _text(screen, "#setup-prompt-note"), app.clipboard

    progress, note, terminal_clipboard = asyncio.run(run())

    assert progress.startswith("Step 2 of 4")
    assert copied == [ai_setup.assistant_prompt()]
    # The terminal is asked as well: over SSH it is the only clipboard there is.
    assert terminal_clipboard == ai_setup.assistant_prompt()
    assert note.startswith("Copied.")
    assert not (app_config.settings_path().parent / "provider-prompt.txt").exists()


def test_an_unconfirmed_copy_also_leaves_the_prompt_in_a_file(service) -> None:
    async def run() -> str:
        app = _Host(ProviderSetupScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _open_other(pilot, screen)
            await _press(pilot, screen, "setup-copy-prompt")
            return _text(screen, "#setup-prompt-note")

    note = asyncio.run(run())

    saved = app_config.settings_path().parent / "provider-prompt.txt"
    assert saved.read_text(encoding="utf-8") == ai_setup.assistant_prompt()
    assert "provider-prompt.txt" in note.replace("\n", "")


def test_a_pasted_reply_becomes_a_provider_file_and_leads_to_the_key_step(service) -> None:
    async def run():  # noqa: ANN202
        app = _Host(ProviderSetupScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _open_other(pilot, screen)
            screen.query_one("#setup-reply", TextArea).text = REPLY
            await _press(pilot, screen, "setup-primary")
            assert screen._step == STEP_KEY, _text(screen, "#setup-status")
            intro = _text(screen, "#setup-key-intro")
            progress = _text(screen, "#setup-progress")
            open_page_shown = bool(screen.query_one("#setup-open-page").display)

            service.good_key = "sk-campus-0001"
            screen.query_one("#setup-key-input", Input).value = "sk-campus-0001"
            await _press(pilot, screen, "setup-primary")
            await _settle(pilot, lambda: screen._step == STEP_DONE)
            await _press(pilot, screen, "setup-primary")
            await _settle(pilot, lambda: app.result is not _UNSET)
        return intro, progress, open_page_shown, app.result

    intro, progress, open_page_shown, dismissed = asyncio.run(run())

    # No public key page for a campus service: the file's own hint is shown.
    assert "College of Engineering help desk" in intro
    assert "ai.sjsu-gateway.net" in intro
    assert not open_page_shown
    assert progress.startswith("Step 3 of 4")
    assert dismissed == "sjsu-gateway"

    written = (app_config.manifests_dir() / "sjsu-gateway.toml").read_text(encoding="utf-8")
    assert written.startswith("name         =") and "Here you go" not in written
    spec = app_config.get_provider("sjsu-gateway")
    assert spec.default_host == "https://ai.sjsu-gateway.net/v1"
    assert keystore.lookup(keystore.account_for(spec)) == "sk-campus-0001"
    assert "sk-campus-0001" not in written


def test_a_bad_reply_writes_nothing_and_offers_the_error_to_copy(service) -> None:
    async def run() -> tuple[str, bool, str, str]:
        app = _Host(ProviderSetupScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _open_other(pilot, screen)
            assert not screen.query_one("#setup-copy-error").display
            screen.query_one("#setup-reply", TextArea).text = REPLY.replace(
                "https://ai.sjsu-gateway.net", "http://ai.sjsu-gateway.net"
            )
            await _press(pilot, screen, "setup-primary")
            status = _text(screen, "#setup-status")
            shown = bool(screen.query_one("#setup-copy-error").display)
            await _press(pilot, screen, "setup-copy-error")
            return screen._step, shown, status, app.clipboard

    step, shown, status, copied = asyncio.run(run())

    assert step == STEP_OTHER and shown
    assert "cannot be used yet" in status and "https://" in status
    assert "must use https://" in copied and "whole file again" in copied
    assert list(app_config.manifests_dir().glob("*.toml")) == []


def test_replacing_an_existing_provider_from_a_reply_takes_a_second_press(service) -> None:
    ai_setup.install_reply(REPLY)
    app_config.reload_manifests()
    changed = REPLY.replace("llava-13b", "llava-34b")

    async def run() -> tuple[str, str, str]:
        app = _Host(ProviderSetupScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _open_other(pilot, screen)
            screen.query_one("#setup-reply", TextArea).text = changed
            await _press(pilot, screen, "setup-primary")
            first = (screen._step, _text(screen, "#setup-status"))
            still = app_config.get_provider("sjsu-gateway").default_vision_model
            await _press(pilot, screen, "setup-primary")
            return first[0], first[1], still, screen._step

    step, status, still, after = asyncio.run(run())

    assert step == STEP_OTHER and "already a provider called 'sjsu-gateway'" in status
    assert still == "llava-13b"
    assert after == STEP_KEY
    assert app_config.get_provider("sjsu-gateway").default_vision_model == "llava-34b"


# ---------------------------------------------------------- providers screen


def _log(screen: ProviderScreen) -> str:
    return " ".join(screen._log_lines)


def test_the_providers_screen_opens_the_setup_and_shows_what_it_added(service) -> None:
    async def run() -> tuple[str, str, bool, bool]:
        app = _Host(ProviderScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            providers = app.screen
            await _press(pilot, providers, "prov-setup")
            await _settle(pilot, lambda: isinstance(app.screen, ProviderSetupScreen))
            screen = app.screen
            await _pick(pilot, screen, "openai")
            await _press(pilot, screen, "setup-primary")
            screen.query_one("#setup-key-input", Input).value = service.good_key
            await _press(pilot, screen, "setup-primary")
            await _settle(pilot, lambda: screen._step == STEP_DONE)
            await _press(pilot, screen, "setup-primary")
            await _settle(pilot, lambda: app.screen is providers)
            await pilot.pause()
            return (
                providers._selected().name,
                str(providers.query_one("#prov-details", Static).render()),
                providers.query_one("#prov-forget-key", Button).disabled,
                providers._changed,
            )

    selected, details, forget_disabled, changed = asyncio.run(run())

    assert selected == "openai"
    assert "saved in a test key store" in details
    assert service.good_key not in details
    assert not forget_disabled
    assert changed


def test_forget_key_takes_two_presses_and_leaves_the_provider(service) -> None:
    async def run() -> tuple[str, str, bool]:
        app = _Host(ProviderScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#prov-example", Select).value = "openai"
            await _press(pilot, screen, "prov-add-example")
            spec = app_config.get_provider("openai")
            keystore.store(keystore.account_for(spec), service.good_key)
            screen._refresh_providers(select="openai")
            await pilot.pause()

            await _press(pilot, screen, "prov-forget-key")
            armed = _log(screen)
            still_there = keystore.lookup(keystore.account_for(spec))
            await _press(pilot, screen, "prov-forget-key")
            return armed, str(still_there), screen.query_one(
                "#prov-forget-key", Button
            ).disabled

    armed, still_there, disabled_after = asyncio.run(run())

    assert "Press Forget key again" in armed
    assert still_there == service.good_key
    assert disabled_after
    spec = app_config.get_provider("openai")
    assert spec.name == "openai"
    assert keystore.lookup(keystore.account_for(spec)) is None


def test_removing_a_provider_removes_its_saved_key_too(service) -> None:
    async def run() -> tuple[str, str]:
        app = _Host(ProviderScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#prov-example", Select).value = "openai"
            await _press(pilot, screen, "prov-add-example")
            spec = app_config.get_provider("openai")
            account = keystore.account_for(spec)
            keystore.store(account, service.good_key)
            await _press(pilot, screen, "prov-remove")
            await _press(pilot, screen, "prov-remove")
            return account, _log(screen)

    account, log = asyncio.run(run())

    assert keystore.lookup(account) is None
    assert "saved key was removed" in log
    assert not (app_config.manifests_dir() / "openai.toml").exists()


def test_the_example_list_marks_the_advanced_ones() -> None:
    async def run() -> list[str]:
        app = _Host(ProviderScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            picker = app.screen.query_one("#prov-example", Select)
            return [str(label) for label, _value in picker._options]

    labels = asyncio.run(run())

    assert "bedrock (advanced)" in labels and "vertex (advanced)" in labels
    assert "openai" in labels and "anthropic" in labels


def test_the_setup_button_is_at_the_right_and_the_by_hand_tools_start_folded() -> None:
    from textual.widgets import Collapsible

    async def run() -> tuple[bool, bool, int, int, bool]:
        app = _Host(ProviderScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            card = screen.query_one("#prov-advanced", Collapsible)
            inside = {widget.id for widget in card.query("*")}
            setup, test = screen.query_one("#prov-setup"), screen.query_one("#prov-test")
            hint = screen.query_one("#prov-setup-hint")
            return (
                card.collapsed,
                {"prov-example", "prov-add-example", "prov-reload", "prov-files-hint"} <= inside,
                setup.region.x,
                hint.region.x,
                # Flush with the right edge of the row of buttons below it.
                setup.region.right == screen.query_one("#prov-remove").region.right,
            )

    collapsed, folded, setup_x, hint_x, flush = asyncio.run(run())

    assert collapsed and folded
    assert setup_x > hint_x
    assert flush


def _rows(screen: ProviderScreen) -> list[str]:
    from textual.widgets import OptionList

    listing = screen.query_one("#prov-list", OptionList)
    return [
        str(listing.get_option_at_index(index).prompt)
        for index in range(listing.option_count)
    ]


def test_every_provider_is_listed_with_where_it_runs_and_whether_it_is_ready(
    service, monkeypatch
) -> None:
    asked: list[str] = []

    def local(name: str):  # noqa: ANN202
        asked.append(name)
        return (("qwen2.5vl:7b", "qwen3.5:9b"), "") if name == "ollama" else None

    monkeypatch.setattr(ai_setup, "local_models", local)
    for example in ("openai", "anthropic"):
        ai_setup.install_reply(ai_manifests_example(example))
    app_config.reload_manifests()
    keystore.store(
        keystore.account_for(app_config.get_provider("openai")), service.good_key
    )

    async def run() -> tuple[list[str], str, bool]:
        app = _Host(ProviderScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            await _settle(pilot, lambda: "2 models installed" in " ".join(_rows(screen)))
            return (
                _rows(screen),
                str(screen.query_one("#prov-details", Static).render()),
                bool(screen.query_one("#prov-log", Static).display),
            )

    rows, details, log_shown = asyncio.run(run())

    by_label = {row.split("  ")[0].strip(): row for row in rows}
    assert rows[0].startswith("Ollama on this computer")
    assert "local · 2 models installed · chosen in AI setup" in rows[0]
    assert "cloud · key saved" in by_label["OpenAI"]
    assert "cloud · no key yet" in by_label["Anthropic"]
    assert "qwen2.5vl:7b, qwen3.5:9b" in details
    # Only the provider on this computer was asked anything on opening.
    assert asked == ["ollama"]
    # Nothing has happened yet, so there is no empty Result box.
    assert not log_shown


def test_a_status_arriving_late_does_not_cancel_a_remove_waiting_for_its_second_press(
    service,
) -> None:
    ai_setup.install_reply(ai_manifests_example("openai"))
    app_config.reload_manifests()

    async def run() -> tuple[bool, bool]:
        app = _Host(ProviderScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.select_provider("openai")
            await pilot.pause()
            await _press(pilot, screen, "prov-remove")
            log_shown = bool(screen.query_one("#prov-log", Static).display)
            # A local server answers while the user reads the warning.
            screen._show_local("ollama", (("qwen3.5:9b",), ""))
            await pilot.pause()
            await _press(pilot, screen, "prov-remove")
            return log_shown, (app_config.manifests_dir() / "openai.toml").exists()

    log_shown, still_there = asyncio.run(run())

    assert log_shown
    assert not still_there


def ai_manifests_example(name: str) -> str:
    from opengrader.core.app_config import manifests

    return manifests.example_text(name)
