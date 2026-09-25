"""`BrowserHealth` — the Node/pnpm + build-freshness panel in Tools.

Every check here is monkeypatched: real `node`/`pnpm` live at `/opt/local/bin`
on this machine but must never actually run from a test.
"""

from __future__ import annotations

import asyncio

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Static

from opengrader.core import browser_app
from opengrader.core.browser_app import ToolInfo, ToolStatus
from opengrader.tui.ui.widgets.browser_health import BrowserHealth

NODE = ToolInfo(path="/opt/local/bin/node", version="v22.22.2")
PNPM = ToolInfo(path="/opt/local/bin/pnpm", version="12.6.0")


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
