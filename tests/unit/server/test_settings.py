from pathlib import Path

import pytest
from pydantic import ValidationError

from opengrader.server.settings import ServerSettings


def test_dotenv_loads_typed_server_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        'export OPENGRADER_GOOGLE_CLIENT_ID="client-from-file"\n'
        'OPENGRADER_GOOGLE_CLIENT_SECRET=private-client-secret\n'
        'OPENGRADER_SESSION_SECRET=private-session-secret\n'
        'OPENGRADER_AUTH_ENABLED=true\n'
        'OPENGRADER_SECURE_COOKIE=yes\n'
        'OPENGRADER_GOOGLE_DOMAIN=SJSU.EDU\n'
        'OPENGRADER_ADMINS= Prof@SJSU.edu, co.instructor@sjsu.edu ,\n'
        'UNRELATED_SETTING=value\n',
        encoding="utf-8",
    )
    settings = ServerSettings()
    assert settings.google_client_id == "client-from-file"
    assert settings.google_client_secret.get_secret_value() == "private-client-secret"
    assert settings.auth_enabled is True
    assert settings.secure_cookie is True
    assert settings.google_domain == "sjsu.edu"
    assert settings.admins == {"prof@sjsu.edu", "co.instructor@sjsu.edu"}
    assert "private-client-secret" not in repr(settings)
    assert "private-session-secret" not in repr(settings)


def test_shell_environment_overrides_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENGRADER_GOOGLE_CLIENT_ID", "client-from-shell")
    (tmp_path / ".env").write_text(
        "OPENGRADER_GOOGLE_CLIENT_ID=client-from-file\n", encoding="utf-8"
    )
    assert ServerSettings().google_client_id == "client-from-shell"


def test_enabled_auth_requires_credentials():
    with pytest.raises(ValidationError, match="Google sign-in requires"):
        ServerSettings(_env_file=None, auth_enabled=True, google_client_id="client-id")


def test_admins_must_be_on_the_domain():
    with pytest.raises(ValidationError, match="prof@gmail.com"):
        ServerSettings(
            _env_file=None,
            auth_enabled=True,
            google_client_id="client-id",
            google_client_secret="client-secret",
            session_secret="session-secret",
            admins="prof@sjsu.edu,prof@gmail.com",
        )


def test_example_requires_a_generated_session_secret():
    example = Path(__file__).resolve().parents[3] / ".env.example"
    with pytest.raises(ValidationError, match="Google sign-in requires"):
        ServerSettings(
            _env_file=example,
            auth_enabled=True,
            google_client_id="client-id",
            google_client_secret="client-secret",
        )


def test_invalid_boolean_is_reported_without_exposing_credentials(monkeypatch):
    monkeypatch.setenv("OPENGRADER_AUTH_ENABLED", "typo")
    monkeypatch.setenv("OPENGRADER_GOOGLE_CLIENT_SECRET", "private-client-secret")
    with pytest.raises(ValidationError) as error:
        ServerSettings(_env_file=None)
    assert "auth_enabled" in str(error.value)
    assert "private-client-secret" not in str(error.value)
