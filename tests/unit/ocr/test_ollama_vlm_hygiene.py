from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from opengrader.core.ocr import ollama_vlm
from opengrader.core.ocr.ollama_vlm import (
    OllamaVisionConfig,
    OllamaVisionError,
    extract_field_text,
    extract_handwriting_only,
    extract_question_prompt_and_answer,
    extract_title_metadata,
    page_has_identity_field,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def read(self) -> bytes:
        return self._raw


@pytest.fixture(autouse=True)
def _skip_model_check(monkeypatch) -> None:
    monkeypatch.setattr(ollama_vlm, "ensure_model_available", lambda config: None)


@pytest.fixture
def image_path(tmp_path):
    path = tmp_path / "crop.png"
    path.write_bytes(b"not-really-a-png")
    return path


def _capture_request_body(monkeypatch, response_text: str) -> dict:
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"response": response_text})

    monkeypatch.setattr(ollama_vlm.ollama_transport.request, "urlopen", fake_urlopen)
    return captured


def test_extract_field_text_sends_schema_and_small_num_predict(monkeypatch, image_path):
    captured = _capture_request_body(monkeypatch, '{"value": "Ada Lovelace"}')
    extract_field_text(image_path=image_path, field_kind="name", config=OllamaVisionConfig())
    body = captured["body"]
    assert body["format"] == {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }
    assert body["options"]["num_predict"] == 128


def test_extract_title_metadata_sends_schema(monkeypatch, image_path):
    captured = _capture_request_body(
        monkeypatch,
        json.dumps(
            {
                "course_code": "CS101",
                "assignment_title": "HW1",
                "assignment_version": "A",
                "raw_title": "CS101 HW1 (A)",
            }
        ),
    )
    extract_title_metadata(image_path=image_path, config=OllamaVisionConfig())
    body = captured["body"]
    assert body["format"]["required"] == [
        "course_code",
        "assignment_title",
        "assignment_version",
        "raw_title",
    ]
    assert body["options"]["num_predict"] == 256


def test_extract_question_prompt_and_answer_uses_generous_transcription_cap(
    monkeypatch, image_path
):
    captured = _capture_request_body(
        monkeypatch, json.dumps({"prompt": "2+2?", "answer": "4"})
    )
    extract_question_prompt_and_answer(image_path=image_path, config=OllamaVisionConfig())
    body = captured["body"]
    assert body["format"] == {
        "type": "object",
        "properties": {"answer": {"type": "string"}, "prompt": {"type": "string"}},
        "required": ["answer", "prompt"],
    }
    assert list(body["format"]["properties"]) == ["answer", "prompt"]
    assert body["options"]["num_predict"] == 2048


def test_handwriting_only_prompt_excludes_printed_remnants(monkeypatch, image_path):
    captured = _capture_request_body(monkeypatch, '{"text": "oddly spaced word"}')

    assert (
        extract_handwriting_only(
            image_path=image_path,
            config=OllamaVisionConfig(),
        )
        == "oddly spaced word"
    )

    body = captured["body"]
    assert body["format"]["required"] == ["text"]
    assert "machine-printed" in body["prompt"]
    assert "Never answer or grade" in body["system"]
    assert body["think"] is False


def test_page_has_identity_field_sends_boolean_schema(monkeypatch, image_path):
    captured = _capture_request_body(monkeypatch, '{"first_page": true}')
    assert page_has_identity_field(image_path=image_path, config=OllamaVisionConfig()) is True
    body = captured["body"]
    assert body["format"] == {
        "type": "object",
        "properties": {"first_page": {"type": "boolean"}},
        "required": ["first_page"],
    }
    assert body["options"]["num_predict"] == 128


def test_generate_with_image_sends_keep_alive(monkeypatch, image_path):
    captured = _capture_request_body(monkeypatch, '{"value": ""}')
    extract_field_text(image_path=image_path, field_kind="sid", config=OllamaVisionConfig())
    assert captured["body"]["keep_alive"] == "10m"


def test_parse_json_from_response_still_salvages_fenced_json():
    result = ollama_vlm._parse_json_from_response('```json\n{"value": "x"}\n```')
    assert result == {"value": "x"}


def test_parse_json_from_response_still_salvages_prose_wrapped_json():
    result = ollama_vlm._parse_json_from_response('Sure, here it is: {"value": "x"} thanks')
    assert result == {"value": "x"}


def test_generate_with_image_raises_vision_error_on_transport_failure(
    monkeypatch, image_path
):
    def _raise(req, timeout=None):
        raise URLError("connection refused")

    monkeypatch.setattr(ollama_vlm.ollama_transport.request, "urlopen", _raise)
    with pytest.raises(OllamaVisionError):
        extract_field_text(image_path=image_path, field_kind="name", config=OllamaVisionConfig())


def test_generate_with_image_raises_vision_error_on_error_payload(monkeypatch, image_path):
    def fake_urlopen(req, timeout=None):
        return _FakeResponse({"error": "model not found"})

    monkeypatch.setattr(ollama_vlm.ollama_transport.request, "urlopen", fake_urlopen)
    with pytest.raises(OllamaVisionError, match="model not found"):
        extract_field_text(image_path=image_path, field_kind="name", config=OllamaVisionConfig())


def test_page_number_prompt_asks_for_empty_string_not_null(monkeypatch):
    prompts: list[str] = []

    def fake_generate(*, image_path, prompt, config, **_kwargs):
        prompts.append(prompt)
        return '{"value": ""}'

    monkeypatch.setattr(ollama_vlm, "_generate_with_image", fake_generate)
    result = ollama_vlm.extract_page_number(
        image_path="unused.png", config=OllamaVisionConfig()
    )
    assert result is None
    assert "null" not in prompts[0]
    assert "empty string" in prompts[0]
