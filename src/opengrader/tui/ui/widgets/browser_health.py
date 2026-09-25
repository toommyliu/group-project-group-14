"""Health panel for the browser app section in Tools (`setup_screen.py`).

*Show Open in browser* only tells the user whether the button is on; it says
nothing about whether the thing it opens actually works on this computer.
This widget answers that: is Node.js here, is pnpm here, and is the built
bundle (`web/dist/`) the one the current sources would produce, or one built
before they last changed (`core/browser_app.web_dist_state`).

Composed into `#section-browser` but otherwise self-contained: `SetupScreen`
only shows or hides it (`set_enabled`) from `_set_browser_enabled` and the
saved preference at compose time. Every check — `node --version`, `pnpm
--version`, hashing the web sources — runs off the UI thread, and none of it
runs at all while the section is hidden, so a user who never opens Tools (or
never turns the browser app on) never pays for it.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static

from opengrader.core import browser_app

INSTALL_HINT = (
    "Install Node.js from https://nodejs.org (bundles pnpm via corepack) — "
    "or `brew install node pnpm` — or on MacPorts: "
    "`sudo port install nodejs22 pnpm`."
)


@dataclass(frozen=True)
class _Snapshot:
    tools: browser_app.ToolStatus
    dist_state: str
    buildable: bool


def _gather() -> _Snapshot:
    """Runs off the UI thread — everything here shells out or hits disk."""
    return _Snapshot(
        tools=browser_app.tool_status(),
        dist_state=browser_app.web_dist_state(),
        buildable=browser_app.web_buildable(),
    )


def _tool_line(label: str, info: browser_app.ToolInfo | None) -> str:
    if info is None:
        return f"✗ {label} not found"
    return f"✓ {label} {info.version}   {info.path}"


def _build_line(state: str) -> str:
    if state == "ready":
        return "✓ Browser app built and up to date"
    if state == "stale":
        return "⚠ Browser app is out of date (web files changed since it was built)"
    return "✗ Browser app not built yet"


class BrowserHealth(Vertical):
    DEFAULT_CSS = """
    BrowserHealth {
        width: 100%;
        height: auto;
    }

    BrowserHealth #browser-health-rows {
        width: 100%;
        height: auto;
    }

    BrowserHealth #browser-health-progress {
        width: 100%;
        height: auto;
        color: $text-muted;
        margin-top: 1;
    }

    BrowserHealth #browser-health-actions {
        width: 100%;
        height: auto;
        margin-top: 1;
    }

    BrowserHealth #browser-health-actions Button {
        margin-right: 1;
    }
    """

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        super().__init__(**kwargs)
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Static("Checking…", id="browser-health-rows", markup=False)
        yield Static(
            "", id="browser-health-progress", classes="hidden", markup=False
        )
        with Horizontal(id="browser-health-actions"):
            yield Button("Check again", id="browser-check", compact=True)
            yield Button(
                "Build browser app", id="browser-build", compact=True, disabled=True
            )

    def on_mount(self) -> None:
        # Composed hidden or shown per the saved preference (see setup_screen);
        # only run the checks if we start out visible.
        if not self.has_class("hidden"):
            self.check_again()

    # -- shown/hidden, from `_set_browser_enabled` -------------------------

    def set_enabled(self, enabled: bool) -> None:
        self.set_class(not enabled, "hidden")
        if enabled:
            self.check_again()

    # -- checks --------------------------------------------------------------

    @on(Button.Pressed, "#browser-check")
    def _on_check_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.check_again()

    def check_again(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._check_worker()

    @work(thread=True, exclusive=True, group="browser-health-check")
    def _check_worker(self) -> None:
        snapshot = _gather()
        self.app.call_from_thread(self._checked, snapshot)

    def _checked(self, snapshot: _Snapshot) -> None:
        self._set_busy(False)
        self._apply_snapshot(snapshot)

    def _apply_snapshot(self, snapshot: _Snapshot) -> None:
        lines = [
            _tool_line("Node.js", snapshot.tools.node),
            _tool_line("pnpm", snapshot.tools.pnpm),
        ]
        if snapshot.tools.node is None or snapshot.tools.pnpm is None:
            lines.append(INSTALL_HINT)
        lines.append(_build_line(snapshot.dist_state))
        self.query_one("#browser-health-rows", Static).update("\n".join(lines))

        build_button = self.query_one("#browser-build", Button)
        build_button.label = (
            "Build browser app"
            if snapshot.dist_state == "missing"
            else "Rebuild browser app"
        )
        build_button.disabled = snapshot.tools.pnpm is None or not snapshot.buildable

    # -- build ---------------------------------------------------------------

    @on(Button.Pressed, "#browser-build")
    def _on_build_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self._start_build()

    def _start_build(self) -> None:
        if self._busy:
            return
        self._set_busy(True)
        progress = self.query_one("#browser-health-progress", Static)
        progress.update("Starting…")
        progress.remove_class("hidden")
        self._build_worker()

    @work(thread=True, exclusive=True, group="browser-health-build")
    def _build_worker(self) -> None:
        app = self.app

        def on_line(line: str) -> None:
            app.call_from_thread(self._progress, line)

        error: browser_app.WebBuildError | None = None
        try:
            browser_app.build_web(on_line=on_line)
        except browser_app.WebBuildError as exc:
            error = exc
        # Re-gather here, still off the UI thread: a failed install can still
        # have left a usable (if stale) dist/ behind, so the panel reflects
        # what actually happened rather than assuming success or failure.
        snapshot = _gather()
        app.call_from_thread(self._build_done, error, snapshot)

    def _progress(self, line: str) -> None:
        self.query_one("#browser-health-progress", Static).update(line)

    def _build_done(
        self, error: browser_app.WebBuildError | None, snapshot: _Snapshot
    ) -> None:
        self._set_busy(False)
        self.query_one("#browser-health-progress").add_class("hidden")
        self._apply_snapshot(snapshot)
        if error is None:
            self.app.notify("Browser app built and ready.")
        else:
            detail = f"\n{error.tail}" if error.tail else ""
            self.app.notify(f"{error}{detail}", severity="error", timeout=15)

    # -- shared busy state -----------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.query_one("#browser-check", Button).disabled = busy
        if busy:
            self.query_one("#browser-build", Button).disabled = True
        # else: `_apply_snapshot` sets the build button's disabled state itself.
