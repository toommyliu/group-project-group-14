import os
from unittest.mock import patch

import pytest

from opengrader.tui.main import _enable_textual_feature, run_app, run_app_dev


def test_enable_textual_feature_merges_and_deduplicates(monkeypatch) -> None:
    monkeypatch.setenv("TEXTUAL", "debug,devtools")

    _enable_textual_feature("devtools")

    assert os.environ["TEXTUAL"] == "debug,devtools"


def test_run_app_launches_tui_app() -> None:
    # Pass an explicit empty argv; a bare run_app() would parse pytest's own
    # command line from sys.argv.
    with patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls:
        run_app([])

    app_cls.assert_called_once_with()
    app_cls.return_value.run.assert_called_once_with()


def test_run_app_passes_file_backed_paths() -> None:
    with patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls:
        run_app(["--qa-file", "qa.md", "--submissions", "submissions"])

    app_cls.assert_called_once_with(
        qa_file_path="qa.md",
        submissions_path="submissions",
    )
    app_cls.return_value.run.assert_called_once_with()


def test_run_app_requires_qa_and_submissions_together() -> None:
    with pytest.raises(SystemExit):
        run_app(["--qa-file", "qa.md"])


def test_run_app_dev_enables_devtools_before_launch(monkeypatch) -> None:
    monkeypatch.delenv("TEXTUAL", raising=False)

    with patch("opengrader.tui.main.run_app") as run_app_mock:
        run_app_dev()

    assert os.environ["TEXTUAL"] == "devtools"
    run_app_mock.assert_called_once()


def test_check_graphics_reports_and_exits_without_launching(monkeypatch) -> None:
    """The support command: an instructor runs it and pastes the answer."""
    monkeypatch.setenv("OPENGRADER_GRAPHICS", "sixel")

    with patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls:
        with pytest.raises(SystemExit) as excinfo:
            run_app(["--check-graphics"])

    assert excinfo.value.code == 0
    app_cls.assert_not_called()


def test_graphics_override_is_applied_before_the_app_starts(monkeypatch) -> None:
    """Probing the terminal is impossible once Textual owns stdin, so the
    protocol must be settled before TuiApp is constructed."""
    from opengrader.tui import terminal_graphics

    monkeypatch.delenv("OPENGRADER_GRAPHICS", raising=False)
    terminal_graphics.set_active_graphics(None)
    seen: list[str] = []

    class RecordingApp:
        def __init__(self, **kwargs) -> None:
            seen.append(terminal_graphics.active_graphics().protocol)

        def run(self) -> None:
            pass

    monkeypatch.setattr("opengrader.tui.main.TuiApp", RecordingApp)
    try:
        run_app(["--graphics", "iterm2"])
    finally:
        terminal_graphics.set_active_graphics(None)

    assert seen == ["iterm2"]


def test_unknown_graphics_protocol_is_rejected() -> None:
    # argparse rejects it before our own validation ever runs.
    with pytest.raises(SystemExit):
        run_app(["--graphics", "jpeg"])
