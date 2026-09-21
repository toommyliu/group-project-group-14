"""`opengrader providers`: the bring-your-own-service CLI (§7.5).

Each test runs against the private `providers/` directory the root conftest
gives it, and `urlopen` is replaced wherever a call could go out. There is no
live service in these tests.
"""

from __future__ import annotations

import json

import pytest

from opengrader.cli import providers as cli
from opengrader.core import ai_config

FLAGS = [
    "add",
    "--name", "campus",
    "--label", "Campus Gateway",
    "--dialect", "openai-chat",
    "--host", "https://ai.example.edu/v1",
    "--vision-model", "v-model",
    "--text-model", "t-model",
    "--api-key-env", "CAMPUS_KEY",
]


@pytest.fixture(autouse=True)
def _clean_registry():
    ai_config.reload_manifests()
    try:
        yield
    finally:
        for path in ai_config.manifests_dir().glob("*.toml"):
            path.unlink()
        ai_config.reload_manifests()


@pytest.fixture
def no_network(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("the providers CLI opened a socket")

    monkeypatch.setattr("urllib.request.urlopen", _boom)


def _run(argv: list[str]) -> int:
    try:
        cli.run_cli(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def test_list_on_a_fresh_install_shows_only_local_ollama(capsys, no_network) -> None:
    assert _run(["list", "--json"]) == 0

    body = json.loads(capsys.readouterr().out)
    assert [p["name"] for p in body["providers"]] == ["ollama"]
    assert body["providers"][0]["kind"] == "local"
    assert body["problems"] == []
    assert set(body["examples"]) >= {"openai", "anthropic"}


def test_add_from_flags_registers_a_cloud_provider_and_says_so(capsys, no_network) -> None:
    assert _run(FLAGS) == 0

    out = capsys.readouterr().out
    spec = ai_config.get_provider("campus")
    assert spec.source == "user" and spec.kind == "cloud"
    assert "CLOUD" in out and "ai.example.edu" in out
    assert "CAMPUS_KEY" in out
    assert (ai_config.manifests_dir() / "campus.toml").is_file()


def test_add_refuses_to_overwrite_without_force(capsys, no_network) -> None:
    assert _run(FLAGS) == 0

    assert _run(FLAGS) == 1
    assert "already exists" in capsys.readouterr().err
    assert _run([*FLAGS, "--force"]) == 0


def test_add_example_copies_the_shipped_file(no_network) -> None:
    assert _run(["add", "--example", "anthropic"]) == 0

    spec = ai_config.get_provider("anthropic")
    assert spec.dialect == "anthropic-messages"
    written = (ai_config.manifests_dir() / "anthropic.toml").read_text(encoding="utf-8")
    assert written.startswith("# Example provider manifest")


def test_add_from_a_file_validates_it_first(tmp_path, capsys, no_network) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text(
        'name = "x"\nlabel = "X"\ndialect = "openai-chat"\n'
        'host = "https://x.example/v1"\nvision_model = "v"\ntext_model = "t"\n'
        'kind = "local"\n',
        encoding="utf-8",
    )

    assert _run(["add", "--from", str(bad)]) == 1
    assert "kind" in capsys.readouterr().err
    assert list(ai_config.manifests_dir().glob("*.toml")) == []


def test_add_needs_exactly_one_source(capsys) -> None:
    assert _run(["add"]) == 1
    assert _run(["add", "--example", "openai", "--name", "x"]) == 1


def test_validate_reports_each_file(capsys, no_network) -> None:
    directory = ai_config.manifests_dir()
    directory.mkdir(parents=True, exist_ok=True)
    assert _run(FLAGS) == 0
    (directory / "broken.toml").write_text("not = [valid", encoding="utf-8")
    capsys.readouterr()

    assert _run(["validate"]) == 2
    out = capsys.readouterr().out
    assert "OK" in out and "campus.toml" in out
    assert "FAIL" in out and "broken.toml" in out


def test_remove_deletes_only_what_the_user_added(capsys, no_network) -> None:
    assert _run(FLAGS) == 0

    assert _run(["remove", "ollama", "--yes"]) == 1
    assert _run(["remove", "campus", "--yes"]) == 0
    assert "campus" not in {s.name for s in ai_config.registered_providers()}
    assert not (ai_config.manifests_dir() / "campus.toml").exists()


def test_test_refuses_a_cloud_provider_without_confirmation(
    capsys, monkeypatch, no_network
) -> None:
    assert _run(FLAGS) == 0
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert _run(["test", "campus"]) == 2
    assert "--yes" in capsys.readouterr().err


def test_test_sends_one_fixed_prompt_and_never_prints_the_key(
    capsys, monkeypatch
) -> None:
    secret = "sk-cli-test-0123456789abcdef"
    assert _run(FLAGS) == 0
    monkeypatch.setenv("CAMPUS_KEY", secret)
    sent = []

    class _Response:
        status = 200

        def read(self) -> bytes:
            return json.dumps({"choices": [{"message": {"content": "ready"}}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

    def _urlopen(req, timeout=None):
        sent.append(req)
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    capsys.readouterr()

    assert _run(["test", "campus", "--yes"]) == 0

    captured = capsys.readouterr()
    assert "reachable" in captured.out
    assert secret not in captured.out + captured.err
    assert len(sent) == 1
    body = json.loads(sent[0].data)
    assert body["messages"][0]["content"] == cli.ai_manifests.CONNECTION_TEST_PROMPT


def test_a_failed_test_is_redacted_and_exits_2(capsys, monkeypatch) -> None:
    from io import BytesIO
    from urllib.error import HTTPError

    secret = "sk-cli-fail-0123456789abcdef"
    assert _run(FLAGS) == 0
    monkeypatch.setenv("CAMPUS_KEY", secret)

    def _refuse(req, timeout=None):
        raise HTTPError(
            req.full_url, 401, "Unauthorized", {},
            BytesIO(f'{{"error": "invalid key {secret}"}}'.encode()),
        )

    monkeypatch.setattr("urllib.request.urlopen", _refuse)
    capsys.readouterr()

    assert _run(["test", "campus", "--yes"]) == 2

    err = capsys.readouterr().err
    assert "401" in err
    assert secret not in err


def test_the_front_door_routes_to_this_command() -> None:
    from opengrader.cli.main import find

    assert find("providers").target == "opengrader.cli.providers:run_cli"
    assert find("opengrader-providers").name == "providers"
