import os

from opengrader.cli.serve import _load_project_env


def test_load_project_env_reads_oauth_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENGRADER_GOOGLE_CLIENT_ID", raising=False)
    (tmp_path / ".env").write_text(
        'export OPENGRADER_GOOGLE_CLIENT_ID="client-from-file"\n',
        encoding="utf-8",
    )

    try:
        _load_project_env(tmp_path)

        assert os.environ["OPENGRADER_GOOGLE_CLIENT_ID"] == "client-from-file"
    finally:
        os.environ.pop("OPENGRADER_GOOGLE_CLIENT_ID", None)


def test_load_project_env_preserves_explicit_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENGRADER_GOOGLE_CLIENT_ID", "client-from-shell")
    (tmp_path / ".env").write_text(
        "OPENGRADER_GOOGLE_CLIENT_ID=client-from-file\n",
        encoding="utf-8",
    )

    _load_project_env(tmp_path)

    assert os.environ["OPENGRADER_GOOGLE_CLIENT_ID"] == "client-from-shell"
