import pytest

from opengrader.tui.terminal_graphics import (
    ENV_OVERRIDE,
    GraphicsCapability,
    GraphicsOverrideError,
    active_graphics,
    configure_graphics,
    detect_graphics_capability,
    graphics_report,
    set_active_graphics,
)


def _detect(environ: dict[str, str], *, queried=None, is_tty: bool = True):
    return detect_graphics_capability(
        environ=environ,
        is_tty=is_tty,
        query=lambda: queried,
    )


@pytest.mark.parametrize(
    ("environ", "protocol", "terminal"),
    [
        ({"TERM": "xterm-kitty"}, "kitty", "kitty"),
        ({"TERM": "xterm-256color", "KITTY_WINDOW_ID": "1"}, "kitty", "kitty"),
        ({"TERM_PROGRAM": "ghostty"}, "kitty", "Ghostty"),
        ({"TERM": "contour"}, "kitty", "Contour"),
        ({"TERM_PROGRAM": "WezTerm"}, "iterm2", "WezTerm"),
        ({"TERM_PROGRAM": "iTerm.app"}, "iterm2", "iTerm2"),
        ({"LC_TERMINAL": "iTerm2"}, "iterm2", "iTerm2"),
        ({"TERM_PROGRAM": "vscode"}, "iterm2", "VS Code"),
        ({"KONSOLE_VERSION": "230804"}, "sixel", "Konsole"),
        ({"TERM": "foot-extra"}, "sixel", "foot"),
        ({"TERM": "mlterm"}, "sixel", "mlterm"),
        ({"WT_SESSION": "abc"}, "sixel", "Windows Terminal"),
        ({"TERM_PROGRAM": "Apple_Terminal"}, "halfcell", "Terminal.app"),
    ],
)
def test_known_terminals_are_recognised_without_probing(
    environ, protocol, terminal
) -> None:
    def explode():
        raise AssertionError("terminal was probed despite a known environment")

    capability = detect_graphics_capability(
        environ=environ, is_tty=True, query=explode
    )

    assert capability.protocol == protocol
    assert capability.terminal == terminal
    assert capability.source == "environment"


def test_wezterm_prefers_iterm2_over_its_partial_kitty_support() -> None:
    capability = _detect({"TERM_PROGRAM": "WezTerm"}, queried="kitty")

    assert capability.protocol == "iterm2"


def test_multiplexer_downgrades_because_escapes_are_filtered() -> None:
    capability = _detect({"TMUX": "/tmp/tmux-501/default,1,0", "TERM": "screen"})

    assert capability.protocol == "halfcell"
    assert not capability.pixel_accurate
    assert "tmux" in capability.terminal


def test_pixel_accuracy_split() -> None:
    assert GraphicsCapability("kitty", source="x").pixel_accurate
    assert GraphicsCapability("iterm2", source="x").pixel_accurate
    assert GraphicsCapability("sixel", source="x").pixel_accurate
    assert not GraphicsCapability("halfcell", source="x").pixel_accurate
    assert not GraphicsCapability("unicode", source="x").pixel_accurate


def test_unknown_terminal_falls_back_to_the_capability_query() -> None:
    capability = _detect({"TERM": "xterm-256color"}, queried="sixel")

    assert capability.protocol == "sixel"
    assert capability.source == "query"


def test_silent_terminal_degrades_to_halfcell() -> None:
    capability = _detect({"TERM": "xterm-256color"}, queried=None)

    assert capability.protocol == "halfcell"
    assert capability.source == "fallback"
    assert not capability.pixel_accurate


def test_non_tty_never_probes_and_uses_unicode() -> None:
    def explode():
        raise AssertionError("probed a non-terminal")

    capability = detect_graphics_capability(
        environ={"TERM": "xterm-kitty"}, is_tty=False, query=explode
    )

    assert capability.protocol == "unicode"


def test_explicit_override_wins_over_everything() -> None:
    capability = detect_graphics_capability(
        "sixel", environ={"TERM": "xterm-kitty"}, is_tty=True
    )

    assert capability.protocol == "sixel"
    assert capability.source == "override"


def test_environment_override_is_honoured() -> None:
    capability = _detect({"TERM": "xterm-kitty", ENV_OVERRIDE: "iterm2"})

    assert capability.protocol == "iterm2"
    assert capability.source == "override"


def test_auto_override_means_detect() -> None:
    capability = _detect({"TERM": "xterm-kitty", ENV_OVERRIDE: "auto"})

    assert capability.protocol == "kitty"
    assert capability.source == "environment"


def test_unknown_override_is_rejected_with_the_valid_choices() -> None:
    with pytest.raises(GraphicsOverrideError) as excinfo:
        detect_graphics_capability("jpeg", environ={}, is_tty=True)

    assert "halfcell" in str(excinfo.value)


def test_configure_graphics_sets_the_process_wide_answer(monkeypatch) -> None:
    monkeypatch.setenv(ENV_OVERRIDE, "sixel")
    try:
        configured = configure_graphics()
        assert configured.protocol == "sixel"
        assert active_graphics() is configured
    finally:
        set_active_graphics(None)


def test_graphics_report_explains_the_degraded_case() -> None:
    report = graphics_report(
        GraphicsCapability(
            "halfcell",
            source="environment",
            terminal="Terminal.app",
            detail="macOS Terminal has no image support",
        )
    )

    assert "Terminal.app" in report
    assert "NOT rendered in the terminal" in report
    assert "--graphics" in report
    assert "'o'" in report


def test_graphics_report_is_quiet_about_the_escape_hatch_when_images_work() -> None:
    report = graphics_report(GraphicsCapability("kitty", source="environment"))

    assert "rendered in the terminal" in report
    assert "Press 'o'" not in report


def test_auto_from_the_cli_does_not_shadow_the_environment_variable() -> None:
    capability = detect_graphics_capability(
        "auto",
        environ={"TERM": "xterm-kitty", ENV_OVERRIDE: "sixel"},
        is_tty=True,
    )

    assert capability.protocol == "sixel"
    assert capability.source == "override"


def test_cli_override_beats_the_environment_variable() -> None:
    capability = detect_graphics_capability(
        "kitty",
        environ={ENV_OVERRIDE: "sixel"},
        is_tty=True,
    )

    assert capability.protocol == "kitty"
    assert "--graphics" in capability.detail
