import json
from urllib.error import URLError

import pytest

from opengrader.core.llm import ollama_transport
from opengrader.core.llm.ollama_client import (
    OllamaConfig,
    OllamaError,
    generate,
    parse_json_object,
)


def test_parse_json_object_bare() -> None:
    assert parse_json_object('{"a": 1}') == {"a": 1}


def test_parse_json_object_fenced() -> None:
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_object_embedded_in_prose() -> None:
    assert parse_json_object('Here you go: {"a": 1} thanks') == {"a": 1}


def test_parse_json_object_returns_none_for_garbage() -> None:
    assert parse_json_object("no json here") is None
    assert parse_json_object("") is None


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def read(self) -> bytes:
        return self._raw


def _patch_response(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(
        ollama_transport.request,
        "urlopen",
        lambda req, timeout=None: _FakeResponse(payload),
    )


def test_generate_returns_response_text(monkeypatch) -> None:
    _patch_response(monkeypatch, {"response": "hello"})
    assert generate(prompt="hi", config=OllamaConfig()) == "hello"


def test_generate_raises_on_error_payload(monkeypatch) -> None:
    _patch_response(monkeypatch, {"error": "model 'x' not found"})
    with pytest.raises(OllamaError, match="model 'x' not found"):
        generate(prompt="hi", config=OllamaConfig())


def test_generate_raises_ollama_error_on_transport_failure(monkeypatch) -> None:
    def _raise(req, timeout=None):
        raise URLError("connection refused")

    monkeypatch.setattr(ollama_transport.request, "urlopen", _raise)
    with pytest.raises(OllamaError):
        generate(prompt="hi", config=OllamaConfig())


def _capture_request_body(monkeypatch, payload: dict) -> dict:
    """Patch urlopen to record the posted request body and return the payload."""
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse(payload)

    monkeypatch.setattr(ollama_transport.request, "urlopen", fake_urlopen)
    return captured


def test_generate_sends_keep_alive(monkeypatch) -> None:
    captured = _capture_request_body(monkeypatch, {"response": "ok"})
    generate(prompt="hi", config=OllamaConfig())
    assert captured["body"]["keep_alive"] == "10m"


def test_generate_sends_response_format_schema(monkeypatch) -> None:
    captured = _capture_request_body(monkeypatch, {"response": "ok"})
    schema = {"type": "object", "properties": {"value": {"type": "string"}}}
    generate(prompt="hi", config=OllamaConfig(), response_format=schema)
    assert captured["body"]["format"] == schema


def test_generate_sends_num_predict_in_options(monkeypatch) -> None:
    captured = _capture_request_body(monkeypatch, {"response": "ok"})
    generate(prompt="hi", config=OllamaConfig())
    # OllamaConfig's default num_predict (512, sized for the short grading
    # JSON reply) is threaded into options unless overridden per call.
    assert captured["body"]["options"]["num_predict"] == 512
