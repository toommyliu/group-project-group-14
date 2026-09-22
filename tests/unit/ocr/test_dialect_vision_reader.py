from __future__ import annotations

import base64
import json
import pickle
from pathlib import Path

import pytest
from PIL import Image

from opengrader.core.llm.dialect_client import DialectConfig
from opengrader.core.ocr.vision_reader import (
    DialectVisionReader,
    TitleMetadataResult,
)

_KEY = "sk-conformance-test-key-0123456789"


class _Response:
    def __init__(self, body: dict) -> None:
        self.status = 200
        self._raw = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc) -> None:
        return None


def _wrap(dialect: str, reply: str) -> dict:
    if dialect == "openai-chat":
        return {"choices": [{"message": {"role": "assistant", "content": reply}}]}
    return {"content": [{"type": "text", "text": reply}]}


@pytest.fixture
def wire(monkeypatch):
    """A fake `urlopen` that records every request and replays one reply."""

    state: dict = {"requests": [], "reply": "{}", "dialect": "openai-chat"}

    def _urlopen(req, timeout=None):
        state["requests"].append(req)
        return _Response(_wrap(state["dialect"], state["reply"]))

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    return state


@pytest.fixture
def crop(tmp_path: Path) -> Path:
    path = tmp_path / "crop.png"
    Image.new("RGB", (24, 12), color="white").save(path)
    return path


def _reader(dialect: str) -> DialectVisionReader:
    host = (
        "https://api.acme.example/v1"
        if dialect == "openai-chat"
        else "https://api.anthropic.example/v1"
    )
    return DialectVisionReader(
        config=DialectConfig(
            dialect=dialect,
            host=host,
            model="vision-model-1",
            api_key=_KEY,
            label="Acme",
            max_attempts=1,
        )
    )


DIALECTS = ("openai-chat", "anthropic-messages")

# (operation, kwargs, what the model says, what the reader must return)
CASES = [
    (
        "extract_field_text",
        {"field_kind": "name"},
        '{"value": "Name: Ada  Lovelace"}',
        "Ada Lovelace",
    ),
    (
        "extract_field_text",
        {"field_kind": "sid"},
        '{"value": "12-345-6789"}',
        "123456789",
    ),
    (
        "extract_title_metadata",
        {},
        json.dumps(
            {
                "course_code": "CMPE30",
                "assignment_title": "Quiz 2",
                "assignment_version": "Version a",
                "raw_title": "CMPE30 Quiz 2 (a)",
            }
        ),
        TitleMetadataResult(
            course_code="CMPE30",
            assignment_title="Quiz 2",
            assignment_version="A",
            raw_title="CMPE30 Quiz 2 (a)",
        ),
    ),
    (
        "extract_question_prompt_and_answer",
        {},
        '```json\n{"prompt": "What is  a stack?", "answer": "LIFO"}\n```',
        ("What is a stack?", "LIFO"),
    ),
    (
        "extract_handwriting_only",
        {},
        '{"text": "  x = 4  "}',
        "x = 4",
    ),
    ("extract_page_number", {}, '{"value": "3"}', 3),
    ("page_has_identity_field", {}, '{"first_page": true}', True),
]


@pytest.mark.parametrize("dialect", DIALECTS)
def test_dialect_reader_is_picklable_and_keeps_its_key_out_of_repr(
    dialect: str,
) -> None:
    """`batch.ExtractionJob` carries a reader into a `spawn`ed worker."""
    reader = _reader(dialect)

    clone = pickle.loads(pickle.dumps(reader))

    assert clone.config.api_key == _KEY
    assert clone.config.dialect == dialect
    assert _KEY not in repr(reader)


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize(
    ("operation", "kwargs", "reply", "expected"),
    CASES,
    ids=[f"{case[0]}-{i}" for i, case in enumerate(CASES)],
)
def test_every_operation_round_trips_through_the_dialect(
    wire, crop, dialect, operation, kwargs, reply, expected
) -> None:
    wire["dialect"] = dialect
    wire["reply"] = reply

    result = getattr(_reader(dialect), operation)(image_path=crop, **kwargs)

    assert result == expected
    assert len(wire["requests"]) == 1
    req = wire["requests"][0]
    body = json.loads(req.data)
    encoded = base64.b64encode(crop.read_bytes()).decode("ascii")
    content = body["messages"][0]["content"]
    if dialect == "openai-chat":
        assert req.full_url == "https://api.acme.example/v1/chat/completions"
        assert req.get_header("Authorization") == f"Bearer {_KEY}"
        assert "response_format" in body
        image_parts = [p for p in content if p["type"] == "image_url"]
        assert image_parts[0]["image_url"]["url"] == f"data:image/png;base64,{encoded}"
    else:
        assert req.full_url == "https://api.anthropic.example/v1/messages"
        assert req.get_header("X-api-key") == _KEY
        image_parts = [p for p in content if p["type"] == "image"]
        assert image_parts[0]["source"]["data"] == encoded
        assert image_parts[0]["source"]["media_type"] == "image/png"
    assert body["model"] == "vision-model-1"


def test_math_latex_changes_the_prompt_the_dialect_sends(wire, crop) -> None:
    reader = _reader("openai-chat")
    wire["reply"] = '{"prompt": "", "answer": "$x^2$"}'

    reader.extract_question_prompt_and_answer(image_path=crop, math_latex=False)
    reader.extract_question_prompt_and_answer(image_path=crop, math_latex=True)

    plain, latex = (
        json.loads(r.data)["messages"][0]["content"][0]["text"]
        for r in wire["requests"]
    )
    assert "LaTeX" not in plain
    assert "LaTeX" in latex


def test_a_wire_failure_surfaces_as_an_llm_error_with_the_key_redacted(
    monkeypatch, crop
) -> None:
    """Callers above the seam catch `LLMError` and fall through to Tesseract."""
    from io import BytesIO
    from urllib.error import HTTPError

    from opengrader.core.llm import LLMError

    def _refuse(req, timeout=None):
        raise HTTPError(
            req.full_url,
            401,
            "Unauthorized",
            {},
            BytesIO(f'{{"error": "bad key {_KEY}"}}'.encode()),
        )

    monkeypatch.setattr("urllib.request.urlopen", _refuse)

    with pytest.raises(LLMError) as caught:
        _reader("openai-chat").extract_handwriting_only(image_path=crop)

    assert "401" in str(caught.value)
    assert _KEY not in str(caught.value)
