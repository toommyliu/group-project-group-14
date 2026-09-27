"""`opengrader data-flow`: the printable report says where names and IDs go,
including the `read_identity_locally` state (see core/app_config/consent.py's
`data_flow_report`, the single source for this wording)."""

from __future__ import annotations

import json

import pytest

from opengrader.cli import data_flow
from opengrader.core import app_config


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*_args, **_kwargs):
        raise AssertionError("data-flow must not touch the network")

    monkeypatch.setattr("urllib.request.urlopen", _refuse)


def test_default_settings_say_names_and_ids_use_a_local_model(
    capsys: pytest.CaptureFixture,
) -> None:
    data_flow.run_cli([])
    out = capsys.readouterr().out

    assert "identity-image" in out
    assert "Tesseract only" not in out
    assert "Processed on this computer" in out


def test_read_identity_locally_off_says_tesseract_only(
    capsys: pytest.CaptureFixture,
) -> None:
    app_config.save_settings(app_config.AISettings(read_identity_locally=False))

    data_flow.run_cli([])
    out = capsys.readouterr().out

    assert "Tesseract only" in out


def test_json_output_carries_the_identity_reason(capsys: pytest.CaptureFixture) -> None:
    app_config.save_settings(app_config.AISettings(read_identity_locally=False))

    data_flow.run_cli(["--json"])
    payload = json.loads(capsys.readouterr().out)
    by = {item["category"]: item for item in payload["items"]}

    assert "Tesseract only" in by["identity-image"]["reason"]
    assert by["identity-image"]["leaves_machine"] is False


def test_json_output_reflects_the_default_on_state(capsys: pytest.CaptureFixture) -> None:
    data_flow.run_cli(["--json"])
    payload = json.loads(capsys.readouterr().out)
    by = {item["category"]: item for item in payload["items"]}

    assert "Tesseract only" not in by["identity-image"]["reason"]
