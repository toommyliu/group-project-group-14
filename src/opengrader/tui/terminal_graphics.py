"""Terminal graphics capability detection.

Opengrader's grade screen shows the *scanned handwriting crop* for the answer
being graded. Painting real pixels inside a terminal needs the emulator to
implement a graphics protocol, and there are three in the wild:

``kitty``
    The Kitty graphics protocol (a.k.a. TGP). Best quality and the only one
    that can address images by id, so a redraw replaces rather than repaints.
    kitty, Ghostty, WezTerm (partially), Konsole (partially), recent iTerm2.
``iterm2``
    iTerm2's inline-images protocol (``OSC 1337 ; File=...``). Ubiquitous on
    macOS and, importantly, the protocol WezTerm/VS Code implement *reliably*
    (their Kitty-protocol support does not cover the unicode placeholders the
    renderer depends on).
``sixel``
    The DEC VT340 bitmap format. Older and lossier (palette-limited), but the
    widest support: XTerm, foot, mlterm, Windows Terminal 1.22+, Konsole,
    VS Code, Rio.

Anything else (macOS Terminal.app, GNOME Terminal, PuTTY, a tmux session
without passthrough, a piped/CI run) gets no pixels at all. There the TUI
degrades to a coarse half-block preview and the grader is pointed at the
external-viewer escape hatch (:mod:`opengrader.tui.external_viewer`).

This module is the single place that decides which of those we are talking to.
It answers in three passes, cheapest first:

1. an explicit override (``--graphics`` / ``OPENGRADER_GRAPHICS``),
2. environment fingerprinting -- free, silent, and exactly right for the
   terminals instructors actually run,
3. querying the terminal, reusing ``textual-image``'s own probe so we never
   write a second round of escape sequences into the user's stdin.

Detection is a pure function over an environment mapping, so it is unit-tested
without a terminal. The process-wide answer is set once by
:func:`configure_graphics` (called from the CLI before the app starts) and read
by widgets through :func:`active_graphics`.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, get_args

GraphicsProtocol = Literal["kitty", "iterm2", "sixel", "halfcell", "unicode"]

#: Protocols that put real pixels on screen. Everything else is a text
#: approximation, legible for a chart but not for handwriting.
PIXEL_PROTOCOLS: frozenset[str] = frozenset({"kitty", "iterm2", "sixel"})

#: Accepted values for ``--graphics`` / ``OPENGRADER_GRAPHICS``.
PROTOCOL_CHOICES: tuple[str, ...] = ("auto", *get_args(GraphicsProtocol))

ENV_OVERRIDE = "OPENGRADER_GRAPHICS"

_PROTOCOL_LABELS: dict[str, str] = {
    "kitty": "Kitty graphics protocol",
    "iterm2": "iTerm2 inline images",
    "sixel": "Sixel",
    "halfcell": "half-block text approximation",
    "unicode": "unicode text approximation",
}


class GraphicsOverrideError(ValueError):
    """Raised when an explicit protocol override is not a known protocol."""


@dataclass(frozen=True)
class GraphicsCapability:
    """What this terminal can do, and how we found out."""

    protocol: GraphicsProtocol
    #: ``override`` | ``environment`` | ``query`` | ``fallback``
    source: str
    #: Human-readable terminal name (``""`` when unrecognised).
    terminal: str = ""
    #: One sentence of context for the user, may be empty.
    detail: str = ""

    @property
    def pixel_accurate(self) -> bool:
        """True when answer crops render as real pixels."""
        return self.protocol in PIXEL_PROTOCOLS

    @property
    def label(self) -> str:
        return _PROTOCOL_LABELS.get(self.protocol, self.protocol)

    def describe(self) -> str:
        """One-line summary, e.g. ``Sixel (WezTerm)``."""
        if self.terminal:
            return f"{self.label} ({self.terminal})"
        return self.label


# --------------------------------------------------------------------------
# Environment fingerprinting
# --------------------------------------------------------------------------


def _term_program(environ: Mapping[str, str]) -> str:
    return environ.get("TERM_PROGRAM", "").strip().lower()


def _detect_from_environment(
    environ: Mapping[str, str],
) -> GraphicsCapability | None:
    """Recognise the terminal from the variables it exports.

    Ordered most-specific first. Every branch encodes a concrete, verified
    fact about that emulator -- when a terminal supports several protocols we
    pick the one that works best *with this renderer*, which is not always the
    newest one (see WezTerm and VS Code below).
    """
    term = environ.get("TERM", "").strip().lower()
    program = _term_program(environ)

    # A multiplexer sits between us and the emulator and, by default, filters
    # the escape sequences graphics protocols are made of. Passthrough can be
    # enabled (tmux `allow-passthrough`), so this is a downgrade the user can
    # override, not a refusal.
    if environ.get("TMUX") or term.startswith(("screen", "tmux")):
        return GraphicsCapability(
            "halfcell",
            source="environment",
            terminal="tmux/screen",
            detail=(
                "terminal multiplexers do not forward graphics escape "
                "sequences by default; run outside tmux, or force a protocol "
                "with --graphics once passthrough is enabled"
            ),
        )

    if term == "xterm-kitty" or environ.get("KITTY_WINDOW_ID"):
        return GraphicsCapability("kitty", source="environment", terminal="kitty")

    if program == "ghostty" or environ.get("GHOSTTY_RESOURCES_DIR"):
        return GraphicsCapability("kitty", source="environment", terminal="Ghostty")

    if "contour" in term:
        return GraphicsCapability("kitty", source="environment", terminal="Contour")

    # WezTerm advertises the Kitty protocol but does not implement the unicode
    # placeholder cells the renderer relies on; its iTerm2 support is complete.
    if program == "wezterm" or environ.get("WEZTERM_EXECUTABLE"):
        return GraphicsCapability(
            "iterm2",
            source="environment",
            terminal="WezTerm",
            detail="using iTerm2 inline images (WezTerm's Kitty placeholders are incomplete)",
        )

    if program == "iterm.app" or environ.get("LC_TERMINAL", "").lower() == "iterm2":
        return GraphicsCapability("iterm2", source="environment", terminal="iTerm2")

    if program == "vscode":
        return GraphicsCapability(
            "iterm2",
            source="environment",
            terminal="VS Code",
            detail=(
                "requires the built-in terminal's image support "
                "(settings: terminal.integrated.enableImages)"
            ),
        )

    if program == "rio":
        return GraphicsCapability("sixel", source="environment", terminal="Rio")

    if environ.get("KONSOLE_VERSION"):
        return GraphicsCapability("sixel", source="environment", terminal="Konsole")

    if "mlterm" in term:
        return GraphicsCapability("sixel", source="environment", terminal="mlterm")

    if term.startswith("foot"):
        return GraphicsCapability("sixel", source="environment", terminal="foot")

    if environ.get("WT_SESSION"):
        return GraphicsCapability(
            "sixel",
            source="environment",
            terminal="Windows Terminal",
            detail="Sixel requires Windows Terminal 1.22 or newer",
        )

    # Known-negative: saying so up front beats probing a terminal that will
    # never answer, and lets the UI explain itself instead of just looking bad.
    if program == "apple_terminal":
        return GraphicsCapability(
            "halfcell",
            source="environment",
            terminal="Terminal.app",
            detail=(
                "macOS Terminal has no image support; install Ghostty, iTerm2 "
                "or WezTerm to see crops in the terminal"
            ),
        )

    return None


# --------------------------------------------------------------------------
# Terminal query
# --------------------------------------------------------------------------


def _query_terminal() -> GraphicsProtocol | None:
    """Ask the terminal, by reusing ``textual-image``'s import-time probe.

    ``textual_image.renderable`` sends the Sixel (``\\x1b[c``) and Kitty
    (``a=q``) capability queries when it is first imported and remembers the
    winner. Reading that result costs nothing and -- crucially -- avoids
    writing a *second* round of probes at the user's prompt, each of which can
    swallow a keystroke typed while it waits.
    """
    try:
        from textual_image.renderable import Image as auto_renderable
        from textual_image.renderable.sixel import Image as sixel_renderable
        from textual_image.renderable.tgp import Image as tgp_renderable
    except Exception:  # pragma: no cover - textual-image is a hard dependency
        return None

    if auto_renderable is sixel_renderable:
        return "sixel"
    if auto_renderable is tgp_renderable:
        return "kitty"
    return None


# --------------------------------------------------------------------------
# Public detection entry point
# --------------------------------------------------------------------------


def detect_graphics_capability(
    override: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    is_tty: bool | None = None,
    query: Callable[[], GraphicsProtocol | None] | None = None,
) -> GraphicsCapability:
    """Decide which graphics protocol to render answer crops with.

    Args:
        override: An explicit protocol name, or ``"auto"``/``None`` to detect.
            Falls back to the ``OPENGRADER_GRAPHICS`` environment variable.
        environ: Environment to fingerprint (defaults to ``os.environ``).
        is_tty: Whether stdout is a terminal (defaults to probing stdout).
        query: Terminal-query hook, injected by tests.

    Raises:
        GraphicsOverrideError: The override is not a known protocol.
    """
    environ = os.environ if environ is None else environ
    query = _query_terminal if query is None else query

    # An explicit "auto" (argparse's default for --graphics) means "no opinion"
    # and must not shadow the environment variable.
    explicit = (override or "").strip().lower()
    if explicit == "auto":
        explicit = ""
    requested = explicit or environ.get(ENV_OVERRIDE, "").strip().lower()
    if requested and requested != "auto":
        if requested not in _PROTOCOL_LABELS:
            raise GraphicsOverrideError(
                f"unknown graphics protocol {requested!r}; "
                f"choose from {', '.join(PROTOCOL_CHOICES)}"
            )
        return GraphicsCapability(
            requested,  # type: ignore[arg-type]
            source="override",
            detail=f"forced by {'--graphics' if explicit else ENV_OVERRIDE}",
        )

    if is_tty is None:
        stdout = sys.__stdout__
        is_tty = bool(stdout and stdout.isatty())

    if not is_tty:
        # Piped, redirected, or driven by the Textual test pilot: no terminal
        # to draw into, and no terminal to query either.
        return GraphicsCapability(
            "unicode",
            source="fallback",
            detail="output is not a terminal",
        )

    from_environment = _detect_from_environment(environ)
    if from_environment is not None:
        return from_environment

    queried = query()
    if queried is not None:
        return GraphicsCapability(
            queried,
            source="query",
            detail="reported by the terminal's capability query",
        )

    return GraphicsCapability(
        "halfcell",
        source="fallback",
        detail=(
            "this terminal reported no image support; crops render as coarse "
            "colour blocks"
        ),
    )


# --------------------------------------------------------------------------
# Process-wide selection
# --------------------------------------------------------------------------

_active: GraphicsCapability | None = None


def configure_graphics(override: str | None = None) -> GraphicsCapability:
    """Detect (or force) the protocol for this process and remember it.

    Called once by the CLI *before* the app starts, because probing the
    terminal is impossible once Textual owns stdin.
    """
    global _active
    _active = detect_graphics_capability(override)
    return _active


def active_graphics() -> GraphicsCapability:
    """The protocol in force, detecting lazily on first use."""
    if _active is None:
        return configure_graphics()
    return _active


def set_active_graphics(capability: GraphicsCapability | None) -> None:
    """Override or clear the process-wide capability (tests, previews)."""
    global _active
    _active = capability


def graphics_report(capability: GraphicsCapability | None = None) -> str:
    """Multi-line diagnostic used by ``tui --check-graphics``.

    This is the thing to ask an instructor to paste into a bug report: it says
    what we chose, why, and what to do if it is wrong.
    """
    from opengrader.tui.external_viewer import describe_external_viewer

    capability = capability or active_graphics()
    lines = [
        f"Terminal graphics : {capability.describe()}",
        f"Detected via      : {capability.source}",
        f"Answer crops      : "
        + (
            "rendered in the terminal"
            if capability.pixel_accurate
            else "NOT rendered in the terminal (low-fidelity preview only)"
        ),
    ]
    if capability.detail:
        lines.append(f"Note              : {capability.detail}")
    lines.append(f"External viewer   : {describe_external_viewer()}")
    lines.append("")
    lines.append(
        "Force a protocol with --graphics "
        f"{{{','.join(PROTOCOL_CHOICES)}}} or {ENV_OVERRIDE}=<protocol>."
    )
    if not capability.pixel_accurate:
        lines.append(
            "Press 'o' on the grade screen to open the full-resolution crop in "
            "your image viewer."
        )
    return "\n".join(lines)
