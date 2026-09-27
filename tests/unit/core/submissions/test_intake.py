"""SubmissionIntake (used by the web server/TUI review flow) must never let a
missing cloud consent escape as a bare traceback: app_config.vision_reader()
raises CloudConsentRequired/CloudDisabledError before app_config.ensure_ready()
even runs, and __init__ used to call vision_reader() outside its try/except.

No network anywhere: app_config.vision_reader is monkeypatched directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opengrader.core import app_config
from opengrader.core.submissions.intake import SubmissionIntake

QA_MD = """\
---
title: |
  Test Exam
instructions: Answer all questions.
questions_start_page: 1
---

- (10 points) What is 2 + 2?
  - **Correct**
    - 4
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
"""


def _qa_file(tmp_path: Path) -> Path:
    path = tmp_path / "qa.md"
    path.write_text(QA_MD)
    return path


def _raise_consent_required(*_args, **_kwargs):
    raise app_config.CloudConsentRequired("not consented for this test")


def test_missing_consent_raises_value_error_when_handwriting_is_on(
    tmp_path: Path, monkeypatch
) -> None:
    app_config.save_settings(app_config.AISettings(read_handwriting=True))
    monkeypatch.setattr(app_config, "vision_reader", _raise_consent_required)

    with pytest.raises(ValueError) as excinfo:
        SubmissionIntake(_qa_file(tmp_path), auto_grade=False, assignment_version=None)

    message = str(excinfo.value)
    assert "opengrader providers consent" in message


def test_missing_consent_raises_value_error_when_escalate_only_is_on(
    tmp_path: Path, monkeypatch
) -> None:
    app_config.save_settings(
        app_config.AISettings(read_handwriting=True, escalate_only=True)
    )
    monkeypatch.setattr(app_config, "vision_reader", _raise_consent_required)

    with pytest.raises(ValueError):
        SubmissionIntake(_qa_file(tmp_path), auto_grade=False, assignment_version=None)


def test_missing_consent_falls_back_silently_when_handwriting_is_off(
    tmp_path: Path, monkeypatch
) -> None:
    app_config.save_settings(
        app_config.AISettings(read_handwriting=False, escalate_only=False)
    )
    monkeypatch.setattr(app_config, "vision_reader", _raise_consent_required)

    intake = SubmissionIntake(_qa_file(tmp_path), auto_grade=False, assignment_version=None)

    assert intake.vision is None


def test_a_consented_cloud_provider_gets_no_reader_with_handwriting_reading_off(
    tmp_path: Path, monkeypatch
) -> None:
    """Extraction without a template hands every answer crop to the reader it
    is given, so a cloud reader here would send answers the settings say are
    not sent."""
    from opengrader.core.app_config import consent

    spec = app_config.ProviderSpec(
        name="intake-cloud",
        label="Intake Cloud",
        summary="",
        default_host="https://api.intake-cloud.example/v1",
        default_vision_model="v",
        default_text_model="t",
        setup_hint="",
        supports_model_listing=False,
        dialect="openai-chat",
    )
    app_config.register_provider(spec)
    try:
        settings = app_config.AISettings(provider=spec.name)
        settings = consent.grant_cloud_consent(settings, app_config.resolve(settings))
        app_config.save_settings(settings)
        assert app_config.resolve().read_handwriting is False
        built: list = []
        monkeypatch.setattr(
            app_config, "vision_reader", lambda *a, **k: built.append(a) or object()
        )

        intake = SubmissionIntake(_qa_file(tmp_path), auto_grade=False, assignment_version=None)

        assert intake.vision is None
        assert built == []
    finally:
        app_config._REGISTRY.pop(spec.name, None)
