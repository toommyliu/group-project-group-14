from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, get_args

GraphicsProtocol = Literal["kitty", "iterm2", "sixel", "halfcell", "unicode"]

PIXEL_PROTOCOLS: frozenset[str] = frozenset({"kitty", "iterm2", "sixel"})

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
    pass


@dataclass(frozen=True)
class GraphicsCapability:
    protocol: GraphicsProtocol
    source: str
    terminal: str = ""
    detail: str = ""

    @property
    def pixel_accurate(self) -> bool:
        return self.protocol in PIXEL_PROTOCOLS

    @property
    def label(self) -> str:
        return _PROTOCOL_LABELS.get(self.protocol, self.protocol)

    def describe(self) -> str:
        if self.terminal:
            return f"{self.label} ({self.terminal})"
        return self.label


def _term_program(environ: Mapping[str, str]) -> str:
    return environ.get("TERM_PROGRAM", "").strip().lower()


def _detect_from_environment(
    environ: Mapping[str, str],
) -> GraphicsCapability | None:
    term = environ.get("TERM", "").strip().lower()
    program = _term_program(environ)

    # Multiplexers may filter graphics escapes unless passthrough is configured.
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

    # WezTerm lacks the Kitty Unicode placeholders this renderer requires.
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


def _query_terminal() -> GraphicsProtocol | None:
    try:
        from textual_image.renderable import Image as auto_renderable
        from textual_image.renderable.sixel import Image as sixel_renderable
        from textual_image.renderable.tgp import Image as tgp_renderable
    except Exception:  # pragma: no cover
        return None

    if auto_renderable is sixel_renderable:
        return "sixel"
    if auto_renderable is tgp_renderable:
        return "kitty"
    return None


def detect_graphics_capability(
    override: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    is_tty: bool | None = None,
    query: Callable[[], GraphicsProtocol | None] | None = None,
) -> GraphicsCapability:
    environ = os.environ if environ is None else environ
    query = _query_terminal if query is None else query

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


_active: GraphicsCapability | None = None


def configure_graphics(override: str | None = None) -> GraphicsCapability:
    global _active
    _active = detect_graphics_capability(override)
    return _active


def active_graphics() -> GraphicsCapability:
    if _active is None:
        return configure_graphics()
    return _active


def set_active_graphics(capability: GraphicsCapability | None) -> None:
    global _active
    _active = capability


def graphics_report(capability: GraphicsCapability | None = None) -> str:
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
