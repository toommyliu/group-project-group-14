import json
from base64 import b64encode

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from opengrader.server.auth import AuthSettings, user_from_claims
from opengrader.server.workspace.app import create_app


def test_auth_is_optional_for_offline_use(tmp_path):
    with TestClient(
        create_app(tmp_path, auth_settings=AuthSettings(), process_jobs=False)
    ) as client:
        assert client.get("/auth/me").json() == {"enabled": False, "user": None}
        response = client.get("/auth/login")
        assert response.status_code == 503


def test_partial_auth_configuration_is_rejected():
    try:
        AuthSettings.from_env({"OPENGRADER_GOOGLE_CLIENT_ID": "client-id"})
    except ValueError as exc:
        assert "requires" in str(exc)
    else:
        raise AssertionError("partial OAuth configuration should fail")


def test_session_reports_user_and_logout_clears_it(tmp_path):
    settings = AuthSettings(
        client_id="client-id",
        client_secret="client-secret",
        session_secret="test-session-secret",
    )
    session = {
        "user": {
            "id": "google-subject",
            "email": "grader@sjsu.edu",
            "name": "Test Grader",
        }
    }
    cookie = TimestampSigner(settings.session_secret).sign(
        b64encode(json.dumps(session).encode("utf-8"))
    ).decode("utf-8")

    with TestClient(
        create_app(tmp_path, auth_settings=settings, process_jobs=False)
    ) as client:
        client.cookies.set("opengrader_session", cookie)
        assert client.get("/auth/me").json()["user"] == {
            "id": "google-subject",
            "email": "grader@sjsu.edu",
            "name": "Test Grader",
        }
        response = client.post("/auth/logout", follow_redirects=False)
        assert response.status_code == 303
        assert 'opengrader_session=null' in response.headers["set-cookie"]


def test_google_claims_must_be_verified_and_from_sjsu():
    claims = {
        "sub": "google-subject",
        "email": "Grader@SJSU.edu",
        "email_verified": True,
        "hd": "sjsu.edu",
        "name": "Test Grader",
    }
    assert user_from_claims(claims, "sjsu.edu").email == "grader@sjsu.edu"

    for invalid in (
        {**claims, "hd": "gmail.com"},
        {**claims, "email_verified": False},
        {**claims, "sub": ""},
    ):
        try:
            user_from_claims(invalid, "sjsu.edu")
        except ValueError:
            pass
        else:
            raise AssertionError("invalid Google claims should be rejected")
