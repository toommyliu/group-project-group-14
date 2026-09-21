"""The provider screen, and the half of Phase 4's exit criterion that is a UI.

§7.9 says a hand-written manifest for a service the codebase has never heard
of must appear in the AI Setup dropdown **with zero edits to
`ai_settings_screen.py`**. The first test here checks exactly that. It writes
a TOML file and opens the unmodified AI Setup screen, then chooses and saves
the new provider. The extraction half is in
`tests/unit/core/submissions/test_extraction_through_a_manifest_provider.py`.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from textual.app import App
from textual.widgets import Button, RichLog, Select, Static

from opengrader.core import ai_config
from opengrader.tui.ui.screens.ai_settings_screen import AISettingsScreen
from opengrader.tui.ui.screens.provider_screen import ProviderScreen

_SIZE = (130, 50)

UNHEARD_OF = """\
name         = "zephyr-labs"
label        = "Zephyr Labs Vision"
dialect      = "anthropic-messages"
host         = "https://inference.zephyr-labs.example/v1"
vision_model = "zephyr-v3"
text_model   = "zephyr-t3"
api_key_env  = "ZEPHYR_KEY"
"""


class _Host(App):
    def __init__(self, screen) -> None:  # noqa: ANN001
        super().__init__()
        self._screen = screen
        self.result = None
        self.dismissed = False

    def on_mount(self) -> None:
        def _record(value) -> None:  # noqa: ANN001
            self.result = value
            self.dismissed = True

        self.push_screen(self._screen, _record)


async def _settle(pilot, predicate, limit: int = 100) -> None:  # noqa: ANN001
    for _ in range(limit):
        await pilot.pause()
        await asyncio.sleep(0.01)
        if predicate():
            return


def _log(screen: ProviderScreen) -> str:
    return " ".join(str(line) for line in screen.query_one("#prov-log", RichLog).lines)


async def _press(pilot, screen, button_id: str) -> None:  # noqa: ANN001
    """Press a button as a separate action, not as the second half of a click.

    Two `pilot.click`s in a row land inside Textual's double-click window,
    which does not press a button twice. That is right for a confirm step,
    since a double-click should not confirm a delete, so the tests press
    explicitly instead.
    """
    screen.query_one(f"#{button_id}", Button).press()
    await pilot.pause()


def _details(screen: ProviderScreen) -> str:
    return str(screen.query_one("#prov-details", Static).render())


@pytest.fixture(autouse=True)
def _clean_registry():
    ai_config.reload_manifests()
    try:
        yield
    finally:
        for path in ai_config.manifests_dir().glob("*.toml"):
            path.unlink()
        ai_config.reload_manifests()


@pytest.fixture
def no_network(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("the screen opened a socket")

    monkeypatch.setattr("urllib.request.urlopen", _boom)


def test_a_hand_written_manifest_is_offered_and_saved_by_ai_setup(no_network) -> None:
    directory = ai_config.manifests_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "zephyr.toml").write_text(UNHEARD_OF, encoding="utf-8")
    ai_config.reload_manifests()

    async def run() -> tuple[list, object]:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            picker = app.screen.query_one("#ai-provider", Select)
            options = list(picker._options)
            picker.value = "zephyr-labs"
            await pilot.pause()
            await pilot.click("#ai-save")
            await _settle(pilot, lambda: app.dismissed)
        return options, app.result

    options, saved = asyncio.run(run())

    assert ("Zephyr Labs Vision", "zephyr-labs") in [(str(l), v) for l, v in options]
    assert saved is not None and saved.provider == "zephyr-labs"
    assert ai_config.load_settings().provider == "zephyr-labs"
    assert ai_config.resolve().provider.name == "zephyr-labs"


def test_the_screen_opens_on_the_local_default(no_network) -> None:
    async def run() -> str:
        app = _Host(ProviderScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            assert screen.query_one("#prov-remove", Button).disabled
            return _details(screen)

    details = asyncio.run(run())

    assert "LOCAL" in details
    assert ai_config.DEFAULT_HOST in details


def test_adding_an_example_registers_it_and_states_it_is_cloud(no_network) -> None:
    async def run() -> tuple[str, str, bool]:
        app = _Host(ProviderScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#prov-example", Select).value = "openai"
            await pilot.pause()
            await pilot.click("#prov-add-example")
            await pilot.pause()
            log, details = _log(screen), _details(screen)
            assert screen.query_one("#prov-select", Select).value == "openai"
            assert not screen.query_one("#prov-remove", Button).disabled
            await pilot.click("#prov-close")
            await _settle(pilot, lambda: app.dismissed)
        return log, details, app.result

    log, details, changed = asyncio.run(run())

    assert "openai" in {spec.name for spec in ai_config.registered_providers()}
    assert "cloud provider" in log and "api.openai.com" in log
    assert "CLOUD" in details
    assert changed is True


def test_removing_needs_a_second_press(no_network) -> None:
    ai_config.manifests_dir().mkdir(parents=True, exist_ok=True)
    (ai_config.manifests_dir() / "zephyr.toml").write_text(UNHEARD_OF, encoding="utf-8")
    ai_config.reload_manifests()

    async def run() -> tuple[bool, str]:
        app = _Host(ProviderScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#prov-select", Select).value = "zephyr-labs"
            await pilot.pause()
            await _press(pilot, screen, "prov-remove")
            still_there = (ai_config.manifests_dir() / "zephyr.toml").exists()
            await _press(pilot, screen, "prov-remove")
            return still_there, _log(screen)

    still_there_after_one, log = asyncio.run(run())

    assert still_there_after_one
    assert not (ai_config.manifests_dir() / "zephyr.toml").exists()
    assert "Removed Zephyr Labs Vision" in log
    assert "zephyr-labs" not in {spec.name for spec in ai_config.registered_providers()}


def test_testing_a_cloud_provider_needs_a_second_press(monkeypatch) -> None:
    ai_config.manifests_dir().mkdir(parents=True, exist_ok=True)
    (ai_config.manifests_dir() / "zephyr.toml").write_text(UNHEARD_OF, encoding="utf-8")
    ai_config.reload_manifests()
    monkeypatch.setenv("ZEPHYR_KEY", "sk-zephyr-0123456789abcdef")
    sent = []

    class _Response:
        status = 200

        def read(self) -> bytes:
            return json.dumps({"content": [{"type": "text", "text": "ready"}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

    def _urlopen(req, timeout=None):
        sent.append(req)
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)

    async def run() -> tuple[int, str, str]:
        app = _Host(ProviderScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#prov-select", Select).value = "zephyr-labs"
            await pilot.pause()
            await _press(pilot, screen, "prov-test")
            after_first, warning = len(sent), _log(screen)
            await _press(pilot, screen, "prov-test")
            await _settle(pilot, lambda: "reachable" in _log(screen))
            return after_first, warning, _log(screen)

    after_first, warning, result = asyncio.run(run())

    assert after_first == 0
    assert "inference.zephyr-labs.example" in warning
    assert "reachable" in result
    assert [r.full_url for r in sent] == [
        "https://inference.zephyr-labs.example/v1/messages"
    ]
    assert "sk-zephyr" not in result


def test_a_broken_file_is_listed_with_its_reason(no_network) -> None:
    directory = ai_config.manifests_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "sneaky.toml").write_text(
        UNHEARD_OF.replace("zephyr-labs\"", "sneaky\"", 1) + 'kind = "local"\n',
        encoding="utf-8",
    )

    async def run() -> str:
        app = _Host(ProviderScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await pilot.click("#prov-reload")
            await pilot.pause()
            return str(app.screen.query_one("#prov-problems", Static).render())

    problems = asyncio.run(run())

    assert "sneaky.toml" in problems and "kind" in problems
