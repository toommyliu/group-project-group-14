import os
from unittest.mock import patch

import pytest

from opengrader.tui.main import _enable_textual_feature, run_app, run_app_dev


def test_enable_textual_feature_merges_and_deduplicates(monkeypatch) -> None:
    monkeypatch.setenv("TEXTUAL", "debug,devtools")

    _enable_textual_feature("devtools")

    assert os.environ["TEXTUAL"] == "debug,devtools"


def test_run_app_launches_tui_app() -> None:
    with patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls:
        run_app()

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
