"""`BrowserHealth` — the Node/pnpm + build-freshness panel in Tools.

Every check here is monkeypatched: real `node`/`pnpm` live at `/opt/local/bin`
on this machine but must never actually run from a test.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Static

from opengrader.core import browser_app, homebrew
from opengrader.core.browser_app import ToolInfo, ToolStatus, WebBuildError
from opengrader.tui.ui.widgets.browser_health import BrowserHealth

NODE = ToolInfo(path="/opt/local/bin/node", version="v22.22.2")
PNPM = ToolInfo(path="/opt/local/bin/pnpm", version="12.6.0")
BREW = "/opt/homebrew/bin/brew"


@pytest.fixture(autouse=True)
def _no_homebrew(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Homebrew unless a test says so — whatever this machine has."""
    monkeypatch.setattr(homebrew, "find_homebrew", lambda: None)


class _Host(App):
    """Just the widget — the full `SetupScreen` is not needed for these."""

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        super().__init__()
        self._kwargs = kwargs

    def compose(self) -> ComposeResult:
        yield BrowserHealth(**self._kwargs)


async def _settle(pilot) -> None:  # noqa: ANN001
    for _ in range(50):
        await pilot.pause()
        await asyncio.sleep(0.01)


def _stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    node: ToolInfo | None = None,
    pnpm: ToolInfo | None = None,
    state: str = "missing",
    buildable: bool = True,
) -> None:
    monkeypatch.setattr(browser_app, "tool_status", lambda: ToolStatus(node=node, pnpm=pnpm))
    monkeypatch.setattr(browser_app, "web_dist_state", lambda source=None: state)
    monkeypatch.setattr(browser_app, "web_buildable", lambda source=None: buildable)


def _rows_text(app: App) -> str:
    return str(app.query_one("#browser-health-rows", Static).render())


async def _checked(app: App, extract):  # noqa: ANN001, ANN201
    """Runs `app`, waits for the initial check to settle, and reads `extract(app)`
    while the app is still mounted — a query after `run_test` exits finds nothing."""
    async with app.run_test(size=(80, 24)) as pilot:
        await _settle(pilot)
        await app.workers.wait_for_complete()
        await pilot.pause()
        return extract(app)


def test_a_hidden_panel_never_runs_a_check(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom_status() -> ToolStatus:
        raise AssertionError("tool_status ran while the panel was hidden")

    def _boom_state(source=None) -> str:  # noqa: ANN001
        raise AssertionError("web_dist_state ran while the panel was hidden")

    monkeypatch.setattr(browser_app, "tool_status", _boom_status)
    monkeypatch.setattr(browser_app, "web_dist_state", _boom_state)

    async def run() -> None:
        app = _Host(classes="hidden")
        async with app.run_test(size=(80, 24)) as pilot:
            await _settle(pilot)
            await app.workers.wait_for_complete()

    asyncio.run(run())  # no AssertionError means neither check ran


def test_a_shown_panel_checks_and_lists_node_and_pnpm(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, node=NODE, pnpm=PNPM, state="ready", buildable=True)

    text = asyncio.run(_checked(_Host(), _rows_text))
    assert "✓ Node.js v22.22.2" in text and "/opt/local/bin/node" in text
    assert "✓ pnpm 12.6.0" in text and "/opt/local/bin/pnpm" in text
    assert "up to date" in text


def test_missing_tools_are_reported_with_an_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub(monkeypatch, node=None, pnpm=None, state="missing", buildable=True)

    text = asyncio.run(_checked(_Host(), _rows_text))
    assert "✗ Node.js not found" in text
    assert "✗ pnpm not found" in text
    assert "nodejs.org" in text
    assert "✗ Browser app not built yet" in text


def test_a_stale_build_is_reported_as_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, node=NODE, pnpm=PNPM, state="stale", buildable=True)

    text = asyncio.run(_checked(_Host(), _rows_text))
    assert "⚠ Browser app is out of date" in text


def _build_button(app: App) -> Button:
    return app.query_one("#browser-build", Button)


def test_build_button_is_disabled_without_pnpm(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, node=NODE, pnpm=None, state="missing", buildable=True)

    assert asyncio.run(_checked(_Host(), lambda app: _build_button(app).disabled))


def test_build_button_is_disabled_without_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, node=NODE, pnpm=PNPM, state="missing", buildable=False)

    assert asyncio.run(_checked(_Host(), lambda app: _build_button(app).disabled))


def test_build_button_offers_rebuild_once_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, node=NODE, pnpm=PNPM, state="ready", buildable=True)

    disabled, label = asyncio.run(
        _checked(_Host(), lambda app: (_build_button(app).disabled, str(_build_button(app).label)))
    )
    assert not disabled
    assert "Rebuild" in label


def test_check_again_reruns_the_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _status() -> ToolStatus:
        calls["n"] += 1
        return ToolStatus(node=NODE, pnpm=PNPM)

    monkeypatch.setattr(browser_app, "tool_status", _status)
    monkeypatch.setattr(browser_app, "web_dist_state", lambda source=None: "ready")
    monkeypatch.setattr(browser_app, "web_buildable", lambda source=None: True)

    async def run() -> int:
        app = _Host()
        async with app.run_test(size=(80, 24)) as pilot:
            await _settle(pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.query_one("#browser-check", Button).press()
            await _settle(pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            return calls["n"]

    assert asyncio.run(run()) >= 2


def test_set_enabled_shows_the_panel_and_checks_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub(monkeypatch, node=NODE, pnpm=PNPM, state="ready", buildable=True)

    async def run() -> tuple[bool, bool]:
        app = _Host(classes="hidden")
        async with app.run_test(size=(80, 24)) as pilot:
            await _settle(pilot)
            widget = app.query_one(BrowserHealth)
            hidden_before = widget.has_class("hidden")
            widget.set_enabled(True)
            await _settle(pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            return hidden_before, widget.has_class("hidden")

    hidden_before, hidden_after = asyncio.run(run())
    assert hidden_before and not hidden_after


def test_pressing_build_runs_build_web_and_refreshes_the_panel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"value": "missing"}
    monkeypatch.setattr(
        browser_app, "tool_status", lambda: ToolStatus(node=NODE, pnpm=PNPM)
    )
    monkeypatch.setattr(browser_app, "web_dist_state", lambda source=None: state["value"])
    monkeypatch.setattr(browser_app, "web_buildable", lambda source=None: True)
    built = {"n": 0}

    def _build(source=None, *, on_line=None, log=None):  # noqa: ANN001, ANN202
        built["n"] += 1
        state["value"] = "ready"

    monkeypatch.setattr(browser_app, "build_web", _build)

    async def run() -> tuple[int, str]:
        app = _Host()
        async with app.run_test(size=(80, 24)) as pilot:
            await _settle(pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.query_one("#browser-build", Button).press()
            await _settle(pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            return built["n"], _rows_text(app)

    count, text = asyncio.run(run())
    assert count == 1
    assert "up to date" in text


# -- installing Node.js and pnpm ----------------------------------------------


def _install_button(app: App) -> Button:
    return app.query_one("#browser-install-tools", Button)


def test_no_install_button_without_homebrew(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, node=None, pnpm=None)

    shown, text = asyncio.run(
        _checked(_Host(), lambda app: (_install_button(app).display, _rows_text(app)))
    )
    assert shown is False
    assert "press Install" not in text


def test_no_install_button_when_nothing_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub(monkeypatch, node=NODE, pnpm=PNPM, state="ready")
    monkeypatch.setattr(homebrew, "find_homebrew", lambda: BREW)

    assert asyncio.run(_checked(_Host(), lambda app: _install_button(app).display)) is False


def test_missing_tools_with_homebrew_offer_to_install_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub(monkeypatch, node=NODE, pnpm=None)
    monkeypatch.setattr(homebrew, "find_homebrew", lambda: BREW)
    monkeypatch.setattr(
        browser_app, "install_tools", lambda *a, **k: pytest.fail("installed unasked")
    )

    shown, disabled, text = asyncio.run(
        _checked(
            _Host(),
            lambda app: (
                _install_button(app).display,
                _install_button(app).disabled,
                _rows_text(app),
            ),
        )
    )
    assert shown is True and not disabled
    assert "press Install Node.js and pnpm" in text


def test_pressing_install_installs_and_enables_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = {"status": ToolStatus(node=None, pnpm=None)}
    monkeypatch.setattr(browser_app, "tool_status", lambda: tools["status"])
    monkeypatch.setattr(browser_app, "web_dist_state", lambda source=None: "missing")
    monkeypatch.setattr(browser_app, "web_buildable", lambda source=None: True)
    monkeypatch.setattr(homebrew, "find_homebrew", lambda: BREW)
    installs: list[str] = []

    def _install(brew, *, on_line=None, on_start=None):  # noqa: ANN001, ANN202
        installs.append(brew)
        on_line("==> Pouring node")
        tools["status"] = ToolStatus(node=NODE, pnpm=PNPM)

    monkeypatch.setattr(browser_app, "install_tools", _install)

    async def run() -> tuple[bool, bool, str]:
        app = _Host()
        async with app.run_test(size=(80, 24)) as pilot:
            await _settle(pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert _build_button(app).disabled
            _install_button(app).press()
            await _settle(pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            return (
                _install_button(app).display,
                _build_button(app).disabled,
                _rows_text(app),
            )

    install_shown, build_disabled, text = asyncio.run(run())
    assert installs == [BREW]
    assert install_shown is False
    assert not build_disabled
    assert "✓ Node.js" in text and "✓ pnpm" in text


def test_a_failed_install_says_why_and_can_be_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub(monkeypatch, node=None, pnpm=None)
    monkeypatch.setattr(homebrew, "find_homebrew", lambda: BREW)

    def _install(brew, *, on_line=None, on_start=None):  # noqa: ANN001, ANN202
        raise WebBuildError("Node.js and pnpm were not installed.", "Error: no bottle")

    monkeypatch.setattr(browser_app, "install_tools", _install)
    notices: list[tuple[str, str]] = []

    async def run() -> tuple[bool, bool]:
        app = _Host()
        async with app.run_test(size=(80, 24)) as pilot:
            await _settle(pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            monkeypatch.setattr(
                app,
                "notify",
                lambda message, **kw: notices.append((str(message), kw.get("severity", ""))),
            )
            _install_button(app).press()
            await _settle(pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            return _install_button(app).display, _install_button(app).disabled

    shown, disabled = asyncio.run(run())
    assert shown is True and not disabled
    assert notices and notices[-1][1] == "error"
    assert "were not installed" in notices[-1][0] and "no bottle" in notices[-1][0]


def test_quitting_stops_a_running_install(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, node=None, pnpm=None)
    monkeypatch.setattr(homebrew, "find_homebrew", lambda: BREW)
    release = threading.Event()

    class _Process:
        terminated = False

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            self.terminated = True
            release.set()

    process = _Process()

    def _install(brew, *, on_line=None, on_start=None):  # noqa: ANN001, ANN202
        on_start(process)
        release.wait(timeout=10)
        raise WebBuildError("stopped")

    monkeypatch.setattr(browser_app, "install_tools", _install)

    async def run() -> None:
        app = _Host()
        async with app.run_test(size=(80, 24)) as pilot:
            await _settle(pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            _install_button(app).press()
            for _ in range(200):
                if app.query_one(BrowserHealth)._install_process is not None:
                    break
                await pilot.pause()
                await asyncio.sleep(0.01)

    try:
        asyncio.run(run())
    finally:
        release.set()
    assert process.terminated
