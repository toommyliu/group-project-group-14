"""Ask before building — or rebuilding — the browser app (`core/browser_app`).

Two situations share this dialog: the app has never been built (`stale=False`)
and the app is built but its web sources have since changed (`stale=True`,
from `browser_app.web_dist_state`). Either way the result says what the
caller should do next, not just yes/no, since "open the possibly-stale copy
anyway" is a real choice a stale build offers that a missing one doesn't.
"""

from __future__ import annotations

from typing import Literal

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

# What the user chose: "cancel" (dismissed/closed, do nothing), "open" (use
# the build that's there, stale or not), "build" (build/rebuild, then open) or
# "install" (install Node.js with Homebrew, then build and open).
BrowserBuildChoice = Literal["cancel", "open", "build", "install"]


class BrowserBuildScreen(ModalScreen[BrowserBuildChoice]):
    """Dismisses with the user's choice; see `BrowserBuildChoice`.

    With no npm on this machine there is nothing to build with. On a Mac
    that has Homebrew (`can_install`) the dialog offers to install Node.js
    first; otherwise it says what to install and
    offers only Close. Neither happens for the stale variant — the caller only shows it once it has
    already confirmed npm is there (see `browser_launcher.open_folder`).
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    BrowserBuildScreen {
        align: center middle;
    }

    #bb-dialog {
        width: 80%;
        max-width: 90;
        height: auto;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #bb-dialog Static {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }

    #bb-dialog .bb-muted {
        color: $text-muted;
    }

    #bb-buttons {
        height: auto;
        align-horizontal: right;
    }

    #bb-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(
        self, *, can_build: bool, stale: bool = False, can_install: bool = False
    ) -> None:
        super().__init__()
        self._can_build = can_build
        self._stale = stale
        # Only ever acted on when there is nothing to build with.
        self._can_install = can_install and not can_build and not stale

    def compose(self) -> ComposeResult:
        with Vertical(id="bb-dialog") as dialog:
            dialog.border_title = (
                "Browser app out of date" if self._stale else "Set up the browser app"
            )
            if self._stale:
                yield Static(
                    "The browser app's web files have changed since it was "
                    "last built. It still works — you may just be missing a "
                    "recent fix."
                )
                yield Static(
                    "Rebuilding takes a minute or two and needs the internet; "
                    "grading in this window keeps working meanwhile. Opening "
                    "as it is now needs neither.",
                    classes="bb-muted",
                )
            elif self._can_build:
                yield Static(
                    "The browser app is not built on this computer yet. It is "
                    "built once, the first time you open it, and then starts "
                    "in seconds."
                )
                yield Static(
                    "Building downloads the app's packages (a few hundred MB, "
                    "needs the internet this one time) and takes a minute or "
                    "two. Grading in this window keeps working meanwhile.",
                    classes="bb-muted",
                )
            elif self._can_install:
                yield Static(
                    "The browser app is not built on this computer yet. It is "
                    "built once, the first time you open it, and then starts "
                    "in seconds."
                )
                yield Static(
                    "Building needs Node.js, which is not installed. "
                    "Homebrew is on this Mac, so Opengrader can install it "
                    "for you (brew install node) and then build the app. "
                    "Both need the internet and together take several "
                    "minutes. Grading in this window keeps working meanwhile.",
                    classes="bb-muted",
                )
            else:
                yield Static(
                    "The browser app is not built on this computer yet. It is "
                    "built once, the first time you open it, and then starts "
                    "in seconds."
                )
                yield Static(
                    "Building needs Node.js, which is not installed. "
                    "Install Node.js from https://nodejs.org — or on a Mac "
                    "with Homebrew: brew install node — then press Open "
                    "in browser again.",
                    classes="bb-muted",
                )
            with Horizontal(id="bb-buttons"):
                if self._stale:
                    yield Button("Open anyway", id="bb-open", compact=True)
                    yield Button(
                        "Rebuild and open",
                        id="bb-build",
                        variant="primary",
                        compact=True,
                    )
                elif self._can_build:
                    yield Button("Not now", id="bb-cancel", compact=True)
                    yield Button(
                        "Build and open", id="bb-build", variant="primary", compact=True
                    )
                elif self._can_install:
                    yield Button("Not now", id="bb-cancel", compact=True)
                    yield Button(
                        "Install and build",
                        id="bb-install",
                        variant="primary",
                        compact=True,
                    )
                else:
                    yield Button("Close", id="bb-cancel", variant="primary", compact=True)

    def on_mount(self) -> None:
        # Rebuilding is the safer default for a stale copy; for a missing one
        # it's Build when there's something to build with. Installing software
        # is never the default: that one takes a deliberate press.
        default = "#bb-build" if (self._stale or self._can_build) else "#bb-cancel"
        self.query_one(default, Button).focus()

    @on(Button.Pressed, "#bb-build")
    def _build(self) -> None:
        self.dismiss("build")

    @on(Button.Pressed, "#bb-install")
    def _install(self) -> None:
        self.dismiss("install")

    @on(Button.Pressed, "#bb-open")
    def _open_anyway(self) -> None:
        self.dismiss("open")

    @on(Button.Pressed, "#bb-cancel")
    def _cancel_button(self) -> None:
        self.dismiss("cancel")

    def action_cancel(self) -> None:
        self.dismiss("cancel")
